from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta

import asyncpg  # type: ignore[import-untyped]
import pytest
from alembic import command as alembic_command
from opennosh_api.jobs import JobLane, JobMessage
from opennosh_api.jobs.pgqueuer import PGQUEUER_SETTINGS, PgQueuerJobQueue
from opennosh_api.jobs.worker import asyncpg_dsn
from opennosh_api.publication.executor import PublicationEffectExecutor
from opennosh_api.publication.orchestrator import (
    PublicationFailpoint,
    PublicationOrchestrator,
)
from opennosh_api.publication.planner import plan_next_action
from opennosh_api.publication.repository import PostgresPublicationRepository
from opennosh_api.publication.service import create_publication_intent
from opennosh_api.publication.state import (
    EffectIntent,
    ExternalObservation,
    ObservationStatus,
    PublicationStepName,
    effect_idempotency_key,
    publication_protocol,
)
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tests.jobs.test_pgqueuer_integration import (
    create_draft,
    publication_command,
    reset_t4_tables,
)

from api.tests.test_migrations import migration_config

INTEGRATION_DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL")
NOW = datetime(2026, 8, 25, 12, tzinfo=UTC)


class PersistentProtocolAdapter:
    identity = "persistent-protocol-fake"
    version = "1"

    def __init__(self) -> None:
        self.effect_counts: dict[str, int] = {}

    async def apply(self, intent: EffectIntent) -> None:
        self.effect_counts[intent.idempotency_key] = (
            self.effect_counts.get(intent.idempotency_key, 0) + 1
        )

    async def observe(self, intent: EffectIntent) -> ExternalObservation:
        verified = intent.idempotency_key in self.effect_counts
        return ExternalObservation(
            step=intent.step,
            status=ObservationStatus.VERIFIED if verified else ObservationStatus.ABSENT,
            observed_at=NOW,
            destination=intent.destination,
            effect_idempotency_key=intent.idempotency_key,
            adapter_identity=self.identity,
            adapter_version=self.version,
            content_digest="a" * 64 if verified else None,
            external_reference="b" * 40 if verified else None,
        )


def registry(
    adapter: PersistentProtocolAdapter,
) -> dict[PublicationStepName, PersistentProtocolAdapter]:
    return {step: adapter for step in PublicationStepName}


async def create_intent(database_url: str) -> tuple[object, int]:
    queue = PgQueuerJobQueue(clock=lambda: NOW)
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        draft_id = await create_draft(database_url, "t10")
        async with sessions() as session:
            async with session.begin():
                intent = await create_publication_intent(
                    session,
                    queue,
                    publication_command(draft_id),
                    now=NOW,
                )
        async with engine.connect() as connection:
            result = await connection.exec_driver_sql(
                f"SELECT id FROM {PGQUEUER_SETTINGS.queue_table} ORDER BY id LIMIT 1"
            )
            job_id = result.scalar_one()
        return intent, int(job_id)
    finally:
        await engine.dispose()


async def run_crash_matrix(database_url: str) -> None:
    pool = await asyncpg.create_pool(dsn=asyncpg_dsn(database_url), min_size=1, max_size=3)
    assert pool is not None
    try:
        for failpoint in PublicationFailpoint:
            await reset_t4_tables(database_url)
            intent, queue_job_id = await create_intent(database_url)
            adapter = PersistentProtocolAdapter()

            async def crash(
                point: PublicationFailpoint, target: PublicationFailpoint = failpoint
            ) -> None:
                if point is target:
                    raise RuntimeError(f"crash:{point.value}")

            crashing = PublicationOrchestrator(
                repository=PostgresPublicationRepository(pool),
                executor=PublicationEffectExecutor(registry(adapter)),
                owner="worker-a",
                clock=lambda: NOW,
                failpoint=crash,
            )
            message = JobMessage(
                lane=JobLane.PUBLICATION,
                job_type="publication.wake",
                subject_id=intent.id,
                idempotency_key="publication-intent-key-0001",
                workflow_revision=0,
            )
            with pytest.raises(RuntimeError, match=f"crash:{failpoint.value}"):
                await crashing.process(message, queue_job_id=queue_job_id)

            await pool.execute(
                """
                UPDATE publication_steps
                SET lease_expires_at = $1
                WHERE publication_intent_id = $2 AND state = 'leased'
                """,
                NOW - timedelta(seconds=1),
                intent.id,
            )
            recovering = PublicationOrchestrator(
                repository=PostgresPublicationRepository(pool),
                executor=PublicationEffectExecutor(registry(adapter)),
                owner="worker-b",
                clock=lambda: NOW + timedelta(seconds=30),
            )
            await recovering.process(message, queue_job_id=queue_job_id)

            commit_definition = next(
                definition
                for definition in publication_protocol("github:RujitRaval/opennosh-data")
                if definition.name is PublicationStepName.COMMIT_RECORD
            )
            commit_key = effect_idempotency_key(
                publication_id=intent.id,
                workflow_version="1.0",
                step=PublicationStepName.COMMIT_RECORD,
                destination=commit_definition.destination,
                approved_payload_digest="a" * 64,
            )
            assert adapter.effect_counts.get(commit_key, 0) == 1
            assert (
                await pool.fetchval(
                    """
                    SELECT count(*)
                    FROM publication_durable_acknowledgements
                    WHERE publication_intent_id = $1
                      AND acknowledgement_kind = 'commit_record'
                    """,
                    intent.id,
                )
                == 1
            )
            assert (
                await pool.fetchval(
                    """
                    SELECT state
                    FROM publication_steps
                    WHERE publication_intent_id = $1
                      AND step_name = 'commit_record'
                    """,
                    intent.id,
                )
                == "verified"
            )
            queued, distinct_keys = await pool.fetchrow(
                f"""
                SELECT count(*) AS queued, count(DISTINCT dedupe_key) AS distinct_keys
                FROM {PGQUEUER_SETTINGS.queue_table}
                WHERE status IN ('queued', 'picked')
                """
            )
            assert queued == distinct_keys
    finally:
        await pool.close()


@pytest.mark.skipif(
    INTEGRATION_DATABASE_URL is None,
    reason="INTEGRATION_DATABASE_URL is required for PostgreSQL integration tests",
)
def test_publication_crash_matrix_is_idempotent_and_revision_serialized() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    alembic_command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(run_crash_matrix(INTEGRATION_DATABASE_URL))


async def run_complete_protocol_and_concurrent_claim(database_url: str) -> None:
    pool = await asyncpg.create_pool(dsn=asyncpg_dsn(database_url), min_size=1, max_size=3)
    assert pool is not None
    try:
        await reset_t4_tables(database_url)
        intent_record, queue_job_id = await create_intent(database_url)
        adapter = PersistentProtocolAdapter()
        orchestrator = PublicationOrchestrator(
            repository=PostgresPublicationRepository(pool),
            executor=PublicationEffectExecutor(registry(adapter)),
            owner="complete-worker",
            clock=lambda: NOW,
        )
        message = JobMessage(
            lane=JobLane.PUBLICATION,
            job_type="publication.wake",
            subject_id=intent_record.id,
            idempotency_key="publication-complete-0001",
        )

        for _ in range(len(PublicationStepName) + 1):
            await orchestrator.process(message, queue_job_id=queue_job_id)

        state, verified_steps, accepted_events = await pool.fetchrow(
            """
            SELECT intent.state,
                   (SELECT count(*) FROM publication_steps
                    WHERE publication_intent_id = intent.id AND state = 'verified'),
                   (SELECT count(*) FROM accepted_events
                    WHERE publication_intent_id = intent.id)
            FROM publication_intents AS intent
            WHERE intent.id = $1
            """,
            intent_record.id,
        )
        assert state == "published"
        assert verified_steps == len(PublicationStepName)
        assert accepted_events == 1
        assert len(adapter.effect_counts) == len(PublicationStepName)
        assert set(adapter.effect_counts.values()) == {1}

        await reset_t4_tables(database_url)
        race_intent, race_job_id = await create_intent(database_url)
        repository = PostgresPublicationRepository(pool)
        source = await repository.load_or_initialize(race_intent.id)
        effect = plan_next_action(source, None, now=NOW)
        assert isinstance(effect, EffectIntent)
        claims = await asyncio.gather(
            repository.claim(effect, queue_job_id=race_job_id, owner="worker-a", now=NOW),
            repository.claim(effect, queue_job_id=race_job_id, owner="worker-b", now=NOW),
        )
        assert sum(claim is not None for claim in claims) == 1
        assert (
            await pool.fetchval(
                """
                SELECT count(*) FROM publication_steps
                WHERE publication_intent_id = $1 AND state = 'leased'
                """,
                race_intent.id,
            )
            == 1
        )
    finally:
        await pool.close()


@pytest.mark.skipif(
    INTEGRATION_DATABASE_URL is None,
    reason="INTEGRATION_DATABASE_URL is required for PostgreSQL integration tests",
)
def test_complete_receipt_protocol_and_concurrent_claim_are_serialized() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    alembic_command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(run_complete_protocol_and_concurrent_claim(INTEGRATION_DATABASE_URL))


async def seed_legacy_publication_step(database_url: str) -> None:
    draft_id = await create_draft(database_url, "legacy-t10-migration")
    pool = await asyncpg.create_pool(dsn=asyncpg_dsn(database_url), min_size=1, max_size=2)
    assert pool is not None
    try:
        intent_id = await pool.fetchval(
            """
            INSERT INTO publication_intents (
                source_draft_id, source_draft_version, reviewed_decision_id,
                approving_actor_id, pack_id, record_id, approved_payload_digest,
                expected_base_commit, required_checks_json, forge_target,
                idempotency_key_hash
            )
            VALUES (
                $1, 1, gen_random_uuid(), gen_random_uuid(), 'commons', 'legacy',
                $2, $3, '["schema"]'::jsonb, 'github:RujitRaval/opennosh-data', $4
            )
            RETURNING id
            """,
            draft_id,
            "a" * 64,
            "b" * 40,
            "c" * 64,
        )
        await pool.execute(
            """
            INSERT INTO publication_steps (publication_intent_id, step_name)
            VALUES ($1, 'commit_record')
            """,
            intent_id,
        )
    finally:
        await pool.close()


@pytest.mark.skipif(
    INTEGRATION_DATABASE_URL is None,
    reason="INTEGRATION_DATABASE_URL is required for PostgreSQL integration tests",
)
def test_t10_migration_rejects_nonempty_legacy_step_ledger() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    config = migration_config(INTEGRATION_DATABASE_URL)
    alembic_command.downgrade(config, "20260825_0013")
    try:
        asyncio.run(seed_legacy_publication_step(INTEGRATION_DATABASE_URL))
        with pytest.raises(DBAPIError, match="refuses non-empty legacy"):
            alembic_command.upgrade(config, "head")
    finally:
        asyncio.run(reset_t4_tables(INTEGRATION_DATABASE_URL))
        alembic_command.upgrade(config, "head")
