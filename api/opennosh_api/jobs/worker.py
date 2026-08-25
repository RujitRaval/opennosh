from __future__ import annotations

import asyncio
import signal
from typing import Any

import asyncpg  # type: ignore[import-untyped]
from pgqueuer import PgQueuer
from pgqueuer.db import AsyncpgPoolDriver
from pgqueuer.models import Channel
from sqlalchemy.engine import make_url

from opennosh_api.capacity import ProcessRole, load_capacity_manifest
from opennosh_api.jobs.pgqueuer import PGQUEUER_SETTINGS, build_queries
from opennosh_api.runtime import supervise_role
from opennosh_api.settings import Settings, get_settings

PUBLICATION_DRAIN_TIMEOUT_SECONDS = 30.0
PGQUEUER_HEARTBEAT_TIMEOUT_SECONDS = 30.0


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
    ) -> None:
        if worker_concurrency < 2:
            raise ValueError("PgQueuer worker concurrency must be at least two")
        self._queue = queue
        self._pool = pool
        self._worker_concurrency = worker_concurrency
        self._batch_size = min(10, worker_concurrency // 2)
        self._run_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        await self._queue.qm.verify_structure()
        self._run_task = asyncio.create_task(
            self._queue.run(
                batch_size=self._batch_size,
                max_concurrent_tasks=self._worker_concurrency,
            ),
            name="opennosh-publication-pgqueuer",
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


async def create_publication_role_driver(
    settings: Settings | None = None,
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
