from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg  # type: ignore[import-untyped]
import pytest
from alembic import command as alembic_command
from opennosh_api.governance.service import revoke_steward
from opennosh_api.jobs.worker import asyncpg_dsn
from opennosh_api.missions.contracts import (
    MissionDefinitionSpec,
    MissionGapKind,
    MissionLifecycleAction,
)
from opennosh_api.missions.policy import MissionLifecycleError
from opennosh_api.missions.projector import project_mission_progress
from opennosh_api.missions.repository import MissionRepository
from opennosh_api.missions.service import (
    ProposeMission,
    TransitionMission,
    propose_mission,
    transition_mission,
)
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.tests.test_migrations import migration_config

INTEGRATION_DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL")
NOW = datetime(2026, 9, 2, 18, tzinfo=UTC)


async def _exercise_concurrency(database_url: str) -> None:
    mission_id = uuid4()
    definition_id = uuid4()
    proposal_id = uuid4()
    proposer_id = uuid4()
    steward_a_id = uuid4()
    steward_b_id = uuid4()
    suffix = mission_id.hex
    connection = await asyncpg.connect(asyncpg_dsn(database_url))
    try:
        await connection.executemany(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, 'hash')",
            (
                (proposer_id, f"mission-proposer-{suffix}@example.test"),
                (steward_a_id, f"mission-steward-a-{suffix}@example.test"),
                (steward_b_id, f"mission-steward-b-{suffix}@example.test"),
            ),
        )
        await connection.executemany(
            "INSERT INTO governance_role_assignments "
            "(pack_id, actor_id, role, granted_by_actor_id, grant_reason, granted_at) "
            "VALUES ('opennosh-starter', $1, 'steward', $1, 'mission test', $2)",
            (
                (proposer_id, NOW),
                (steward_a_id, NOW),
                (steward_b_id, NOW),
            ),
        )
        steward_b_role_id = await connection.fetchval(
            "SELECT id FROM governance_role_assignments "
            "WHERE pack_id = 'opennosh-starter' AND actor_id = $1 AND role = 'steward'",
            steward_b_id,
        )
        assert steward_b_role_id is not None
    finally:
        await connection.close()

    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        proposal_command = ProposeMission(
            mission_id=mission_id,
            definition_id=definition_id,
            event_id=proposal_id,
            actor_id=proposer_id,
            responsible_steward_actor_id=steward_a_id,
            definition=MissionDefinitionSpec(
                gap_kind=MissionGapKind.DATASET,
                title="Fill the institutional dataset gap",
                summary="A bounded mission persisted through governed lifecycle facts.",
                target_pack_id="opennosh-starter",
                target_dataset="institutional-foods",
                acceptance_target=20,
                acceptance_criteria="Count twenty distinct verified active records.",
            ),
            public_reason="The public dataset has a measurable institutional gap.",
        )
        async with sessions() as session, session.begin():
            _definition, proposal = await propose_mission(
                MissionRepository(session), proposal_command, now=NOW
            )

        async def decide(action: MissionLifecycleAction, actor_id: UUID) -> object:
            try:
                async with sessions() as session, session.begin():
                    return await transition_mission(
                        MissionRepository(session),
                        TransitionMission(
                            mission_id=mission_id,
                            definition_id=definition_id,
                            event_id=uuid4(),
                            expected_prior_event_id=proposal.id,
                            actor_id=actor_id,
                            action=action,
                            public_reason=f"Concurrently choose {action.value}.",
                        ),
                        now=NOW + timedelta(minutes=1),
                    )
            except MissionLifecycleError as error:
                return error

        results = await asyncio.gather(
            decide(MissionLifecycleAction.APPROVE, steward_a_id),
            decide(MissionLifecycleAction.CLOSE, steward_b_id),
        )
        successes = [item for item in results if not isinstance(item, MissionLifecycleError)]
        errors = [item for item in results if isinstance(item, MissionLifecycleError)]
        assert len(successes) == 1
        assert [error.code for error in errors] == ["mission_revision_conflict"]

        async with engine.connect() as database:
            count = await database.exec_driver_sql(
                "SELECT count(*) FROM mission_lifecycle_events WHERE mission_id = $1",
                (mission_id,),
            )
            assert count.scalar_one() == 2

        empty_progress = project_mission_progress(
            mission_id=mission_id,
            definition_id=definition_id,
            bindings=(),
            accepted_events=(),
        )
        checkpoint_id = uuid4()
        activation_id = uuid4()
        async with engine.begin() as database:
            await database.exec_driver_sql(
                "INSERT INTO mission_progress_checkpoints "
                "(id, mission_id, definition_id, accepted_count, matched_event_count, "
                "event_set_digest, built_at) VALUES ($1, $2, $3, 0, 0, $4, $5)",
                (checkpoint_id, mission_id, definition_id, empty_progress.event_set_digest, NOW),
            )
            await database.exec_driver_sql(
                "INSERT INTO mission_progress_activations "
                "(id, mission_id, definition_id, checkpoint_id, activated_at) "
                "VALUES ($1, $2, $3, $4, $5)",
                (activation_id, mission_id, definition_id, checkpoint_id, NOW),
            )
        async with sessions() as session:
            repository = MissionRepository(session)
            assert await repository.receipt("f" * 64) is None
            checkpoint = await repository.active_progress(definition_id)
            assert checkpoint is not None
            assert await repository.progress_is_current(checkpoint)

        stale_checkpoint_id = uuid4()
        async with engine.begin() as database:
            await database.exec_driver_sql(
                "INSERT INTO mission_progress_checkpoints "
                "(id, mission_id, definition_id, accepted_count, matched_event_count, "
                "event_set_digest, built_at) VALUES ($1, $2, $3, 0, 0, $4, $5)",
                (stale_checkpoint_id, mission_id, definition_id, "b" * 64, NOW),
            )
            await database.exec_driver_sql(
                "UPDATE mission_progress_activations SET checkpoint_id = $1, activated_at = $2 "
                "WHERE definition_id = $3",
                (stale_checkpoint_id, NOW + timedelta(seconds=1), definition_id),
            )
        async with sessions() as session:
            repository = MissionRepository(session)
            checkpoint = await repository.active_progress(definition_id)
            assert checkpoint is not None
            assert not await repository.progress_is_current(checkpoint)

        async with engine.begin() as database:
            with pytest.raises(DBAPIError, match="mission facts are append-only"):
                await database.exec_driver_sql(
                    "UPDATE mission_lifecycle_events SET public_reason = 'rewritten' "
                    "WHERE mission_id = $1",
                    (mission_id,),
                )

        authorization_locked = asyncio.Event()
        release_authorization = asyncio.Event()
        completion_order: list[str] = []

        async def authorize_while_locked() -> None:
            async with sessions() as session, session.begin():
                assert await MissionRepository(session).actor_is_active_human_steward(
                    actor_id=steward_b_id,
                    pack_id="opennosh-starter",
                    at=NOW + timedelta(hours=1),
                )
                authorization_locked.set()
                await release_authorization.wait()
            completion_order.append("mission-authorization")

        async def revoke_while_authorized() -> None:
            async with sessions() as session, session.begin():
                await revoke_steward(
                    session,
                    steward_b_role_id,
                    revoked_by_actor_id=proposer_id,
                    reason="Rotate the mission steward after the in-flight decision.",
                    now=NOW + timedelta(hours=2),
                )
            completion_order.append("revocation")

        authorization_task = asyncio.create_task(authorize_while_locked())
        await authorization_locked.wait()
        revocation_task = asyncio.create_task(revoke_while_authorized())
        await asyncio.sleep(0.1)
        assert not revocation_task.done()
        release_authorization.set()
        await asyncio.gather(authorization_task, revocation_task)
        assert completion_order == ["mission-authorization", "revocation"]

        async with sessions() as session, session.begin():
            assert not await MissionRepository(session).actor_is_active_human_steward(
                actor_id=steward_b_id,
                pack_id="opennosh-starter",
                at=NOW + timedelta(hours=3),
            )
    finally:
        await engine.dispose()


@pytest.mark.skipif(
    INTEGRATION_DATABASE_URL is None,
    reason="INTEGRATION_DATABASE_URL is required for PostgreSQL integration tests",
)
def test_concurrent_lifecycle_decisions_serialize_and_facts_remain_append_only() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    alembic_command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_exercise_concurrency(INTEGRATION_DATABASE_URL))
