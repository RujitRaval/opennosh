from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg  # type: ignore[import-untyped]
import pytest
from alembic import command as alembic_command
from opennosh_api.evidence.contracts import EvidenceAcknowledgement
from opennosh_api.evidence.repository import tombstone_evidence
from opennosh_api.governance.contracts import (
    PROTECTED_STATUS_CHECKS,
    ApprovedChangeSet,
    ApprovedFileChange,
    GovernanceDecisionOutcome,
)
from opennosh_api.governance.service import (
    ApproveContribution,
    GovernanceDecisionError,
    ResubmitPublication,
    approve_contribution,
    intervene_publication,
    resubmit_publication,
)
from opennosh_api.jobs.pgqueuer import PGQUEUER_SETTINGS, PgQueuerJobQueue
from opennosh_api.jobs.worker import (
    PublicationActivationWakeupOutcome,
    asyncpg_dsn,
    ensure_publication_activation_wakeup,
)
from opennosh_api.publication.receipts import ReceiptEventType
from opennosh_api.publication.service import (
    CreatePublicationIntent,
    create_publication_intent,
)
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.tests.evidence.factories import seed_verified_reference_evidence
from api.tests.test_migrations import migration_config

INTEGRATION_DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL")
NOW = datetime(2026, 8, 29, 1, tzinfo=UTC)
CONTRIBUTOR = UUID("11111111-1111-4111-8111-111111111111")
STEWARD = UUID("22222222-2222-4222-8222-222222222222")
DRAFT = UUID("33333333-3333-4333-8333-333333333333")
SECOND_DRAFT = UUID("44444444-4444-4444-8444-444444444444")
THIRD_DRAFT = UUID("55555555-5555-4555-8555-555555555555")
FOURTH_DRAFT = UUID("66666666-6666-4666-8666-666666666666")
FIFTH_DRAFT = UUID("77777777-7777-4777-8777-777777777777")


def _approval(
    *,
    draft_id: UUID = DRAFT,
    record_id: str = "lentils",
    path: str = "packs/global-core/foods/lentils.json",
) -> ApproveContribution:
    return ApproveContribution(
        source_draft_id=draft_id,
        deciding_actor_id=STEWARD,
        approved_changes=ApprovedChangeSet.build(
            pack_id="global-core",
            files=(
                ApprovedFileChange(
                    path=path,
                    content='{"name":"Lentils"}\n',
                ),
            ),
        ),
        record_id=record_id,
        expected_base_commit="a" * 40,
        required_checks=PROTECTED_STATUS_CHECKS,
        forge_target="github:RujitRaval/opennosh",
        reason="Initial governed approval.",
    )


async def _run_terminal_resubmission(database_url: str) -> None:
    connection = await asyncpg.connect(asyncpg_dsn(database_url))
    try:
        await connection.execute(
            "TRUNCATE governance_decisions, governance_recusals, "
            "governance_publication_pauses, governance_role_assignments, "
            "publication_steps, publication_intents, opennosh_pgqueuer, "
            "opennosh_pgqueuer_log, opennosh_pgqueuer_statistics, "
            "opennosh_pgqueuer_schedules, contribution_drafts, users CASCADE"
        )
        await connection.execute(
            "INSERT INTO users (id, email, password_hash) VALUES "
            "($1, 'contributor@example.test', 'hash'), "
            "($2, 'steward@example.test', 'hash')",
            CONTRIBUTOR,
            STEWARD,
        )
        await connection.execute(
            "INSERT INTO contribution_drafts "
            "(id, user_id, client_draft_id, review_state, fields_json) "
            "VALUES ($1, $6, 'resubmission', 'in_review', "
            '\'{"pack_id":"global-core"}\'), '
            "($2, $6, 'resubmission-race', 'in_review', "
            '\'{"pack_id":"global-core"}\'), '
            "($3, $6, 'resubmission-bypass', 'in_review', "
            '\'{"pack_id":"global-core"}\'), '
            "($4, $6, 'resubmission-evidence-race', 'in_review', "
            '\'{"pack_id":"global-core"}\'), '
            "($5, $6, 'resubmission-first-race', 'in_review', "
            '\'{"pack_id":"global-core"}\')',
            DRAFT,
            SECOND_DRAFT,
            THIRD_DRAFT,
            FOURTH_DRAFT,
            FIFTH_DRAFT,
            CONTRIBUTOR,
        )
        await connection.execute(
            "INSERT INTO governance_role_assignments "
            "(pack_id, actor_id, role, granted_by_actor_id, grant_reason, granted_at) "
            "VALUES ('global-core', $1, 'steward', $1, 'test grant', $2)",
            STEWARD,
            NOW,
        )
    finally:
        await connection.close()

    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    queue = PgQueuerJobQueue(clock=lambda: NOW)
    try:
        async with sessions() as session:
            async with session.begin():
                await seed_verified_reference_evidence(
                    session,
                    draft_id=DRAFT,
                    draft_version=1,
                    now=NOW,
                )
                await seed_verified_reference_evidence(
                    session,
                    draft_id=SECOND_DRAFT,
                    draft_version=1,
                    now=NOW,
                )
                third_manifest = await seed_verified_reference_evidence(
                    session,
                    draft_id=THIRD_DRAFT,
                    draft_version=1,
                    now=NOW,
                )
                fourth_manifest = await seed_verified_reference_evidence(
                    session,
                    draft_id=FOURTH_DRAFT,
                    draft_version=1,
                    now=NOW,
                )
                await seed_verified_reference_evidence(
                    session,
                    draft_id=FIFTH_DRAFT,
                    draft_version=1,
                    now=NOW,
                )
                first_decision, first_intent = await approve_contribution(
                    session,
                    queue,
                    _approval(),
                    now=NOW,
                )
                first_intent.state = "publish_blocked"
                first_intent.workflow_revision = 2
                first_intent.last_failure_code = "forge_retry_exhausted"
                first_intent.last_failure_context_json = {"attempt": 8}
                first_intent.updated_at = NOW + timedelta(minutes=1)

        async with sessions() as session:
            async with session.begin():
                with pytest.raises(
                    IntegrityError,
                    match="publication_resubmission_binding_invalid",
                ):
                    async with session.begin_nested():
                        await create_publication_intent(
                            session,
                            queue,
                            CreatePublicationIntent(
                                source_draft_id=first_intent.source_draft_id,
                                source_draft_version=first_intent.source_draft_version,
                                prior_publication_intent_id=first_intent.id,
                                reviewed_decision_id=first_decision.id,
                                approving_actor_id=STEWARD,
                                pack_id=first_intent.pack_id,
                                record_id=first_intent.record_id,
                                approved_payload_digest=first_intent.approved_payload_digest,
                                expected_base_commit="b" * 40,
                                required_checks=PROTECTED_STATUS_CHECKS,
                                forge_target=first_intent.forge_target,
                                idempotency_key="direct-bypass-must-be-rejected",
                                event_type=ReceiptEventType(first_intent.event_type),
                                prior_receipt_digest=first_intent.prior_receipt_digest,
                                evidence_manifest_digests=tuple(
                                    first_intent.evidence_manifest_digests_json
                                ),
                                evidence_acknowledgements=tuple(
                                    EvidenceAcknowledgement.model_validate(value)
                                    for value in first_intent.evidence_acknowledgements_json
                                ),
                            ),
                            now=NOW + timedelta(minutes=2),
                        )

        command = ResubmitPublication(
            prior_publication_intent_id=first_intent.id,
            deciding_actor_id=STEWARD,
            expected_base_commit="b" * 40,
            reason="Retry unchanged reviewed material from fresh main.",
        )
        resubmitted_at = NOW + timedelta(minutes=2)
        async with sessions() as session:
            async with session.begin():
                next_decision, next_intent = await resubmit_publication(
                    session,
                    queue,
                    command,
                    now=resubmitted_at,
                )

        async with sessions() as session:
            async with session.begin():
                replay_decision, replay_intent = await resubmit_publication(
                    session,
                    queue,
                    command,
                    now=resubmitted_at,
                )
                assert replay_decision.id == next_decision.id
                assert replay_intent.id == next_intent.id

        async with sessions() as session:
            async with session.begin():
                with pytest.raises(
                    GovernanceDecisionError,
                    match="publication_already_resubmitted",
                ):
                    await resubmit_publication(
                        session,
                        queue,
                        ResubmitPublication(
                            prior_publication_intent_id=first_intent.id,
                            deciding_actor_id=STEWARD,
                            expected_base_commit="c" * 40,
                            reason="Conflicting replay must not fork the successor lineage.",
                        ),
                        now=resubmitted_at,
                    )

        pool = await asyncpg.create_pool(asyncpg_dsn(database_url), min_size=1, max_size=1)
        assert pool is not None
        try:
            async with pool.acquire() as connection:
                execute_after = await connection.fetchval(
                    f"SELECT execute_after FROM {PGQUEUER_SETTINGS.queue_table} "
                    "WHERE status = 'queued' "
                    "AND convert_from(payload, 'UTF8')::jsonb ->> 'subject_id' = $1",
                    str(next_intent.id),
                )
            assert execute_after is not None
            wakeup = await ensure_publication_activation_wakeup(
                pool,
                next_intent.id,
                now=execute_after + timedelta(microseconds=1),
            )
        finally:
            await pool.close()
        assert wakeup.outcome is PublicationActivationWakeupOutcome.EXISTING
        assert wakeup.state.value == "pending"
        assert wakeup.active_jobs == 1
        assert wakeup.eligible is True

        async with sessions() as session:
            persisted_first = await session.get(type(first_intent), first_intent.id)
            persisted_next = await session.get(type(next_intent), next_intent.id)
            decisions = (
                await session.scalars(
                    select(type(first_decision)).where(
                        type(first_decision).source_draft_id == DRAFT
                    )
                )
            ).all()
            queue_count = await session.scalar(
                select(func.count()).select_from(text(PGQUEUER_SETTINGS.queue_table))
            )

        assert persisted_first is not None
        assert persisted_first.state == "publish_blocked"
        assert persisted_first.workflow_revision == 2
        assert persisted_first.last_failure_code == "forge_retry_exhausted"
        assert persisted_next is not None
        assert persisted_next.state == "pending"
        assert persisted_next.prior_publication_intent_id == first_intent.id
        assert persisted_next.source_draft_id == first_intent.source_draft_id
        assert persisted_next.source_draft_version == first_intent.source_draft_version
        assert persisted_next.approved_payload_digest == first_intent.approved_payload_digest
        assert persisted_next.evidence_manifest_digests_json == (
            first_intent.evidence_manifest_digests_json
        )
        assert next_decision.prior_decision_id == first_decision.id
        assert next_decision.expected_base_commit == "b" * 40
        assert len(decisions) == 2
        assert queue_count == 2

        immutable_connection = await asyncpg.connect(asyncpg_dsn(database_url))
        try:
            with pytest.raises(
                asyncpg.CheckViolationError,
                match="publication_intent_lineage_is_immutable",
            ):
                await immutable_connection.execute(
                    "UPDATE publication_intents "
                    "SET prior_publication_intent_id = NULL WHERE id = $1",
                    next_intent.id,
                )
            for immutable_intent_id in (first_intent.id, next_intent.id):
                with pytest.raises(
                    asyncpg.CheckViolationError,
                    match="publication_intent_history_is_immutable",
                ):
                    await immutable_connection.execute(
                        "DELETE FROM publication_intents WHERE id = $1",
                        immutable_intent_id,
                    )
            with pytest.raises(
                asyncpg.CheckViolationError,
                match="publication_already_resubmitted",
            ):
                await immutable_connection.execute(
                    "INSERT INTO governance_merge_authorizations "
                    "(publication_intent_id, decision_id, pack_id, head_commit, "
                    "approved_payload_digest, authorized_at) "
                    "VALUES ($1, $2, 'global-core', $3, $4, $5)",
                    first_intent.id,
                    first_decision.id,
                    "b" * 40,
                    first_intent.approved_payload_digest,
                    NOW + timedelta(minutes=3),
                )
        finally:
            await immutable_connection.close()

        async with sessions() as session:
            async with session.begin():
                with pytest.raises(GovernanceDecisionError, match="publication_not_terminal"):
                    await resubmit_publication(
                        session,
                        queue,
                        ResubmitPublication(
                            prior_publication_intent_id=next_intent.id,
                            deciding_actor_id=STEWARD,
                            expected_base_commit="c" * 40,
                            reason="Invalid retry of an active successor.",
                        ),
                        now=NOW + timedelta(minutes=3),
                    )
                await intervene_publication(
                    session,
                    next_intent.id,
                    actor_id=STEWARD,
                    action=GovernanceDecisionOutcome.CHANGES_REQUESTED,
                    reason="Reviewed material must change before another attempt.",
                    now=NOW + timedelta(minutes=3),
                )

        async with sessions() as session:
            async with session.begin():
                with pytest.raises(GovernanceDecisionError, match="publication_intervened"):
                    await resubmit_publication(
                        session,
                        queue,
                        ResubmitPublication(
                            prior_publication_intent_id=next_intent.id,
                            deciding_actor_id=STEWARD,
                            expected_base_commit="c" * 40,
                            reason="Intervened material cannot be retried unchanged.",
                        ),
                        now=NOW + timedelta(minutes=4),
                    )

        async with sessions() as session:
            async with session.begin():
                second_decision, second_intent = await approve_contribution(
                    session,
                    queue,
                    _approval(
                        draft_id=SECOND_DRAFT,
                        record_id="chickpeas",
                        path="packs/global-core/foods/chickpeas.json",
                    ),
                    now=NOW + timedelta(minutes=5),
                )
                second_intent.state = "failed"
                second_intent.last_failure_code = "test_terminal_failure"
                second_intent.updated_at = NOW + timedelta(minutes=6)

        async def attempt_resubmission_during_authorization() -> None:
            async with sessions() as session:
                async with session.begin():
                    await resubmit_publication(
                        session,
                        queue,
                        ResubmitPublication(
                            prior_publication_intent_id=second_intent.id,
                            deciding_actor_id=STEWARD,
                            expected_base_commit="d" * 40,
                            reason="Must lose to committed merge authority.",
                        ),
                        now=NOW + timedelta(minutes=7),
                    )

        authorization_connection = await asyncpg.connect(asyncpg_dsn(database_url))
        authorization_transaction = authorization_connection.transaction()
        await authorization_transaction.start()
        transaction_open = True
        try:
            await authorization_connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                "opennosh.governance-pack:global-core",
            )
            await authorization_connection.execute(
                "INSERT INTO governance_merge_authorizations "
                "(publication_intent_id, decision_id, pack_id, head_commit, "
                "approved_payload_digest, authorized_at) "
                "VALUES ($1, $2, 'global-core', $3, $4, $5)",
                second_intent.id,
                second_decision.id,
                "a" * 40,
                second_intent.approved_payload_digest,
                NOW + timedelta(minutes=7),
            )
            blocked_resubmission = asyncio.create_task(attempt_resubmission_during_authorization())
            await asyncio.sleep(0.1)
            assert not blocked_resubmission.done()
            await authorization_transaction.commit()
            transaction_open = False
            with pytest.raises(
                GovernanceDecisionError,
                match="merge_authorization_committed",
            ):
                await blocked_resubmission
        finally:
            if transaction_open:
                await authorization_transaction.rollback()
            await authorization_connection.close()

        async with sessions() as session:
            successor_count = await session.scalar(
                select(func.count())
                .select_from(type(second_intent))
                .where(type(second_intent).prior_publication_intent_id == second_intent.id)
            )
        assert successor_count == 0

        async with sessions() as session:
            async with session.begin():
                third_decision, third_intent = await approve_contribution(
                    session,
                    queue,
                    _approval(
                        draft_id=THIRD_DRAFT,
                        record_id="black-beans",
                        path="packs/global-core/foods/black-beans.json",
                    ),
                    now=NOW + timedelta(minutes=8),
                )
                third_intent.state = "failed"
                third_intent.last_failure_code = "test_terminal_failure"
                third_intent.updated_at = NOW + timedelta(minutes=9)

        bypass_decision = type(third_decision)(
            id=uuid4(),
            prior_decision_id=third_decision.id,
            source_draft_id=third_decision.source_draft_id,
            source_draft_version=third_decision.source_draft_version,
            pack_id=third_decision.pack_id,
            record_id=third_decision.record_id,
            contributor_actor_id=third_decision.contributor_actor_id,
            deciding_actor_id=STEWARD,
            outcome=third_decision.outcome,
            reason="Direct successor decision before evidence removal.",
            approved_payload_digest=third_decision.approved_payload_digest,
            approved_changes_json=third_decision.approved_changes_json,
            expected_base_commit="e" * 40,
            required_checks_json=third_decision.required_checks_json,
            forge_target=third_decision.forge_target,
            decided_at=NOW + timedelta(minutes=10),
        )
        async with sessions() as session:
            async with session.begin():
                session.add(bypass_decision)
                await session.flush()

        async with sessions() as session:
            async with session.begin():
                await tombstone_evidence(
                    session,
                    evidence_id=third_manifest.evidence_id,
                    removed_by_actor_id=STEWARD,
                    reason="Test stale-evidence bypass protection.",
                    now=NOW + timedelta(minutes=11),
                )

        async with sessions() as session:
            async with session.begin():
                with pytest.raises(
                    IntegrityError,
                    match="publication_resubmission_evidence_invalid",
                ):
                    async with session.begin_nested():
                        await create_publication_intent(
                            session,
                            queue,
                            CreatePublicationIntent(
                                source_draft_id=third_intent.source_draft_id,
                                source_draft_version=third_intent.source_draft_version,
                                prior_publication_intent_id=third_intent.id,
                                reviewed_decision_id=bypass_decision.id,
                                approving_actor_id=STEWARD,
                                pack_id=third_intent.pack_id,
                                record_id=third_intent.record_id,
                                approved_payload_digest=third_intent.approved_payload_digest,
                                expected_base_commit=bypass_decision.expected_base_commit,
                                required_checks=PROTECTED_STATUS_CHECKS,
                                forge_target=third_intent.forge_target,
                                idempotency_key="stale-evidence-bypass-must-be-rejected",
                                event_type=ReceiptEventType(third_intent.event_type),
                                prior_receipt_digest=third_intent.prior_receipt_digest,
                                evidence_manifest_digests=tuple(
                                    third_intent.evidence_manifest_digests_json
                                ),
                                evidence_acknowledgements=tuple(
                                    EvidenceAcknowledgement.model_validate(value)
                                    for value in third_intent.evidence_acknowledgements_json
                                ),
                            ),
                            now=NOW + timedelta(minutes=12),
                        )

        async with sessions() as session:
            async with session.begin():
                fourth_decision, fourth_intent = await approve_contribution(
                    session,
                    queue,
                    _approval(
                        draft_id=FOURTH_DRAFT,
                        record_id="kidney-beans",
                        path="packs/global-core/foods/kidney-beans.json",
                    ),
                    now=NOW + timedelta(minutes=13),
                )
                fourth_intent.state = "failed"
                fourth_intent.last_failure_code = "test_terminal_failure"
                fourth_intent.updated_at = NOW + timedelta(minutes=14)

        async def attempt_resubmission_during_evidence_removal() -> None:
            async with sessions() as session:
                async with session.begin():
                    await resubmit_publication(
                        session,
                        queue,
                        ResubmitPublication(
                            prior_publication_intent_id=fourth_intent.id,
                            deciding_actor_id=STEWARD,
                            expected_base_commit="f" * 40,
                            reason="Must lose to committed evidence removal.",
                        ),
                        now=NOW + timedelta(minutes=15),
                    )

        tombstone_session = sessions()
        tombstone_transaction = await tombstone_session.begin()
        transaction_open = True
        try:
            await tombstone_evidence(
                tombstone_session,
                evidence_id=fourth_manifest.evidence_id,
                removed_by_actor_id=STEWARD,
                reason="Test concurrent evidence removal serialization.",
                now=NOW + timedelta(minutes=15),
            )
            blocked_resubmission = asyncio.create_task(
                attempt_resubmission_during_evidence_removal()
            )
            await asyncio.sleep(0.1)
            assert not blocked_resubmission.done()
            await tombstone_transaction.commit()
            transaction_open = False
            with pytest.raises(GovernanceDecisionError, match="evidence_tombstoned"):
                await blocked_resubmission
        finally:
            if transaction_open:
                await tombstone_transaction.rollback()
            await tombstone_session.close()

        async with sessions() as session:
            fourth_successor_count = await session.scalar(
                select(func.count())
                .select_from(type(fourth_intent))
                .where(type(fourth_intent).prior_publication_intent_id == fourth_intent.id)
            )
            fourth_decision_successor_count = await session.scalar(
                select(func.count())
                .select_from(type(fourth_decision))
                .where(type(fourth_decision).prior_decision_id == fourth_decision.id)
            )
        assert fourth_successor_count == 0
        assert fourth_decision_successor_count == 0

        async with sessions() as session:
            async with session.begin():
                fifth_decision, fifth_intent = await approve_contribution(
                    session,
                    queue,
                    _approval(
                        draft_id=FIFTH_DRAFT,
                        record_id="navy-beans",
                        path="packs/global-core/foods/navy-beans.json",
                    ),
                    now=NOW + timedelta(minutes=16),
                )
                fifth_intent.state = "failed"
                fifth_intent.last_failure_code = "test_terminal_failure"
                fifth_intent.updated_at = NOW + timedelta(minutes=17)

        authorization_connection = await asyncpg.connect(asyncpg_dsn(database_url))

        async def authorize_with_unbound_pack() -> None:
            await authorization_connection.execute(
                "INSERT INTO governance_merge_authorizations "
                "(publication_intent_id, decision_id, pack_id, head_commit, "
                "approved_payload_digest, authorized_at) "
                "VALUES ($1, $2, 'attacker-controlled-pack', $3, $4, $5)",
                fifth_intent.id,
                fifth_decision.id,
                "a" * 40,
                fifth_intent.approved_payload_digest,
                NOW + timedelta(minutes=18),
            )

        resubmission_session = sessions()
        resubmission_transaction = await resubmission_session.begin()
        transaction_open = True
        try:
            fifth_successor_decision, fifth_successor_intent = await resubmit_publication(
                resubmission_session,
                queue,
                ResubmitPublication(
                    prior_publication_intent_id=fifth_intent.id,
                    deciding_actor_id=STEWARD,
                    expected_base_commit="1" * 40,
                    reason="Resubmission must win this transaction ordering.",
                ),
                now=NOW + timedelta(minutes=18),
            )
            blocked_authorization = asyncio.create_task(authorize_with_unbound_pack())
            await asyncio.sleep(0.1)
            assert not blocked_authorization.done()
            await resubmission_transaction.commit()
            transaction_open = False
            with pytest.raises(
                asyncpg.CheckViolationError,
                match="governance_merge_authorization_binding_invalid",
            ):
                await blocked_authorization
        finally:
            if transaction_open:
                await resubmission_transaction.rollback()
            await resubmission_session.close()
            await authorization_connection.close()

        async with sessions() as session:
            authorization_count = await session.scalar(
                select(func.count())
                .select_from(text("governance_merge_authorizations"))
                .where(text("publication_intent_id = :intent_id"))
                .params(intent_id=fifth_intent.id)
            )
        assert fifth_successor_decision.prior_decision_id == fifth_decision.id
        assert fifth_successor_intent.prior_publication_intent_id == fifth_intent.id
        assert authorization_count == 0
    finally:
        await engine.dispose()


@pytest.mark.skipif(
    INTEGRATION_DATABASE_URL is None,
    reason="INTEGRATION_DATABASE_URL is required for PostgreSQL integration tests",
)
def test_terminal_publication_creates_one_auditable_successor() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    alembic_command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_run_terminal_resubmission(INTEGRATION_DATABASE_URL))
