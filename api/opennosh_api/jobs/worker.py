from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID

import asyncpg  # type: ignore[import-untyped]
from pgqueuer import PgQueuer
from pgqueuer.db import AsyncpgDriver, AsyncpgPoolDriver
from pgqueuer.errors import RetryRequested
from pgqueuer.models import Channel, Job
from sqlalchemy.engine import make_url

from opennosh_api.capacity import ProcessRole, RoleBudget, load_capacity_manifest
from opennosh_api.jobs.contracts import JobLane, JobMessage
from opennosh_api.jobs.pgqueuer import (
    PGQUEUER_SETTINGS,
    PUBLICATION_ENTRYPOINT,
    build_queries,
    decode_message,
    encode_message,
)
from opennosh_api.public.artifacts import HttpArtifactStore
from opennosh_api.public.r2 import S3R2ObjectWriter
from opennosh_api.public.refresh import (
    LatestPointerRefreshService,
    run_latest_pointer_refresh_loop,
)
from opennosh_api.public.signing import load_production_signing_key
from opennosh_api.public_commons.manifests import ManifestKeyRing
from opennosh_api.publication.adapters import PublicationAdapterRegistry
from opennosh_api.publication.executor import PublicationEffectExecutor
from opennosh_api.publication.orchestrator import PublicationOrchestrator
from opennosh_api.publication.receipts import PublicationReceiptKeyRing
from opennosh_api.publication.repository import PostgresPublicationRepository
from opennosh_api.publication.runtime import (
    PreparedProductionPublicationRuntime,
    run_zero_claim_preactivation_smoke,
    validate_production_adapter_registry,
)
from opennosh_api.publication.state import PublicationState
from opennosh_api.settings import Settings, get_settings

PUBLICATION_DRAIN_TIMEOUT_SECONDS = 30.0
PGQUEUER_HEARTBEAT_TIMEOUT_SECONDS = 30.0
PUBLICATION_FAILURE_RETRY_DELAY = timedelta(seconds=65)
PUBLICATION_MAX_UNEXPECTED_ATTEMPTS = 5
logger = logging.getLogger(__name__)


def asyncpg_dsn(database_url: str) -> str:
    """Translate the SQLAlchemy async URL without losing escaped credentials."""

    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        raise ValueError("PgQueuer requires a PostgreSQL database URL")
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


class PublicationActivationWakeupOutcome(StrEnum):
    EXISTING = "existing"
    ENQUEUED = "enqueued"
    TERMINAL = "terminal"


@dataclass(frozen=True, slots=True)
class PublicationActivationWakeup:
    outcome: PublicationActivationWakeupOutcome
    state: PublicationState
    workflow_revision: int
    active_jobs: int
    eligible: bool
    job_id: int | None


_TERMINAL_PUBLICATION_STATES = frozenset(
    {
        PublicationState.BLOCKED,
        PublicationState.FAILED,
        PublicationState.PUBLISHED,
        PublicationState.PUBLISH_BLOCKED,
        PublicationState.QUARANTINED,
    }
)


async def ensure_publication_activation_wakeup(
    pool: Any,
    publication_id: UUID,
    *,
    now: datetime,
) -> PublicationActivationWakeup:
    """Recover one configured nonterminal activation without scanning the backlog."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Publication activation recovery time must include a timezone")
    async with pool.acquire() as connection:
        async with connection.transaction():
            intent = await connection.fetchrow(
                """
                SELECT state, workflow_revision, next_attempt_at
                FROM publication_intents
                WHERE id = $1
                FOR UPDATE
                """,
                publication_id,
            )
            if intent is None:
                raise LookupError(f"Unknown publication intent: {publication_id}")
            state = PublicationState(str(intent["state"]))
            revision = int(intent["workflow_revision"])
            # PgQueuer claims a queue row before the orchestrator locks this intent.
            # Reading active rows without locking them keeps rolling deploys from
            # taking the same two locks in opposite order.
            active = await connection.fetch(
                f"""
                SELECT id, status, execute_after, heartbeat, payload
                FROM {PGQUEUER_SETTINGS.queue_table}
                WHERE entrypoint = $1
                  AND status IN ('queued', 'picked')
                  AND convert_from(payload, 'UTF8')::jsonb ->> 'subject_id' = $2
                ORDER BY id
                """,
                PUBLICATION_ENTRYPOINT,
                str(publication_id),
            )
            eligible = any(
                (
                    str(row["status"]) == "queued"
                    and row["execute_after"] < now
                )
                or (
                    str(row["status"]) == "picked"
                    and row["execute_after"] < now
                    and row["heartbeat"] is not None
                    and row["heartbeat"]
                    < now - timedelta(seconds=PGQUEUER_HEARTBEAT_TIMEOUT_SECONDS)
                )
                for row in active
            )
            for row in active:
                message = decode_message(bytes(row["payload"]))
                if (
                    message.subject_id != publication_id
                    or message.workflow_revision is None
                    or message.workflow_revision > revision
                ):
                    raise RuntimeError(
                        "Active publication wake-up conflicts with the configured intent"
                    )
            if state in _TERMINAL_PUBLICATION_STATES:
                return PublicationActivationWakeup(
                    outcome=PublicationActivationWakeupOutcome.TERMINAL,
                    state=state,
                    workflow_revision=revision,
                    active_jobs=len(active),
                    eligible=eligible,
                    job_id=int(active[0]["id"]) if active else None,
                )
            if active:
                return PublicationActivationWakeup(
                    outcome=PublicationActivationWakeupOutcome.EXISTING,
                    state=state,
                    workflow_revision=revision,
                    active_jobs=len(active),
                    eligible=eligible,
                    job_id=int(active[0]["id"]),
                )

            run_after = max(intent["next_attempt_at"], now)
            key = f"publication:{publication_id}:activation-recovery:{revision}"
            message = JobMessage(
                lane=JobLane.PUBLICATION,
                job_type="publication.wake",
                subject_id=publication_id,
                idempotency_key=key,
                workflow_revision=revision,
            )
            queries = build_queries(AsyncpgDriver(connection))
            (job_id,) = await queries.enqueue(
                PUBLICATION_ENTRYPOINT,
                encode_message(message),
                execute_after=max(run_after - now, timedelta()),
                dedupe_key=key,
                on_conflict="skip",
            )
            if job_id is None:
                existing = await connection.fetchrow(
                    f"""
                    SELECT id, payload
                    FROM {PGQUEUER_SETTINGS.queue_table}
                    WHERE dedupe_key = $1
                      AND status IN ('queued', 'picked')
                    """,
                    key,
                )
                if existing is None:
                    raise RuntimeError(
                        "Activation wake-up was neither enqueued nor already present"
                    )
                existing_message = decode_message(bytes(existing["payload"]))
                if (
                    existing_message.subject_id != publication_id
                    or existing_message.workflow_revision != revision
                ):
                    raise RuntimeError(
                        "Activation wake-up dedupe key is bound to another message"
                    )
                return PublicationActivationWakeup(
                    outcome=PublicationActivationWakeupOutcome.EXISTING,
                    state=state,
                    workflow_revision=revision,
                    active_jobs=1,
                    eligible=run_after <= now,
                    job_id=int(existing["id"]),
                )
            return PublicationActivationWakeup(
                outcome=PublicationActivationWakeupOutcome.ENQUEUED,
                state=state,
                workflow_revision=revision,
                active_jobs=1,
                eligible=run_after <= now,
                job_id=int(job_id),
            )


class PgQueuerRoleDriver:
    """Drainable runtime wrapper; PgQueuer owns delivery, not domain state."""

    def __init__(
        self,
        queue: PgQueuer,
        pool: Any,
        *,
        worker_concurrency: int,
        task_name: str = "opennosh-publication-pgqueuer",
        resource_closer: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        if worker_concurrency < 2:
            raise ValueError("PgQueuer worker concurrency must be at least two")
        self._queue = queue
        self._pool = pool
        self._worker_concurrency = worker_concurrency
        self._task_name = task_name
        self._resource_closer = resource_closer
        self._batch_size = min(10, worker_concurrency // 2)
        self._run_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        await self._queue.qm.verify_structure()
        self._run_task = asyncio.create_task(
            self._queue.run(
                batch_size=self._batch_size,
                max_concurrent_tasks=self._worker_concurrency,
            ),
            name=self._task_name,
        )
        await asyncio.sleep(0)
        if self._run_task.done():
            await self._run_task

    def stop_claiming(self) -> None:
        self._queue.shutdown.set()

    async def drain(self) -> None:
        if self._run_task is not None:
            await self._run_task

    async def close(self) -> None:
        self._queue.shutdown.set()
        if self._run_task is not None and not self._run_task.done():
            self._run_task.cancel()
            await asyncio.gather(self._run_task, return_exceptions=True)
        try:
            await self._pool.close()
        finally:
            if self._resource_closer is not None:
                await self._resource_closer()


async def supervise_publication_claims(
    driver: PgQueuerRoleDriver,
    shutdown_requested: asyncio.Event,
    *,
    drain_timeout_seconds: float,
) -> None:
    """Treat an unexpected PgQueuer exit as a worker-level failure."""

    drain_task: asyncio.Task[None] | None = None
    shutdown_task: asyncio.Task[bool] | None = None
    try:
        await driver.start()
        drain_task = asyncio.create_task(
            driver.drain(),
            name="opennosh-publication-claims-exit",
        )
        shutdown_task = asyncio.create_task(
            shutdown_requested.wait(),
            name="opennosh-publication-shutdown",
        )
        done, _ = await asyncio.wait(
            (drain_task, shutdown_task),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if shutdown_task in done:
            driver.stop_claiming()
            async with asyncio.timeout(drain_timeout_seconds):
                await drain_task
        else:
            await drain_task
            raise RuntimeError("Publication claims loop exited before shutdown")
    finally:
        pending = [
            task
            for task in (drain_task, shutdown_task)
            if task is not None and not task.done()
        ]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await driver.close()


async def process_publication_wakeup(
    orchestrator: PublicationOrchestrator,
    job: Job,
) -> None:
    message = decode_message(job.payload)
    try:
        await orchestrator.process(
            message,
            queue_job_id=int(job.id),
        )
    except asyncio.CancelledError as error:
        # PgQueuer otherwise records an interrupted picked job as canceled. Turn
        # graceful-drain cancellation into a retry that runs after any lease held
        # by this process has expired.
        raise RetryRequested(
            delay=PUBLICATION_FAILURE_RETRY_DELAY,
            reason="publication recovery after worker cancellation",
        ) from error
    except Exception as error:
        logger.exception(
            "Publication wake-up failed unexpectedly",
            extra={"queue_job_id": int(job.id), "attempts": int(job.attempts)},
        )
        if int(job.attempts) >= PUBLICATION_MAX_UNEXPECTED_ATTEMPTS:
            raise
        raise RetryRequested(
            delay=PUBLICATION_FAILURE_RETRY_DELAY,
            reason=f"publication recovery after {type(error).__name__}",
        ) from error


async def create_publication_role_driver(
    settings: Settings | None = None,
    adapters: PublicationAdapterRegistry | None = None,
) -> PgQueuerRoleDriver:
    configured = settings or get_settings()
    resolved_adapters = adapters or {}
    prepared: PreparedProductionPublicationRuntime | None = None
    if configured.app_environment == "production":
        if adapters is not None:
            resolved_adapters = validate_production_adapter_registry(adapters)
        elif not getattr(configured, "publication_claims_enabled", False):
            resolved_adapters = validate_production_adapter_registry(None)
        else:
            prepared = await PreparedProductionPublicationRuntime.from_settings(
                configured,
                clock=lambda: datetime.now(UTC),
            )
            resolved_adapters = prepared.runtime.adapters
    try:
        manifest = load_capacity_manifest(configured.database_capacity_manifest_path)
        budget = manifest.active_role_budget(ProcessRole.PUBLICATION)
    except BaseException:
        if prepared is not None:
            await prepared.aclose()
        raise
    try:
        pool = await asyncpg.create_pool(
            dsn=asyncpg_dsn(configured.process_database_url(ProcessRole.PUBLICATION)),
            min_size=1,
            max_size=budget.pool_size,
            timeout=budget.acquisition_timeout_ms / 1000,
            server_settings={
                "application_name": (
                    f"opennosh:{manifest.deployment_id}:{ProcessRole.PUBLICATION.value}"[:63]
                ),
                "statement_timeout": str(budget.statement_timeout_ms),
            },
        )
    except BaseException:
        if prepared is not None:
            await prepared.aclose()
        raise
    if pool is None:
        if prepared is not None:
            await prepared.aclose()
        raise RuntimeError("asyncpg did not create the publication pool")
    try:
        if prepared is not None:
            resolved_adapters = prepared.bind_pool(pool)
        if (
            configured.app_environment == "production"
            and configured.publication_claims_enabled
        ):
            activation_id = configured.publication_activation_id
            if activation_id is None:
                raise RuntimeError("Publication claims require one activation intent")
            wakeup = await ensure_publication_activation_wakeup(
                pool,
                activation_id,
                now=datetime.now(UTC),
            )
            logger.warning(
                "Publication activation wake-up ready: outcome=%s state=%s "
                "revision=%s active_jobs=%s eligible=%s",
                wakeup.outcome.value,
                wakeup.state.value,
                wakeup.workflow_revision,
                wakeup.active_jobs,
                wakeup.eligible,
            )
            if wakeup.outcome is PublicationActivationWakeupOutcome.TERMINAL:
                raise RuntimeError(
                    "Configured publication activation is already terminal: "
                    f"{wakeup.state.value}"
                )
        return _assemble_publication_role_driver(
            configured=configured,
            adapters=resolved_adapters,
            prepared=prepared,
            pool=pool,
            budget=budget,
        )
    except BaseException:
        await pool.close()
        if prepared is not None:
            await prepared.aclose()
        raise


def _assemble_publication_role_driver(
    *,
    configured: Settings,
    adapters: PublicationAdapterRegistry,
    prepared: PreparedProductionPublicationRuntime | None,
    pool: Any,
    budget: RoleBudget,
) -> PgQueuerRoleDriver:
    driver = AsyncpgPoolDriver(pool)
    activation_id = (
        configured.publication_activation_id
        if configured.publication_claims_enabled
        else None
    )
    queue = PgQueuer(
        connection=driver,
        channel=Channel(PGQUEUER_SETTINGS.channel),
        queries=build_queries(driver, publication_activation_id=activation_id),
    )
    orchestrator = PublicationOrchestrator(
        PostgresPublicationRepository(pool),
        PublicationEffectExecutor(adapters),
        owner=f"publication:{queue.qm.queue_manager_id}",
    )

    @queue.entrypoint(
        PUBLICATION_ENTRYPOINT,
        concurrency_limit=max(1, budget.max_in_flight_database_sections),
        on_failure="hold",
    )
    async def publication_wakeup(job: Job) -> None:
        await process_publication_wakeup(orchestrator, job)

    return PgQueuerRoleDriver(
        queue,
        pool,
        worker_concurrency=budget.worker_concurrency,
        resource_closer=prepared.aclose if prepared is not None else None,
    )


def create_latest_pointer_refresh_service(settings: Settings) -> LatestPointerRefreshService:
    """Construct every refresh dependency before the first R2 write."""

    if not settings.latest_refresh_enabled:
        raise RuntimeError("Latest refresh construction requires refresh enabled")
    if (
        settings.public_artifact_base_url is None
        or settings.online_manifest_signing_key_id is None
        or settings.online_manifest_signing_key is None
        or settings.r2_account_id is None
        or settings.r2_bucket is None
        or settings.r2_access_key_id is None
        or settings.r2_secret_access_key is None
    ):
        raise RuntimeError("Refresh-only publication settings are incomplete")
    signing_key = load_production_signing_key(
        settings.online_manifest_signing_key,
        key_id=settings.online_manifest_signing_key_id,
    )
    return LatestPointerRefreshService(
        origin=HttpArtifactStore(
            settings.public_artifact_base_url,
            timeout_seconds=settings.public_artifact_timeout_seconds,
        ),
        writer=S3R2ObjectWriter(
            account_id=settings.r2_account_id,
            access_key_id=settings.r2_access_key_id.get_secret_value(),
            secret_access_key=settings.r2_secret_access_key.get_secret_value(),
        ),
        bucket=settings.r2_bucket,
        manifest_keys=ManifestKeyRing.from_config(settings.public_commons_verifying_keys),
        receipt_keys=PublicationReceiptKeyRing.from_json(
            settings.publication_receipt_verifying_keys.get_secret_value()
        ),
        signing_key_id=settings.online_manifest_signing_key_id,
        signing_key=signing_key,
        refresh_after_seconds=settings.latest_refresh_after_seconds,
        pointer_lifetime_seconds=settings.latest_pointer_lifetime_seconds,
        origin_timeout_seconds=settings.public_artifact_timeout_seconds,
    )


async def _run_publication_worker(
    adapters: PublicationAdapterRegistry | None = None,
    *,
    settings: Settings | None = None,
    refresh_service: LatestPointerRefreshService | None = None,
    shutdown_requested: asyncio.Event | None = None,
) -> None:
    configured = settings or get_settings()
    if not configured.latest_refresh_enabled and not configured.publication_claims_enabled:
        raise RuntimeError("Publication worker requires an enabled runtime mode")
    if getattr(configured, "publication_preactivation_smoke_enabled", False):
        steps = await run_zero_claim_preactivation_smoke(
            configured,
            clock=lambda: datetime.now(UTC),
        )
        logger.warning(
            "Zero-claim publication preactivation smoke passed "
            "adapter_count=%d steps=%s claims_enabled=false",
            len(steps),
            ",".join(steps),
        )
    shutdown = shutdown_requested or asyncio.Event()
    loop = asyncio.get_running_loop()
    if shutdown_requested is None:
        for shutdown_signal in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(shutdown_signal, shutdown.set)

    driver = None
    service = None
    if configured.latest_refresh_enabled:
        service = refresh_service or create_latest_pointer_refresh_service(configured)
    try:
        if configured.publication_claims_enabled:
            driver = await create_publication_role_driver(
                settings=configured,
                adapters=adapters,
            )
    except BaseException:
        if service is not None:
            await service.aclose()
        raise

    async with asyncio.TaskGroup() as tasks:
        if driver is not None:
            tasks.create_task(
                supervise_publication_claims(
                    driver,
                    shutdown,
                    drain_timeout_seconds=PUBLICATION_DRAIN_TIMEOUT_SECONDS,
                ),
                name="opennosh-publication-claims",
            )
        if service is not None:
            tasks.create_task(
                run_latest_pointer_refresh_loop(
                    service,
                    shutdown,
                    interval_seconds=configured.latest_refresh_interval_seconds,
                ),
                name="opennosh-latest-pointer-refresh",
            )


def run_publication_worker(
    adapters: PublicationAdapterRegistry | None = None,
) -> int:
    asyncio.run(_run_publication_worker(adapters))
    return 0
