from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]
import pytest
from alembic import command as alembic_command
from opennosh_api.jobs.worker import asyncpg_dsn
from opennosh_api.missions.contracts import (
    MissionDefinitionSpec,
    MissionGapKind,
    MissionLifecycleAction,
)
from opennosh_api.missions.progress_service import (
    RebuildMissionProgress,
    rebuild_mission_progress,
)
from opennosh_api.missions.repository import MissionRepository
from opennosh_api.missions.service import (
    ProposeMission,
    TransitionMission,
    propose_mission,
    transition_mission,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.tests.test_migrations import migration_config

INTEGRATION_DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL")
NOW = datetime(2026, 9, 2, 22, tzinfo=UTC)
PACK_ID = "opennosh-starter"


async def _exercise_public_snapshot(database_url: str) -> None:
    proposer_id = uuid4()
    steward_id = uuid4()
    suffix = proposer_id.hex
    connection = await asyncpg.connect(asyncpg_dsn(database_url))
    try:
        await connection.executemany(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, 'hash')",
            (
                (proposer_id, f"public-mission-proposer-{suffix}@example.test"),
                (steward_id, f"public-mission-steward-{suffix}@example.test"),
            ),
        )
        await connection.executemany(
            "INSERT INTO governance_role_assignments "
            "(pack_id, actor_id, role, granted_by_actor_id, grant_reason, granted_at) "
            "VALUES ($1, $2, 'steward', $2, 'public mission test', $3)",
            ((PACK_ID, proposer_id, NOW), (PACK_ID, steward_id, NOW)),
        )
    finally:
        await connection.close()

    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    approved_mission_id = uuid4()
    approved_definition_id = uuid4()
    proposed_mission_id = uuid4()
    proposed_definition_id = uuid4()
    try:
        async with sessions() as session, session.begin():
            approved_definition, proposal = await propose_mission(
                MissionRepository(session),
                ProposeMission(
                    mission_id=approved_mission_id,
                    definition_id=approved_definition_id,
                    event_id=uuid4(),
                    actor_id=proposer_id,
                    responsible_steward_actor_id=steward_id,
                    definition=MissionDefinitionSpec(
                        gap_kind=MissionGapKind.DATASET,
                        title="Approved public mission",
                        summary="Verify batched public progress proof reads.",
                        target_pack_id=PACK_ID,
                        target_dataset="approved-public-mission",
                        acceptance_target=1,
                        acceptance_criteria="Count one verified accepted record.",
                    ),
                    public_reason="This approved mission may be exposed publicly.",
                ),
                now=NOW,
            )
        async with sessions() as session, session.begin():
            await transition_mission(
                MissionRepository(session),
                TransitionMission(
                    mission_id=approved_mission_id,
                    definition_id=approved_definition.id,
                    event_id=uuid4(),
                    expected_prior_event_id=proposal.id,
                    actor_id=steward_id,
                    action=MissionLifecycleAction.APPROVE,
                    public_reason="Approve the public mission integration fixture.",
                ),
                now=NOW + timedelta(seconds=1),
            )
        async with sessions() as session, session.begin():
            build = await rebuild_mission_progress(
                MissionRepository(session),
                RebuildMissionProgress(
                    checkpoint_id=uuid4(),
                    activation_id=uuid4(),
                    mission_id=approved_mission_id,
                    definition_id=approved_definition_id,
                    expected_active_checkpoint_id=None,
                ),
                now=NOW + timedelta(seconds=2),
            )
        assert build.progress.accepted_count == 0

        async with sessions() as session, session.begin():
            await propose_mission(
                MissionRepository(session),
                ProposeMission(
                    mission_id=proposed_mission_id,
                    definition_id=proposed_definition_id,
                    event_id=uuid4(),
                    actor_id=proposer_id,
                    responsible_steward_actor_id=steward_id,
                    definition=MissionDefinitionSpec(
                        gap_kind=MissionGapKind.DATASET,
                        title="Newer proposed mission",
                        summary="Remain hidden until governance approval.",
                        target_pack_id=PACK_ID,
                        target_dataset="proposed-public-mission",
                        acceptance_target=1,
                        acceptance_criteria="Count one verified accepted record.",
                    ),
                    public_reason="This proposal remains private until approved.",
                ),
                now=NOW + timedelta(seconds=3),
            )

        async with sessions() as session:
            snapshots = await MissionRepository(session).public_mission_snapshots(limit=1)

        assert len(snapshots) == 1
        assert snapshots[0].definition.id == approved_definition_id
        assert snapshots[0].lifecycle_event.action == MissionLifecycleAction.APPROVE.value
        assert snapshots[0].checkpoint is not None
        assert snapshots[0].progress_is_current
    finally:
        await engine.dispose()


@pytest.mark.skipif(
    INTEGRATION_DATABASE_URL is None,
    reason="INTEGRATION_DATABASE_URL is required for PostgreSQL integration tests",
)
def test_public_snapshot_filters_moderation_before_limit_and_batches_proof() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    alembic_command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_exercise_public_snapshot(INTEGRATION_DATABASE_URL))
