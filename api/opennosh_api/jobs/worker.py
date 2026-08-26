from __future__ import annotations

import asyncio
import signal
from datetime import timedelta
from typing import Any

import asyncpg  # type: ignore[import-untyped]
from pgqueuer import PgQueuer
from pgqueuer.db import AsyncpgPoolDriver
from pgqueuer.errors import RetryRequested
from pgqueuer.models import Channel, Job
from sqlalchemy.engine import make_url

from opennosh_api.capacity import ProcessRole, load_capacity_manifest
from opennosh_api.jobs.pgqueuer import (
    PGQUEUER_SETTINGS,
    PUBLICATION_ENTRYPOINT,
    build_queries,
    decode_message,
)
from opennosh_api.publication.adapters import PublicationAdapterRegistry
from opennosh_api.publication.executor import PublicationEffectExecutor
from opennosh_api.publication.orchestrator import PublicationOrchestrator
from opennosh_api.publication.repository import PostgresPublicationRepository
from opennosh_api.runtime import supervise_role
from opennosh_api.settings import Settings, get_settings

PUBLICATION_DRAIN_TIMEOUT_SECONDS = 30.0
PGQUEUER_HEARTBEAT_TIMEOUT_SECONDS = 30.0
PUBLICATION_FAILURE_RETRY_DELAY = timedelta(seconds=65)
PUBLICATION_MAX_UNEXPECTED_ATTEMPTS = 5


def asyncpg_dsn(database_url: str) -> str:
    """Translate the SQLAlchemy async URL without losing escaped credentials."""

    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        raise ValueError("PgQueuer requires a PostgreSQL database URL")
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


class PgQueuerRoleDriver:
    """Drainable runtime wrapper; PgQueuer owns delivery, not domain state."""

    def __init__(
        self,
        queue: PgQueuer,
        pool: Any,
        *,
        worker_concurrency: int,
        task_name: str = "opennosh-publication-pgqueuer",
    ) -> None:
        if worker_concurrency < 2:
            raise ValueError("PgQueuer worker concurrency must be at least two")
        self._queue = queue
        self._pool = pool
        self._worker_concurrency = worker_concurrency
        self._task_name = task_name
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
        await self._pool.close()


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
    manifest = load_capacity_manifest(configured.database_capacity_manifest_path)
    budget = manifest.active_role_budget(ProcessRole.PUBLICATION)
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
    if pool is None:
        raise RuntimeError("asyncpg did not create the publication pool")
    driver = AsyncpgPoolDriver(pool)
    queue = PgQueuer(
        connection=driver,
        channel=Channel(PGQUEUER_SETTINGS.channel),
        queries=build_queries(driver),
    )
    orchestrator = PublicationOrchestrator(
        PostgresPublicationRepository(pool),
        PublicationEffectExecutor(adapters or {}),
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
    )


async def _run_publication_worker() -> None:
    driver = await create_publication_role_driver()
    shutdown_requested = asyncio.Event()
    loop = asyncio.get_running_loop()
    for shutdown_signal in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(shutdown_signal, shutdown_requested.set)
    await supervise_role(
        driver,
        shutdown_requested,
        drain_timeout_seconds=PUBLICATION_DRAIN_TIMEOUT_SECONDS,
    )


def run_publication_worker() -> int:
    asyncio.run(_run_publication_worker())
    return 0
