from __future__ import annotations

import asyncio
import hashlib
import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Protocol

from fastapi import Request
from sqlalchemy import event, text
from sqlalchemy.exc import TimeoutError as SqlAlchemyTimeoutError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from opennosh_api.capacity import ConnectionBudget, JobRole, load_capacity_manifest
from opennosh_api.problems import ProblemCode, RecoveryAction
from opennosh_api.problems.handlers import ProblemException


class DatabaseHealthProbe(Protocol):
    async def check(self) -> None: ...


@dataclass(frozen=True)
class DatabaseIdentity:
    deployment_id: str
    role: str

    @property
    def application_name(self) -> str:
        prefix = "opennosh:"
        suffix = f":{self.role}"
        value = f"{prefix}{self.deployment_id}{suffix}"
        if len(value) <= 63:
            return value
        digest = hashlib.sha256(self.deployment_id.encode("utf-8")).hexdigest()[:8]
        deployment_length = 63 - len(prefix) - len(suffix) - len(digest) - 1
        shortened = f"{self.deployment_id[:deployment_length]}-{digest}"
        return f"{prefix}{shortened}{suffix}"


class DatabasePoolMetrics:
    def __init__(self, identity: DatabaseIdentity, pool_size: int) -> None:
        self.identity = identity
        self.pool_size = pool_size
        self._lock = threading.Lock()
        self._active = 0
        self._idle = 0
        self._waiting = 0
        self._timed_out_total = 0
        self._acquisition_count = 0
        self._acquisition_latency_ms_total = 0.0
        self._acquisition_latency_ms_max = 0.0

    def begin_acquisition(self) -> float:
        with self._lock:
            self._waiting += 1
        return monotonic()

    def acquired(self, started_at: float) -> None:
        elapsed_ms = (monotonic() - started_at) * 1000
        with self._lock:
            self._waiting = max(0, self._waiting - 1)
            self._acquisition_count += 1
            self._acquisition_latency_ms_total += elapsed_ms
            self._acquisition_latency_ms_max = max(self._acquisition_latency_ms_max, elapsed_ms)

    def acquisition_failed(self, *, timed_out: bool) -> None:
        with self._lock:
            self._waiting = max(0, self._waiting - 1)
            if timed_out:
                self._timed_out_total += 1

    def checked_out(self) -> None:
        with self._lock:
            self._active += 1
            self._idle = max(0, self._idle - 1)

    def checked_in(self, *, returned_to_pool: bool = True) -> None:
        with self._lock:
            self._active = max(0, self._active - 1)
            if returned_to_pool:
                self._idle = min(self.pool_size, self._idle + 1)

    def snapshot(self) -> dict[str, str | int | float]:
        with self._lock:
            average = (
                self._acquisition_latency_ms_total / self._acquisition_count
                if self._acquisition_count
                else 0.0
            )
            return {
                "deployment_id": self.identity.deployment_id,
                "role": self.identity.role,
                "pool_size": self.pool_size,
                "active": self._active,
                "idle": self._idle,
                "waiting": self._waiting,
                "timed_out_total": self._timed_out_total,
                "acquisition_count": self._acquisition_count,
                "acquisition_latency_ms_average": round(average, 3),
                "acquisition_latency_ms_max": round(self._acquisition_latency_ms_max, 3),
            }


class SqlAlchemyHealthProbe:
    def __init__(self, engine: AsyncEngine, timeout_seconds: float) -> None:
        self._engine = engine
        self._timeout_seconds = timeout_seconds

    async def check(self) -> None:
        async with asyncio.timeout(self._timeout_seconds):
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))


def build_engine(
    database_url: str,
    *,
    identity: DatabaseIdentity,
    budget: ConnectionBudget,
    metrics: DatabasePoolMetrics | None = None,
) -> AsyncEngine:
    engine = create_async_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=budget.pool_size,
        max_overflow=budget.max_overflow,
        pool_timeout=budget.acquisition_timeout_ms / 1000,
        connect_args={
            "server_settings": {
                "application_name": identity.application_name,
                "statement_timeout": str(budget.statement_timeout_ms),
            }
        },
    )

    if metrics is not None:

        def checked_out(*_args: object) -> None:
            metrics.checked_out()

        def checked_in(dbapi_connection: object | None, *_args: object) -> None:
            metrics.checked_in(returned_to_pool=dbapi_connection is not None)

        event.listen(engine.sync_engine, "checkout", checked_out)
        event.listen(engine.sync_engine, "checkin", checked_in)
    return engine


def build_administration_engine(
    database_url: str, *, manifest_path: str | Path | None = None
) -> AsyncEngine:
    manifest = load_capacity_manifest(manifest_path)
    budget = manifest.jobs[JobRole.ADMINISTRATION]
    return build_engine(
        database_url,
        identity=DatabaseIdentity(
            deployment_id=manifest.deployment_id,
            role=JobRole.ADMINISTRATION.value,
        ),
        budget=budget,
    )


async def get_database_probe(request: Request) -> AsyncIterator[DatabaseHealthProbe]:
    yield request.app.state.database_probe


async def get_database_session(request: Request) -> AsyncIterator[AsyncSession]:
    metrics: DatabasePoolMetrics = request.app.state.database_pool_metrics
    started_at = metrics.begin_acquisition()
    async with request.app.state.session_factory() as session:
        try:
            await session.connection()
        except SqlAlchemyTimeoutError as error:
            metrics.acquisition_failed(timed_out=True)
            raise ProblemException(
                status=503,
                code=ProblemCode.DATABASE_CAPACITY_EXHAUSTED,
                detail="Database capacity is temporarily full. Wait briefly and try again.",
                recovery_actions=(RecoveryAction(id="retry", label="Try again"),),
                retry_after=1,
            ) from error
        except BaseException:
            # Cancellation must clear the waiting gauge before it propagates.
            metrics.acquisition_failed(timed_out=False)
            raise
        metrics.acquired(started_at)
        yield session
