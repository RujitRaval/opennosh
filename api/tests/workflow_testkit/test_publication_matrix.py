from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID

import asyncpg  # type: ignore[import-untyped]
import pytest
from alembic import command as alembic_command
from opennosh_api.jobs import JobLane, JobMessage
from opennosh_api.jobs.pgqueuer import PGQUEUER_SETTINGS, decode_message
from opennosh_api.jobs.worker import asyncpg_dsn
from opennosh_api.publication.executor import PublicationEffectExecutor
from opennosh_api.publication.orchestrator import PublicationOrchestrator
from opennosh_api.publication.repository import PostgresPublicationRepository
from opennosh_api.publication.state import (
    PublicationState,
    PublicationStepName,
    effect_idempotency_key,
    publication_protocol,
)

from api.tests.test_migrations import migration_config
from api.tests.workflow_testkit import (
    FINAL_ACCEPTANCE_FAILPOINTS,
    FORGE_TARGET,
    REQUIRED_PUBLICATION_FAILPOINTS,
    DeterministicClock,
    DeterministicIdGenerator,
    FailpointController,
    InjectedWorkflowCrash,
    PersistentExternalState,
    assert_complete_scenario_coverage,
    assert_publication_trust_invariants,
    capture_publication_snapshot,
    expire_publication_lease,
    publication_adapter_registry,
    publication_crash_scenarios,
    reset_trust_tables,
    restore_publication_snapshot,
    seed_publication,
    system_for_step,
)

INTEGRATION_DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL")
NOW = datetime(2026, 8, 25, 12, tzinfo=UTC)
NAMESPACE = UUID("b487e486-69ec-4978-a49b-4a9da08fa88a")


def _message(publication_id: UUID) -> JobMessage:
    return JobMessage(
        lane=JobLane.PUBLICATION,
        job_type="publication.wake",
        subject_id=publication_id,
        idempotency_key="publication-testkit-message",
        workflow_revision=0,
    )


async def _latest_publication_wakeup(
    pool: asyncpg.Pool, publication_id: UUID
) -> tuple[JobMessage, int]:
    row = await pool.fetchrow(
        f"SELECT id, payload FROM {PGQUEUER_SETTINGS.queue_table} "
        "WHERE (convert_from(payload, 'UTF8')::jsonb ->> 'subject_id')::uuid = $1 "
        "AND status IN ('queued', 'picked') ORDER BY id DESC LIMIT 1",
        publication_id,
    )
    if row is None:
        raise AssertionError("Publication snapshot has no restorable queue wake-up")
    return decode_message(row["payload"]), int(row["id"])


def _orchestrator(
    pool: asyncpg.Pool,
    state: PersistentExternalState,
    clock: DeterministicClock,
    *,
    owner: str,
    failpoint: FailpointController | None = None,
) -> PublicationOrchestrator:
    return PublicationOrchestrator(
        repository=PostgresPublicationRepository(pool),
        executor=PublicationEffectExecutor(publication_adapter_registry(state, clock)),
        owner=owner,
        clock=clock,
        failpoint=failpoint,
    )


async def _finish_protocol(
    pool: asyncpg.Pool,
    orchestrator: PublicationOrchestrator,
    message: JobMessage,
    queue_job_id: int,
) -> None:
    repository = PostgresPublicationRepository(pool)
    for _ in range(14):
        snapshot = await repository.load_or_initialize(message.subject_id)
        if snapshot.state is PublicationState.PUBLISHED:
            return
        await orchestrator.process(message, queue_job_id=queue_job_id)
    raise AssertionError("Publication protocol did not reach PUBLISHED deterministically")


async def _run_generated_crash_matrix(database_url: str) -> None:
    scenarios = publication_crash_scenarios(FORGE_TARGET)
    assert_complete_scenario_coverage(scenarios, FORGE_TARGET)
    pool = await asyncpg.create_pool(dsn=asyncpg_dsn(database_url), min_size=1, max_size=4)
    assert pool is not None
    try:
        for scenario_index, scenario in enumerate(scenarios):
            await reset_trust_tables(database_url)
            clock = DeterministicClock(NOW)
            ids = DeterministicIdGenerator(NAMESPACE)
            seeded = await seed_publication(
                database_url,
                now=clock(),
                ids=ids,
                suffix=f"matrix-{scenario_index}",
            )
            state = PersistentExternalState()
            message = _message(seeded.publication_id)
            normal = _orchestrator(pool, state, clock, owner="worker-prime")
            for _ in range(scenario.ordinal):
                await normal.process(message, queue_job_id=seeded.queue_job_id)

            controller = FailpointController(scenario.failpoint)
            crashing = _orchestrator(
                pool,
                state,
                clock,
                owner="worker-crashing",
                failpoint=controller,
            )
            with pytest.raises(
                InjectedWorkflowCrash,
                match=f"crash:{scenario.failpoint.value}",
            ):
                await crashing.process(message, queue_job_id=seeded.queue_job_id)
            assert controller.hits == list(
                REQUIRED_PUBLICATION_FAILPOINTS[
                    : REQUIRED_PUBLICATION_FAILPOINTS.index(scenario.failpoint) + 1
                ]
            )

            await expire_publication_lease(
                pool,
                seeded.publication_id,
                before=clock() - timedelta(microseconds=1),
            )
            clock.advance(timedelta(seconds=61))
            recovering = _orchestrator(pool, state, clock, owner="worker-recreated")
            await recovering.process(message, queue_job_id=seeded.queue_job_id)
            await _finish_protocol(pool, recovering, message, seeded.queue_job_id)

            definition = publication_protocol(FORGE_TARGET)[scenario.ordinal]
            target_key = effect_idempotency_key(
                publication_id=seeded.publication_id,
                workflow_version="1.0",
                step=scenario.step,
                destination=definition.destination,
                approved_payload_digest="a" * 64,
            )
            assert state.apply_count(system_for_step(scenario.step), target_key) == 1

            await recovering.process(message, queue_job_id=seeded.queue_job_id)
            await recovering.process(message, queue_job_id=seeded.queue_job_id)
            await assert_publication_trust_invariants(pool, seeded.publication_id, state)
            queued, distinct_keys = await pool.fetchrow(
                f"SELECT count(*) AS queued, count(DISTINCT dedupe_key) AS distinct_keys "
                f"FROM {PGQUEUER_SETTINGS.queue_table} "
                "WHERE status IN ('queued', 'picked')"
            )
            assert queued == distinct_keys
    finally:
        await pool.close()


async def _run_snapshot_restore_scenario(database_url: str) -> None:
    await reset_trust_tables(database_url)
    clock = DeterministicClock(NOW)
    ids = DeterministicIdGenerator(NAMESPACE)
    seeded = await seed_publication(
        database_url,
        now=clock(),
        ids=ids,
        suffix="snapshot-restore",
    )
    pool = await asyncpg.create_pool(dsn=asyncpg_dsn(database_url), min_size=1, max_size=3)
    assert pool is not None
    try:
        state = PersistentExternalState()
        message = _message(seeded.publication_id)
        first_worker = _orchestrator(pool, state, clock, owner="worker-before-checkpoint")
        await first_worker.process(message, queue_job_id=seeded.queue_job_id)
        database_checkpoint = await capture_publication_snapshot(pool, seeded.publication_id)
        external_checkpoint = state.snapshot()

        await first_worker.process(message, queue_job_id=seeded.queue_job_id)
        await restore_publication_snapshot(pool, database_checkpoint)
        state.restore(external_checkpoint)

        restored_message, restored_job_id = await _latest_publication_wakeup(
            pool, seeded.publication_id
        )
        assert restored_message.workflow_revision == 2
        recreated = _orchestrator(pool, state, clock, owner="worker-after-restore")
        await recreated.process(restored_message, queue_job_id=restored_job_id)
        await _finish_protocol(pool, recreated, restored_message, restored_job_id)
        await assert_publication_trust_invariants(pool, seeded.publication_id, state)
    finally:
        await pool.close()


@pytest.mark.skipif(
    INTEGRATION_DATABASE_URL is None,
    reason="INTEGRATION_DATABASE_URL is required for PostgreSQL integration tests",
)
def test_generated_publication_crash_matrix_preserves_global_invariants() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    alembic_command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_run_generated_crash_matrix(INTEGRATION_DATABASE_URL))


@pytest.mark.skipif(
    INTEGRATION_DATABASE_URL is None,
    reason="INTEGRATION_DATABASE_URL is required for PostgreSQL integration tests",
)
def test_worker_recreation_can_restore_database_and_external_checkpoints() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    alembic_command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_run_snapshot_restore_scenario(INTEGRATION_DATABASE_URL))


async def _run_final_acceptance_crash_boundaries(database_url: str) -> None:
    pool = await asyncpg.create_pool(dsn=asyncpg_dsn(database_url), min_size=1, max_size=3)
    assert pool is not None
    try:
        for scenario_index, failpoint in enumerate(FINAL_ACCEPTANCE_FAILPOINTS):
            await reset_trust_tables(database_url)
            clock = DeterministicClock(NOW)
            ids = DeterministicIdGenerator(NAMESPACE)
            seeded = await seed_publication(
                database_url,
                now=clock(),
                ids=ids,
                suffix=f"final-{scenario_index}",
            )
            state = PersistentExternalState()
            message = _message(seeded.publication_id)
            normal = _orchestrator(pool, state, clock, owner="final-prime")
            for _ in PublicationStepName:
                await normal.process(message, queue_job_id=seeded.queue_job_id)

            controller = FailpointController(failpoint)
            crashing = _orchestrator(
                pool,
                state,
                clock,
                owner="final-crashing",
                failpoint=controller,
            )
            with pytest.raises(InjectedWorkflowCrash, match=f"crash:{failpoint.value}"):
                await crashing.process(message, queue_job_id=seeded.queue_job_id)
            assert controller.hits == list(
                FINAL_ACCEPTANCE_FAILPOINTS[: FINAL_ACCEPTANCE_FAILPOINTS.index(failpoint) + 1]
            )

            recovering = _orchestrator(pool, state, clock, owner="final-recreated")
            await recovering.process(message, queue_job_id=seeded.queue_job_id)
            await recovering.process(message, queue_job_id=seeded.queue_job_id)
            await assert_publication_trust_invariants(pool, seeded.publication_id, state)
            accepted_count = await pool.fetchval(
                "SELECT count(*) FROM accepted_events WHERE publication_intent_id = $1",
                seeded.publication_id,
            )
            assert accepted_count == 1
    finally:
        await pool.close()


@pytest.mark.skipif(
    INTEGRATION_DATABASE_URL is None,
    reason="INTEGRATION_DATABASE_URL is required for PostgreSQL integration tests",
)
def test_final_acceptance_crash_boundaries_preserve_one_canonical_event() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    alembic_command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_run_final_acceptance_crash_boundaries(INTEGRATION_DATABASE_URL))
