from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import cast

import asyncpg  # type: ignore[import-untyped]
from pgqueuer.adapters.persistence.qb import (
    DBSettings,
    QueryBuilderEnvironment,
    QueryQueueBuilder,
    QuerySchedulerBuilder,
)
from pgqueuer.db import AsyncpgDriver, Driver
from pgqueuer.models import JobId
from pgqueuer.queries import Queries
from sqlalchemy.ext.asyncio import AsyncConnection

from opennosh_api.jobs.contracts import (
    JobEnqueueResult,
    JobLane,
    JobMessage,
    JobQueueHealth,
    JobRequest,
)

PGQUEUER_ADAPTER_VERSION = "1.3.2"
PGQUEUER_SCHEMA_VERSION = "1.0"
PGQUEUER_PREFIX = "opennosh_"
PGQUEUER_SETTINGS = DBSettings(prefix=PGQUEUER_PREFIX)
PUBLICATION_ENTRYPOINT = "opennosh.publication.wake.v1"


def build_queries(driver: Driver) -> Queries:
    """Bind every PgQueuer query family to the reviewed OpenNosh namespace."""

    return Queries(
        driver,
        qbe=QueryBuilderEnvironment(PGQUEUER_SETTINGS),
        qbq=QueryQueueBuilder(PGQUEUER_SETTINGS),
        qbs=QuerySchedulerBuilder(PGQUEUER_SETTINGS),
    )


async def _queries_for_connection(connection: AsyncConnection) -> Queries:
    """Use SQLAlchemy's checked-out asyncpg connection without owning its lifecycle."""

    raw_connection = await connection.get_raw_connection()
    driver_connection = cast(asyncpg.Connection, raw_connection.driver_connection)
    return build_queries(AsyncpgDriver(driver_connection))


def encode_message(message: JobMessage) -> bytes:
    return json.dumps(
        message.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def decode_message(payload: bytes | None) -> JobMessage:
    if payload is None:
        raise ValueError("OpenNosh jobs require a typed payload")
    return JobMessage.model_validate_json(payload)


class PgQueuerJobQueue:
    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))

    async def enqueue(self, connection: AsyncConnection, request: JobRequest) -> JobEnqueueResult:
        if request.message.lane is not JobLane.PUBLICATION:
            raise ValueError(f"Unsupported PgQueuer lane: {request.message.lane.value}")
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Queue clock must return a timezone-aware datetime")
        delay = max(request.run_after - now, timedelta())
        queries = await _queries_for_connection(connection)
        (job_id,) = await queries.enqueue(
            PUBLICATION_ENTRYPOINT,
            encode_message(request.message),
            priority=request.priority,
            execute_after=delay,
            dedupe_key=request.deduplication_key,
            headers={
                key: value
                for key, value in {
                    "trace_id": request.message.trace.trace_id,
                    "request_id": request.message.trace.request_id,
                    "schema_version": request.message.schema_version,
                }.items()
                if value is not None
            },
            on_conflict="skip",
        )
        return JobEnqueueResult(
            job_id=int(job_id) if job_id is not None else None,
            enqueued=job_id is not None,
        )

    async def cancel(self, connection: AsyncConnection, job_id: int) -> None:
        queries = await _queries_for_connection(connection)
        await queries.mark_job_as_cancelled([JobId(job_id)])

    async def health(self, connection: AsyncConnection) -> JobQueueHealth:
        queries = await _queries_for_connection(connection)
        expected_tables = (
            PGQUEUER_SETTINGS.queue_table,
            PGQUEUER_SETTINGS.queue_table_log,
            PGQUEUER_SETTINGS.statistics_table,
            PGQUEUER_SETTINGS.schedules_table,
        )
        structures = [await queries.has_table(table) for table in expected_tables]
        structures.extend(
            (
                await queries.has_function(PGQUEUER_SETTINGS.function),
                await queries.has_trigger(PGQUEUER_SETTINGS.trigger),
            )
        )
        queued = await queries.queued_work([PUBLICATION_ENTRYPOINT])
        eligible = await queries.eligible_queued_work([PUBLICATION_ENTRYPOINT])
        return JobQueueHealth(
            adapter=f"pgqueuer:{PGQUEUER_ADAPTER_VERSION}",
            schema_version=PGQUEUER_SCHEMA_VERSION,
            healthy=all(structures),
            queued=queued,
            eligible=eligible,
        )
