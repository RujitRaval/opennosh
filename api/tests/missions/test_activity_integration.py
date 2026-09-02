from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import UTC, datetime
from types import MethodType, SimpleNamespace
from uuid import UUID, uuid4

import asyncpg  # type: ignore[import-untyped]
import pytest
from alembic import command as alembic_command
from opennosh_api.jobs.worker import asyncpg_dsn
from opennosh_api.missions.repository import MissionRepository
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.tests.test_migrations import migration_config

INTEGRATION_DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL")
NOW = datetime(2026, 9, 2, 23, 30, tzinfo=UTC)


async def _seed_activity_records(database_url: str) -> tuple[UUID, UUID, UUID]:
    actor_id = uuid4()
    mission_ids = (uuid4(), uuid4(), uuid4())
    definition_ids = (uuid4(), uuid4(), uuid4())
    checkpoint_ids = (uuid4(), uuid4(), uuid4())
    connection = await asyncpg.connect(asyncpg_dsn(database_url))
    try:
        await connection.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, 'hash')",
            actor_id,
            f"activity-integration-{actor_id.hex}@example.test",
        )
        for mission_id, definition_id in zip(mission_ids, definition_ids, strict=True):
            await connection.execute(
                """
                INSERT INTO mission_definitions (
                    id, mission_id, definition_version, gap_kind, title, summary,
                    target_pack_id, target_dataset, acceptance_target, acceptance_criteria,
                    definition_json, proposed_by_actor_id, responsible_steward_actor_id, defined_at
                ) VALUES (
                    $1, $2, 1, 'dataset', 'Activity proof', 'Bounded SQL integration proof',
                    'activity-pack', 'activity-data', 20, 'Count proof-bound records.',
                    '{}'::jsonb, $3, $3, $4
                )
                """,
                definition_id,
                mission_id,
                actor_id,
                NOW,
            )

        accepted_ids: list[UUID] = []
        for index in range(11):
            accepted_id = uuid4()
            accepted_ids.append(accepted_id)
            receipt_digest = hashlib.sha256(f"activity:{actor_id}:{index}".encode()).hexdigest()
            commit_sha = hashlib.sha256(f"commit:{actor_id}:{index}".encode()).hexdigest()[:40]
            await connection.execute(
                """
                INSERT INTO publication_receipts (
                    id, publication_id, schema_version, receipt_digest, event_type,
                    pack_id, record_id, envelope_json, signature_key_id, registry_reference,
                    artifact_reference, published_at, reconciled_at
                ) VALUES (
                    $1, $2, '1.0', $3, 'publication', 'activity-pack', $4,
                    '{}'::jsonb, 'activity-test', 'registry:test', 'artifact:test', $5, $5
                )
                """,
                uuid4(),
                uuid4(),
                receipt_digest,
                f"food-{index}",
                NOW,
            )
            await connection.execute(
                """
                INSERT INTO accepted_events (
                    id, repository, commit_sha, pack_id, record_id, event_type,
                    receipt_digest, published_at
                ) VALUES (
                    $1, 'github:RujitRaval/opennosh', $2, 'activity-pack', $3,
                    'record.published', $4, $5
                )
                """,
                accepted_id,
                commit_sha,
                f"food-{index}",
                receipt_digest,
                NOW,
            )

        for index, (mission_id, definition_id, checkpoint_id, count) in enumerate(
            zip(mission_ids, definition_ids, checkpoint_ids, (6, 6, 1), strict=True)
        ):
            await connection.execute(
                """
                INSERT INTO mission_progress_checkpoints (
                    id, mission_id, definition_id, accepted_count, matched_event_count,
                    event_set_digest, built_at
                ) VALUES ($1, $2, $3, $4, $4, $5, $6)
                """,
                checkpoint_id,
                mission_id,
                definition_id,
                count,
                hashlib.sha256(f"checkpoint:{checkpoint_id}".encode()).hexdigest(),
                NOW,
            )
            locale = "en-US" if index in {0, 1} else None
            event_slice = (
                accepted_ids[:6]
                if index == 0
                else [accepted_ids[0], *accepted_ids[6:11]]
                if index == 1
                else [accepted_ids[-1]]
            )
            for record_index, accepted_id in enumerate(event_slice):
                await connection.execute(
                    """
                    INSERT INTO mission_progress_records (
                        id, checkpoint_id, accepted_event_id, repository, pack_id, record_id,
                        activity_locale, activity_pack_version, activity_source_digest, published_at
                    ) VALUES (
                        $1, $2, $3, 'github:RujitRaval/opennosh', 'activity-pack', $4,
                        $5, $6, $7, $8
                    )
                    """,
                    uuid4(),
                    checkpoint_id,
                    accepted_id,
                    f"checkpoint-{index}-record-{record_index}",
                    locale,
                    "1.0.0" if locale is not None else None,
                    "a" * 64 if locale is not None else None,
                    NOW,
                )
    finally:
        await connection.close()
    return checkpoint_ids


async def _exercise_activity_sql(database_url: str) -> None:
    checkpoint_ids = await _seed_activity_records(database_url)
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            checkpoints = tuple(
                SimpleNamespace(
                    id=checkpoint_id,
                    definition_id=uuid4(),
                    accepted_count=count,
                    matched_event_count=count,
                )
                for checkpoint_id, count in zip(checkpoint_ids, (6, 6, 1), strict=True)
            )
            repository = MissionRepository(session)

            async def rows(
                _self: MissionRepository, _limit: int
            ) -> tuple[tuple[object, object, object], ...]:
                return tuple(
                    (
                        SimpleNamespace(id=checkpoint.definition_id),
                        SimpleNamespace(action="approve"),
                        checkpoint,
                    )
                    for checkpoint in checkpoints[:2]
                )

            async def currentness(
                _self: MissionRepository,
                items: tuple[tuple[object, object], ...],
                *,
                max_lineage_events: int | None = None,
            ) -> dict[UUID, bool]:
                assert max_lineage_events == 20_000
                return {checkpoint.id: True for _definition, checkpoint in items}

            repository._public_mission_snapshot_rows = MethodType(  # type: ignore[method-assign]
                rows,
                repository,
            )
            repository._progress_currentness = MethodType(  # type: ignore[method-assign]
                currentness,
                repository,
            )

            result = await repository.public_mission_activity_locales(100, 10_000, 20_000)
            assert [(item.locale, item.accepted_count) for item in result] == [
                ("en-US", 11),
            ]

        async with sessions() as session:
            checkpoints = tuple(
                SimpleNamespace(
                    id=checkpoint_id,
                    definition_id=uuid4(),
                    accepted_count=count,
                    matched_event_count=count,
                )
                for checkpoint_id, count in zip(checkpoint_ids, (6, 6, 1), strict=True)
            )
            repository = MissionRepository(session)

            async def rows_with_missing(
                _self: MissionRepository, _limit: int
            ) -> tuple[tuple[object, object, object], ...]:
                return tuple(
                    (
                        SimpleNamespace(id=checkpoint.definition_id),
                        SimpleNamespace(action="approve"),
                        checkpoint,
                    )
                    for checkpoint in checkpoints
                )

            async def current_with_missing(
                _self: MissionRepository,
                items: tuple[tuple[object, object], ...],
                *,
                max_lineage_events: int | None = None,
            ) -> dict[UUID, bool]:
                return {checkpoint.id: True for _definition, checkpoint in items}

            repository._public_mission_snapshot_rows = MethodType(  # type: ignore[method-assign]
                rows_with_missing,
                repository,
            )
            repository._progress_currentness = MethodType(  # type: ignore[method-assign]
                current_with_missing,
                repository,
            )
            with pytest.raises(ValueError, match="locale_proof_unavailable"):
                await repository.public_mission_activity_locales(100, 10_000, 20_000)
    finally:
        await engine.dispose()


@pytest.mark.skipif(
    INTEGRATION_DATABASE_URL is None,
    reason="INTEGRATION_DATABASE_URL is required for PostgreSQL integration tests",
)
def test_activity_aggregation_executes_privacy_critical_sql_in_postgresql() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    alembic_command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_exercise_activity_sql(INTEGRATION_DATABASE_URL))
