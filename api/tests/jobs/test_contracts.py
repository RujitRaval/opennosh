from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from uuid import uuid4

import pytest
from opennosh_api.jobs import (
    JobAttemptContext,
    JobEnqueueResult,
    JobLane,
    JobMessage,
    JobQueue,
    JobQueueHealth,
    JobRequest,
    JobTraceContext,
)
from opennosh_api.jobs.pgqueuer import decode_message, encode_message
from opennosh_api.jobs.worker import PgQueuerRoleDriver, asyncpg_dsn
from pydantic import ValidationError
from sqlalchemy.engine import URL, make_url


def message() -> JobMessage:
    return JobMessage(
        lane=JobLane.PUBLICATION,
        job_type="publication.wake",
        subject_id=uuid4(),
        idempotency_key="publication-intent-0001",
        trace=JobTraceContext(
            trace_id="0123456789abcdef",
            request_id="request-1",
        ),
    )


def test_job_payload_is_typed_minimal_and_deterministic() -> None:
    first = message()
    encoded = encode_message(first)

    assert encode_message(decode_message(encoded)) == encoded
    assert set(first.model_dump()) == {
        "schema_version",
        "lane",
        "job_type",
        "subject_id",
        "idempotency_key",
        "workflow_revision",
        "trace",
    }
    assert b"approved_payload" not in encoded
    assert b"authority" not in encoded
    assert b"secret" not in encoded


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("idempotency_key", "publication key with spaces"),
        ("idempotency_key", "publication-key-☃"),
    ],
)
def test_job_message_rejects_unsafe_idempotency_keys(field: str, value: str) -> None:
    payload = message().model_dump()
    payload[field] = value

    with pytest.raises(ValidationError):
        JobMessage.model_validate(payload)


def test_evidence_job_is_typed_and_lane_bound() -> None:
    evidence = JobMessage(
        lane=JobLane.EVIDENCE,
        job_type="evidence.preserve",
        subject_id=uuid4(),
        idempotency_key="evidence-preserve:fixture",
    )

    assert evidence.lane is JobLane.EVIDENCE
    with pytest.raises(ValidationError, match="does not belong"):
        JobMessage(
            lane=JobLane.EVIDENCE,
            job_type="publication.wake",
            subject_id=uuid4(),
            idempotency_key="evidence-preserve:wrong-lane",
        )


def test_job_request_and_attempt_context_require_aware_times() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        JobRequest(
            message=message(),
            run_after=datetime(2026, 8, 25),
            deduplication_key="publication-dedupe-0001",
        )

    with pytest.raises(ValidationError, match="timezone"):
        JobAttemptContext(
            queue_job_id=1,
            delivery_count=1,
            picked_at=datetime(2026, 8, 25),
        )


class ReplacementQueue:
    async def enqueue(self, connection: Any, request: JobRequest) -> JobEnqueueResult:
        del connection, request
        return JobEnqueueResult(job_id=7, enqueued=True)

    async def cancel(self, connection: Any, job_id: int) -> None:
        del connection, job_id

    async def health(self, connection: Any) -> JobQueueHealth:
        del connection
        return JobQueueHealth(
            adapter="replacement:1",
            schema_version="1.0",
            healthy=True,
            queued=0,
            eligible=0,
        )


def test_job_queue_port_accepts_a_pgqueuer_independent_adapter() -> None:
    assert isinstance(ReplacementQueue(), JobQueue)


def test_asyncpg_dsn_preserves_escaped_credentials() -> None:
    source = URL.create(
        "postgresql+asyncpg",
        username="worker",
        password="p@ss",
        host="database.example",
        port=5432,
        database="opennosh",
    ).render_as_string(hide_password=False)
    converted = make_url(asyncpg_dsn(source))
    assert (
        converted.drivername,
        converted.username,
        converted.password,
        converted.host,
        converted.port,
        converted.database,
    ) == ("postgresql", "worker", "p@ss", "database.example", 5432, "opennosh")

    with pytest.raises(ValueError, match="PostgreSQL"):
        asyncpg_dsn("sqlite+aiosqlite:///tmp/opennosh.db")


class FakeQueueManager:
    def __init__(self, actions: list[str]) -> None:
        self.actions = actions

    async def verify_structure(self) -> None:
        self.actions.append("verify")


class FakePgQueuer:
    def __init__(self, actions: list[str]) -> None:
        self.actions = actions
        self.shutdown = asyncio.Event()
        self.qm = FakeQueueManager(actions)

    async def run(self, *, batch_size: int, max_concurrent_tasks: int) -> None:
        self.actions.append(f"run:{max_concurrent_tasks}:batch:{batch_size}")
        await self.shutdown.wait()
        self.actions.append("drained")


class FakePool:
    def __init__(self, actions: list[str]) -> None:
        self.actions = actions

    async def close(self) -> None:
        self.actions.append("closed")


@pytest.mark.asyncio
async def test_pgqueuer_role_driver_verifies_stops_drains_and_closes() -> None:
    actions: list[str] = []
    driver = PgQueuerRoleDriver(
        FakePgQueuer(actions),  # type: ignore[arg-type]
        FakePool(actions),
        worker_concurrency=3,
    )

    await driver.start()
    driver.stop_claiming()
    await driver.drain()
    await driver.close()

    assert actions == ["verify", "run:3:batch:1", "drained", "closed"]
