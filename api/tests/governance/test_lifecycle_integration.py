from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]
import pytest
from alembic import command as alembic_command
from opennosh_api.governance.contracts import (
    PROTECTED_STATUS_CHECKS,
    ApprovedChangeSet,
    ApprovedFileChange,
    GovernanceDecisionOutcome,
)
from opennosh_api.governance.gate import PostgresGovernanceGate
from opennosh_api.governance.models import GovernanceRoleAssignment
from opennosh_api.governance.service import (
    ApproveContribution,
    GovernanceDecisionError,
    approve_contribution,
    intervene_publication,
    pause_publication,
    recuse_steward,
    resume_publication,
    revoke_steward,
)
from opennosh_api.jobs.pgqueuer import PgQueuerJobQueue
from opennosh_api.jobs.worker import asyncpg_dsn
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.tests.test_migrations import migration_config

INTEGRATION_DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL")
NOW = datetime(2026, 8, 26, 12, tzinfo=UTC)


async def run_lifecycle(database_url: str) -> None:
    contributor = uuid4()
    steward = uuid4()
    second_steward = uuid4()
    draft_id = uuid4()
    authorized_draft_id = uuid4()
    pack_id = f"test-{uuid4().hex}"
    connection = await asyncpg.connect(asyncpg_dsn(database_url))
    try:
        await connection.execute(
            "INSERT INTO users (id, email, password_hash) VALUES "
            "($1, $2, 'hash'), ($3, $4, 'hash'), ($5, $6, 'hash')",
            contributor,
            f"{contributor}@example.test",
            steward,
            f"{steward}@example.test",
            second_steward,
            f"{second_steward}@example.test",
        )
        await connection.execute(
            "INSERT INTO contribution_drafts "
            "(id, user_id, client_draft_id, review_state, fields_json) "
            "VALUES ($1, $2, $3, 'in_review', jsonb_build_object('pack_id', $4::text)), "
            "($5, $2, $6, 'in_review', jsonb_build_object('pack_id', $4::text))",
            draft_id,
            contributor,
            f"lifecycle-{draft_id}",
            pack_id,
            authorized_draft_id,
            f"lifecycle-{authorized_draft_id}",
        )
        await connection.execute(
            "INSERT INTO governance_role_assignments "
            "(pack_id, actor_id, role, granted_by_actor_id, grant_reason, granted_at) "
            "VALUES ($1, $2, 'steward', $2, 'test grant', $4), "
            "($1, $3, 'steward', $2, 'test grant', $4)",
            pack_id,
            steward,
            second_steward,
            NOW,
        )
    finally:
        await connection.close()

    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    queue = PgQueuerJobQueue(clock=lambda: NOW)
    command = ApproveContribution(
        source_draft_id=draft_id,
        deciding_actor_id=steward,
        approved_changes=ApprovedChangeSet.build(
            pack_id=pack_id,
            files=(
                ApprovedFileChange(
                    path=f"packs/{pack_id}/foods/lentils.json",
                    content='{"name":"Lentils"}\n',
                ),
            ),
        ),
        record_id="lentils",
        expected_base_commit="a" * 40,
        required_checks=PROTECTED_STATUS_CHECKS,
        forge_target="github:RujitRaval/opennosh",
        reason="Lifecycle approval",
    )
    try:
        async with sessions() as session:
            async with session.begin():
                decision, intent = await approve_contribution(
                    session, queue, command, now=NOW
                )
                session.add(
                    recuse_steward(
                        pack_id=pack_id,
                        source_draft_id=draft_id,
                        actor_id=second_steward,
                        reason="Conflict disclosed",
                        now=NOW,
                    )
                )
            publication_id = intent.id

        pool = await asyncpg.create_pool(asyncpg_dsn(database_url), min_size=1, max_size=1)
        assert pool is not None
        try:
            before = await PostgresGovernanceGate(pool).binding_for(publication_id)
            assert before.intervention_action is None
            assert before.required_checks == PROTECTED_STATUS_CHECKS
        finally:
            await pool.close()

        async with sessions() as session:
            async with session.begin():
                with pytest.raises(GovernanceDecisionError, match="steward_recused"):
                    await intervene_publication(
                        session,
                        publication_id,
                        actor_id=second_steward,
                        action=GovernanceDecisionOutcome.REJECTED,
                        reason="Denied after recusal",
                        now=NOW,
                    )

        async with sessions() as session:
            async with session.begin():
                intervention = await intervene_publication(
                    session,
                    publication_id,
                    actor_id=steward,
                    action=GovernanceDecisionOutcome.CHANGES_REQUESTED,
                    reason="Correct the source attribution",
                    now=NOW,
                )
                assert intervention.action == "changes_requested"

        async with sessions() as session:
            async with session.begin():
                _, authorized_intent = await approve_contribution(
                    session,
                    queue,
                    replace(
                        command,
                        source_draft_id=authorized_draft_id,
                        record_id="authorized-lentils",
                    ),
                    now=NOW,
                )

        pool = await asyncpg.create_pool(asyncpg_dsn(database_url), min_size=1, max_size=1)
        assert pool is not None
        try:
            authorized = await PostgresGovernanceGate(pool).authorize_merge(
                authorized_intent.id,
                head_commit="d" * 40,
                expected_payload_digest=command.approved_changes.digest,
                now=NOW,
            )
            assert authorized.merge_authorized_at == NOW
            recovered = await PostgresGovernanceGate(pool).authorize_merge(
                authorized_intent.id,
                head_commit="d" * 40,
                expected_payload_digest=command.approved_changes.digest,
                now=NOW,
            )
            assert recovered.merge_authorized_head_commit == "d" * 40
        finally:
            await pool.close()

        async with sessions() as session:
            async with session.begin():
                with pytest.raises(
                    GovernanceDecisionError,
                    match="merge_authorization_committed",
                ):
                    await intervene_publication(
                        session,
                        authorized_intent.id,
                        actor_id=steward,
                        action=GovernanceDecisionOutcome.REJECTED,
                        reason="Too late to cancel committed merge authority",
                        now=NOW,
                    )

        delete_connection = await asyncpg.connect(asyncpg_dsn(database_url))
        try:
            with pytest.raises(
                asyncpg.CheckViolationError,
                match="governance_audit_rows_are_immutable",
            ):
                await delete_connection.execute(
                    "DELETE FROM governance_role_assignments "
                    "WHERE pack_id = $1 AND actor_id = $2",
                    pack_id,
                    steward,
                )
            immutable_updates = (
                (
                    "UPDATE governance_decisions SET reason = 'rewritten' WHERE id = $1",
                    decision.id,
                ),
                (
                    "UPDATE governance_recusals SET reason = 'rewritten' "
                    "WHERE source_draft_id = $1 AND actor_id = $2",
                    draft_id,
                    second_steward,
                ),
                (
                    "UPDATE governance_publication_interventions "
                    "SET reason = 'rewritten' WHERE id = $1",
                    intervention.id,
                ),
                (
                    "UPDATE governance_merge_authorizations "
                    "SET head_commit = $2 WHERE publication_intent_id = $1",
                    authorized_intent.id,
                    "e" * 40,
                ),
            )
            for statement in immutable_updates:
                with pytest.raises(
                    asyncpg.CheckViolationError,
                    match="governance_audit_rows_are_append_only",
                ):
                    await delete_connection.execute(statement[0], *statement[1:])
            with pytest.raises(
                asyncpg.CheckViolationError,
                match="governance_role_update_must_be_one_way_revocation",
            ):
                await delete_connection.execute(
                    "UPDATE governance_role_assignments SET pack_id = $3 "
                    "WHERE pack_id = $1 AND actor_id = $2",
                    pack_id,
                    steward,
                    f"rewritten-{pack_id}",
                )
        finally:
            await delete_connection.close()

        pool = await asyncpg.create_pool(asyncpg_dsn(database_url), min_size=1, max_size=1)
        assert pool is not None
        try:
            after = await PostgresGovernanceGate(pool).binding_for(publication_id)
            assert after.intervention_action == "changes_requested"
            assert after.intervened_at == NOW
        finally:
            await pool.close()

        async def pause(actor_id):  # type: ignore[no-untyped-def]
            try:
                async with sessions() as session:
                    async with session.begin():
                        return await pause_publication(
                            session,
                            pack_id=pack_id,
                            paused_by_actor_id=actor_id,
                            reason="Investigating forge credentials",
                            now=NOW,
                        )
            except GovernanceDecisionError as error:
                return error

        pauses = await asyncio.gather(pause(steward), pause(second_steward))
        pause_errors = [item for item in pauses if isinstance(item, GovernanceDecisionError)]
        assert [error.code for error in pause_errors] == ["publication_already_paused"]
        active_pause = next(
            item for item in pauses if not isinstance(item, GovernanceDecisionError)
        )
        resumer = (
            second_steward
            if active_pause.paused_by_actor_id == steward
            else steward
        )

        async with sessions() as session:
            async with session.begin():
                with pytest.raises(
                    GovernanceDecisionError,
                    match="publication_resume_requires_second_steward",
                ):
                    await resume_publication(
                        session,
                        active_pause.id,
                        resumed_by_actor_id=active_pause.paused_by_actor_id,
                        reason="Invalid one-person resume",
                        now=NOW,
                    )
                resumed = await resume_publication(
                    session,
                    active_pause.id,
                    resumed_by_actor_id=resumer,
                    reason="Credentials independently verified",
                    now=NOW,
                )
                assert resumed.resumed_by_actor_id == resumer

        pause_update_connection = await asyncpg.connect(asyncpg_dsn(database_url))
        try:
            with pytest.raises(
                asyncpg.CheckViolationError,
                match="governance_pause_update_must_be_one_way_resume",
            ):
                await pause_update_connection.execute(
                    "UPDATE governance_publication_pauses "
                    "SET pause_reason = 'rewritten' WHERE id = $1",
                    active_pause.id,
                )
        finally:
            await pause_update_connection.close()

        async with sessions() as session:
            role_id = await session.scalar(
                select(GovernanceRoleAssignment.id).where(
                    GovernanceRoleAssignment.pack_id == pack_id,
                    GovernanceRoleAssignment.actor_id == second_steward,
                )
            )
            assert role_id is not None

        async def revoke(reason: str) -> object:
            try:
                async with sessions() as session:
                    async with session.begin():
                        return await revoke_steward(
                            session,
                            role_id,
                            revoked_by_actor_id=steward,
                            reason=reason,
                            now=NOW,
                        )
            except GovernanceDecisionError as error:
                return error

        revocations = await asyncio.gather(revoke("Rotation A"), revoke("Rotation B"))
        assert len([item for item in revocations if isinstance(item, GovernanceDecisionError)]) == 1

        async with engine.connect() as database:
            row = (
                await database.execute(
                    text(
                        "SELECT p.state, p.last_failure_code, d.review_state "
                        "FROM publication_intents p JOIN contribution_drafts d "
                        "ON d.id = p.source_draft_id WHERE p.id = :publication_id"
                    ),
                    {"publication_id": publication_id},
                )
            ).one()
            assert row == (
                "publish_blocked",
                "governance_changes_requested",
                "changes_requested",
            )
    finally:
        await engine.dispose()


@pytest.mark.skipif(
    INTEGRATION_DATABASE_URL is None,
    reason="INTEGRATION_DATABASE_URL is required for PostgreSQL integration tests",
)
def test_persisted_gate_intervention_pause_and_concurrent_revocation() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    alembic_command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(run_lifecycle(INTEGRATION_DATABASE_URL))
