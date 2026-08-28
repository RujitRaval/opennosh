from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg  # type: ignore[import-untyped]
import pytest
from alembic import command as alembic_command
from opennosh_api.evidence.contracts import EvidenceAcknowledgement
from opennosh_api.jobs import JobLane, JobMessage, JobRequest
from opennosh_api.jobs.pgqueuer import (
    PGQUEUER_SETTINGS,
    PUBLICATION_ENTRYPOINT,
    PgQueuerJobQueue,
    build_queries,
    decode_message,
)
from opennosh_api.jobs.worker import (
    PublicationActivationWakeupOutcome,
    asyncpg_dsn,
    create_publication_role_driver,
    ensure_publication_activation_wakeup,
)
from opennosh_api.publication.service import (
    CreatePublicationIntent,
    PublicationIntentConflictError,
    create_publication_intent,
)
from opennosh_api.settings import Settings
from pgqueuer.db import AsyncpgDriver
from pgqueuer.ports.repository import EntrypointExecutionParameter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.tests.test_migrations import migration_config

INTEGRATION_DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL")


async def reset_t4_tables(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            receipt_table = await connection.scalar(
                text("SELECT to_regclass(:table_name)"),
                {"table_name": "publication_receipts"},
            )
            if receipt_table is not None:
                await connection.execute(text("TRUNCATE publication_receipts CASCADE"))
            await connection.execute(
                text(
                    "TRUNCATE accepted_events, "
                    "publication_durable_acknowledgements, "
                    "publication_steps, publication_intents, opennosh_pgqueuer, "
                    "opennosh_pgqueuer_log, opennosh_pgqueuer_statistics, "
                    "opennosh_pgqueuer_schedules, contribution_drafts, users CASCADE"
                )
            )
    finally:
        await engine.dispose()


async def create_draft(database_url: str, suffix: str) -> UUID:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            user_id = await connection.scalar(
                text(
                    "INSERT INTO users (email, password_hash) VALUES (:email, 'hash') RETURNING id"
                ),
                {"email": f"publisher-{suffix}@example.test"},
            )
            draft_id = await connection.scalar(
                text(
                    "INSERT INTO contribution_drafts "
                    "(user_id, client_draft_id, review_state) "
                    "VALUES (:user_id, :client_draft_id, 'approved') RETURNING id"
                ),
                {"user_id": user_id, "client_draft_id": f"draft-{suffix}"},
            )
            assert isinstance(draft_id, UUID)
            evidence_table = await connection.scalar(
                text("SELECT to_regclass(:table_name)"),
                {"table_name": "evidence_manifests"},
            )
            if evidence_table is not None:
                await connection.execute(
                    text(
                        "INSERT INTO evidence_manifests "
                        "(source_draft_id, source_draft_version, schema_version, "
                        "evidence_class, manifest_digest, manifest_json, public_state) "
                        "VALUES (:draft_id, 1, '1.0', 'sanitized_media', "
                        ":digest, '{}'::jsonb, 'evidence_preserved')"
                    ),
                    {
                        "draft_id": draft_id,
                        "digest": hashlib.sha256(f"evidence-{suffix}".encode()).hexdigest(),
                    },
                )
            return draft_id
    finally:
        await engine.dispose()


def publication_command(
    draft_id: UUID,
    *,
    key: str = "publication-intent-key-0001",
    record_id: str = "lentils",
) -> CreatePublicationIntent:
    return CreatePublicationIntent(
        source_draft_id=draft_id,
        source_draft_version=1,
        reviewed_decision_id=uuid4(),
        approving_actor_id=uuid4(),
        pack_id="global-core",
        record_id=record_id,
        approved_payload_digest="a" * 64,
        expected_base_commit="b" * 40,
        required_checks=("schema", "provenance", "license"),
        forge_target="github:RujitRaval/opennosh-data",
        idempotency_key=key,
        evidence_manifest_digests=("f" * 64,),
        evidence_acknowledgements=(
            EvidenceAcknowledgement(
                evidence_id=draft_id,
                evidence_class="sanitized_media",
                manifest_digest="f" * 64,
                kind="immutable_sanitized_copy",
                destination="urn:opennosh:durability:evidence",
                content_digest="7" * 64,
                external_reference="memory:evidence",
                verified_at=datetime(2026, 8, 26, tzinfo=UTC),
                adapter_identity="pgqueuer-test-evidence",
                adapter_version="1.0",
            ),
        ),
    )


async def count_rows(database_url: str, table: str) -> int:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            value = await connection.scalar(text(f"SELECT count(*) FROM {table}"))
            assert value is not None
            return int(value)
    finally:
        await engine.dispose()


async def read_queued_message(database_url: str) -> JobMessage:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            payload = await connection.scalar(
                text(f"SELECT payload FROM {PGQUEUER_SETTINGS.queue_table}")
            )
            assert isinstance(payload, bytes)
            return decode_message(payload)
    finally:
        await engine.dispose()


async def transactional_delivery_scenario(database_url: str) -> None:
    await reset_t4_tables(database_url)
    now = datetime(2026, 8, 25, 12, tzinfo=UTC)
    queue = PgQueuerJobQueue(clock=lambda: now)
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        committed_draft = await create_draft(database_url, "committed")
        committed = publication_command(committed_draft)
        async with sessions() as session:
            async with session.begin():
                first_intent = await create_publication_intent(session, queue, committed, now=now)

        assert await count_rows(database_url, "publication_intents") == 1
        assert await count_rows(database_url, PGQUEUER_SETTINGS.queue_table) == 1
        queued_message = await read_queued_message(database_url)
        assert queued_message.subject_id == first_intent.id
        assert queued_message.idempotency_key == committed.idempotency_key

        async with sessions() as session:
            async with session.begin():
                repeated_intent = await create_publication_intent(
                    session, queue, committed, now=now
                )
        assert repeated_intent.id == first_intent.id
        assert await count_rows(database_url, "publication_intents") == 1
        assert await count_rows(database_url, PGQUEUER_SETTINGS.queue_table) == 1

        conflicting = committed.model_copy(update={"record_id": "different-record"})
        async with sessions() as session:
            with pytest.raises(PublicationIntentConflictError, match="Idempotency key"):
                async with session.begin():
                    await create_publication_intent(session, queue, conflicting, now=now)

        different_key = committed.model_copy(
            update={"idempotency_key": "publication-intent-key-different"}
        )
        async with sessions() as session:
            with pytest.raises(PublicationIntentConflictError, match="Draft version"):
                async with session.begin():
                    await create_publication_intent(session, queue, different_key, now=now)

        rollback_draft = await create_draft(database_url, "rollback")
        rollback_command = publication_command(
            rollback_draft,
            key="publication-intent-key-rollback",
            record_id="rollback-record",
        )
        with pytest.raises(RuntimeError, match="force rollback"):
            async with sessions() as session:
                async with session.begin():
                    await create_publication_intent(session, queue, rollback_command, now=now)
                    raise RuntimeError("force rollback")

        assert await count_rows(database_url, "publication_intents") == 1
        assert await count_rows(database_url, PGQUEUER_SETTINGS.queue_table) == 1

        async with engine.connect() as connection:
            health = await queue.health(connection)
        assert health.healthy is True
        assert health.adapter == "pgqueuer:1.3.2"
        assert health.queued == health.eligible == 1
    finally:
        await engine.dispose()


async def delivery_recovery_scenario(database_url: str) -> None:
    await reset_t4_tables(database_url)
    now = datetime.now(UTC)
    queue = PgQueuerJobQueue(clock=lambda: now)
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            low = await queue.enqueue(
                connection,
                JobRequest(
                    message=JobMessage(
                        lane=JobLane.PUBLICATION,
                        job_type="publication.wake",
                        subject_id=uuid4(),
                        idempotency_key="publication-low-priority",
                    ),
                    run_after=now,
                    priority=1,
                    deduplication_key="publication-low-priority",
                ),
            )
            high = await queue.enqueue(
                connection,
                JobRequest(
                    message=JobMessage(
                        lane=JobLane.PUBLICATION,
                        job_type="publication.wake",
                        subject_id=uuid4(),
                        idempotency_key="publication-high-priority",
                    ),
                    run_after=now,
                    priority=10,
                    deduplication_key="publication-high-priority",
                ),
            )
        assert low.enqueued and high.enqueued

        raw = await asyncpg.connect(asyncpg_dsn(database_url))
        try:
            queries = build_queries(AsyncpgDriver(raw))
            entrypoints = {
                PUBLICATION_ENTRYPOINT: EntrypointExecutionParameter(concurrency_limit=0)
            }
            first_manager = uuid4()
            jobs = await queries.dequeue(
                1,
                entrypoints,
                first_manager,
                None,
                timedelta(seconds=30),
            )
            assert [job.id for job in jobs] == [high.job_id]
            assert jobs[0].priority == 10

            await raw.execute(
                f"UPDATE {PGQUEUER_SETTINGS.queue_table} "
                "SET heartbeat = now() - INTERVAL '2 minutes' WHERE id = $1",
                high.job_id,
            )
            recovered = await queries.dequeue(
                1,
                entrypoints,
                uuid4(),
                None,
                timedelta(seconds=30),
            )
            assert [job.id for job in recovered] == [high.job_id]
            assert recovered[0].attempts == jobs[0].attempts

            await raw.execute(f"TRUNCATE {PGQUEUER_SETTINGS.queue_table}")
            await queries.enqueue(
                "opennosh.future.v2",
                b'{"schema_version":"2.0"}',
                dedupe_key="future-version-job",
            )
            unknown = await queries.dequeue(
                1,
                entrypoints,
                uuid4(),
                None,
                timedelta(seconds=30),
            )
            assert unknown == []
        finally:
            await raw.close()

        async with engine.begin() as connection:
            delayed = await queue.enqueue(
                connection,
                JobRequest(
                    message=JobMessage(
                        lane=JobLane.PUBLICATION,
                        job_type="publication.wake",
                        subject_id=uuid4(),
                        idempotency_key="publication-delayed-job",
                    ),
                    run_after=now + timedelta(minutes=5),
                    deduplication_key="publication-delayed-job",
                ),
            )
        async with engine.connect() as connection:
            delayed_health = await queue.health(connection)
        assert delayed_health.queued == 1
        assert delayed_health.eligible == 0

        async with engine.begin() as connection:
            await connection.execute(
                text(
                    f"UPDATE {PGQUEUER_SETTINGS.queue_table} "
                    "SET execute_after = now() - INTERVAL '1 second' WHERE id = :job_id"
                ),
                {"job_id": delayed.job_id},
            )
        async with engine.connect() as connection:
            eligible_health = await queue.health(connection)
            assert eligible_health.eligible == 1
            assert delayed.job_id is not None
            await queue.cancel(connection, delayed.job_id)
            await connection.commit()
        async with engine.connect() as connection:
            cancelled_health = await queue.health(connection)
        assert cancelled_health.queued == 0
        assert cancelled_health.eligible == 0
    finally:
        await engine.dispose()


async def activation_wakeup_recovery_scenario(database_url: str) -> None:
    await reset_t4_tables(database_url)
    now = datetime(2026, 8, 28, 22, tzinfo=UTC)
    queue = PgQueuerJobQueue(clock=lambda: now)
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    pool = await asyncpg.create_pool(asyncpg_dsn(database_url), min_size=1, max_size=2)
    assert pool is not None
    try:
        draft_id = await create_draft(database_url, "activation-recovery")
        async with sessions() as session:
            async with session.begin():
                intent = await create_publication_intent(
                    session,
                    queue,
                    publication_command(draft_id),
                    now=now,
                )

        unrelated_subject = uuid4()
        async with engine.begin() as connection:
            unrelated = await queue.enqueue(
                connection,
                JobRequest(
                    message=JobMessage(
                        lane=JobLane.PUBLICATION,
                        job_type="publication.wake",
                        subject_id=unrelated_subject,
                        idempotency_key="publication-unrelated-picked",
                        workflow_revision=0,
                    ),
                    run_after=now,
                    priority=10,
                    deduplication_key="publication-unrelated-picked",
                ),
            )
        assert unrelated.job_id is not None
        raw = await asyncpg.connect(asyncpg_dsn(database_url))
        try:
            claimed = await build_queries(AsyncpgDriver(raw)).dequeue(
                1,
                {
                    PUBLICATION_ENTRYPOINT: EntrypointExecutionParameter(
                        concurrency_limit=0
                    )
                },
                uuid4(),
                None,
                timedelta(seconds=30),
            )
            assert [job.id for job in claimed] == [unrelated.job_id]
        finally:
            await raw.close()

        async with engine.connect() as connection:
            unrelated_before = (
                await connection.execute(
                    text(
                        f"SELECT status, execute_after, heartbeat, payload "
                        f"FROM {PGQUEUER_SETTINGS.queue_table} WHERE id = :job_id"
                    ),
                    {"job_id": unrelated.job_id},
                )
            ).one()

        existing = await ensure_publication_activation_wakeup(
            pool,
            intent.id,
            now=now,
        )
        assert existing.outcome is PublicationActivationWakeupOutcome.EXISTING
        assert existing.active_jobs == 1
        assert existing.workflow_revision == 0

        async with engine.connect() as connection:
            unrelated_after = (
                await connection.execute(
                    text(
                        f"SELECT status, execute_after, heartbeat, payload "
                        f"FROM {PGQUEUER_SETTINGS.queue_table} WHERE id = :job_id"
                    ),
                    {"job_id": unrelated.job_id},
                )
            ).one()
        assert unrelated_after == unrelated_before

        async with pool.acquire() as claimant:
            async with claimant.transaction():
                await claimant.fetchval(
                    "SELECT id FROM opennosh_pgqueuer WHERE status = 'queued' FOR UPDATE"
                )
                nonblocking = await asyncio.wait_for(
                    ensure_publication_activation_wakeup(pool, intent.id, now=now),
                    timeout=1,
                )
                assert nonblocking.outcome is PublicationActivationWakeupOutcome.EXISTING

        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE opennosh_pgqueuer"))

        first, second = await asyncio.gather(
            ensure_publication_activation_wakeup(pool, intent.id, now=now),
            ensure_publication_activation_wakeup(pool, intent.id, now=now),
        )
        assert {first.outcome, second.outcome} == {
            PublicationActivationWakeupOutcome.ENQUEUED,
            PublicationActivationWakeupOutcome.EXISTING,
        }
        assert await count_rows(database_url, PGQUEUER_SETTINGS.queue_table) == 1
        recovered_message = await read_queued_message(database_url)
        assert recovered_message.subject_id == intent.id
        assert recovered_message.workflow_revision == 0

        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE opennosh_pgqueuer"))
            await connection.execute(
                text("UPDATE publication_intents SET state = 'published' WHERE id = :id"),
                {"id": intent.id},
            )

        terminal = await ensure_publication_activation_wakeup(
            pool,
            intent.id,
            now=now,
        )
        assert terminal.outcome is PublicationActivationWakeupOutcome.TERMINAL
        assert terminal.active_jobs == 0
        assert await count_rows(database_url, PGQUEUER_SETTINGS.queue_table) == 0

        with pytest.raises(LookupError, match="Unknown publication intent"):
            await ensure_publication_activation_wakeup(pool, uuid4(), now=now)
    finally:
        await pool.close()
        await engine.dispose()


@pytest.mark.skipif(
    INTEGRATION_DATABASE_URL is None,
    reason="INTEGRATION_DATABASE_URL is required for PostgreSQL integration tests",
)
def test_publication_intent_and_queue_wakeup_share_one_transaction() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    alembic_command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(transactional_delivery_scenario(INTEGRATION_DATABASE_URL))


@pytest.mark.skipif(
    INTEGRATION_DATABASE_URL is None,
    reason="INTEGRATION_DATABASE_URL is required for PostgreSQL integration tests",
)
def test_pgqueuer_priority_stale_lease_retry_timing_and_unknown_jobs() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    alembic_command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(delivery_recovery_scenario(INTEGRATION_DATABASE_URL))


@pytest.mark.skipif(
    INTEGRATION_DATABASE_URL is None,
    reason="INTEGRATION_DATABASE_URL is required for PostgreSQL integration tests",
)
def test_activation_startup_recovers_only_the_selected_nonterminal_wakeup() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    alembic_command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(activation_wakeup_recovery_scenario(INTEGRATION_DATABASE_URL))


async def graceful_worker_scenario(database_url: str, manifest_path: Path) -> None:
    settings = Settings(
        database_url=database_url,
        publication_database_url=database_url,
        database_capacity_manifest_path=manifest_path,
        app_environment="test",
        _env_file=None,
    )
    driver = await create_publication_role_driver(settings)
    await driver.start()
    driver.stop_claiming()
    await asyncio.wait_for(driver.drain(), timeout=5)
    await driver.close()


@pytest.mark.skipif(
    INTEGRATION_DATABASE_URL is None,
    reason="INTEGRATION_DATABASE_URL is required for PostgreSQL integration tests",
)
def test_publication_worker_verifies_schema_and_shuts_down_gracefully(
    tmp_path: Path,
) -> None:
    assert INTEGRATION_DATABASE_URL is not None
    alembic_command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    source = Path("config/database-capacity.v1.json")
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["roles"]["publication"]["replicas"] = 1
    manifest_path = tmp_path / "database-capacity.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    asyncio.run(graceful_worker_scenario(INTEGRATION_DATABASE_URL, manifest_path))
