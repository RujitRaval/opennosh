from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import UUID

import pytest
from opennosh_api.jobs import JobLane, JobMessage
from opennosh_api.jobs.pgqueuer import encode_message
from opennosh_api.jobs.worker import (
    PUBLICATION_FAILURE_RETRY_DELAY,
    PUBLICATION_MAX_UNEXPECTED_ATTEMPTS,
    process_publication_wakeup,
)
from opennosh_api.publication.repository import DEFAULT_LEASE_DURATION
from pgqueuer.errors import RetryRequested


def job(*, attempts: int) -> SimpleNamespace:
    message = JobMessage(
        lane=JobLane.PUBLICATION,
        job_type="publication.wake",
        subject_id=UUID("11111111-1111-4111-8111-111111111111"),
        idempotency_key="publication-recovery-0001",
        workflow_revision=0,
    )
    return SimpleNamespace(payload=encode_message(message), id=17, attempts=attempts)


class FailingOrchestrator:
    async def process(self, message: JobMessage, *, queue_job_id: int) -> None:
        raise RuntimeError(f"failure:{message.subject_id}:{queue_job_id}")


class CancelledOrchestrator:
    async def process(self, message: JobMessage, *, queue_job_id: int) -> None:
        raise asyncio.CancelledError


@pytest.mark.asyncio
async def test_unexpected_failure_requeues_after_the_lease_expires() -> None:
    with pytest.raises(RetryRequested) as raised:
        await process_publication_wakeup(  # type: ignore[arg-type]
            FailingOrchestrator(),
            job(attempts=1),
        )
    assert raised.value.delay == PUBLICATION_FAILURE_RETRY_DELAY
    assert raised.value.delay > DEFAULT_LEASE_DURATION


@pytest.mark.asyncio
async def test_unexpected_failure_holds_after_bounded_attempts() -> None:
    with pytest.raises(RuntimeError, match="failure"):
        await process_publication_wakeup(  # type: ignore[arg-type]
            FailingOrchestrator(),
            job(attempts=PUBLICATION_MAX_UNEXPECTED_ATTEMPTS),
        )


@pytest.mark.asyncio
async def test_graceful_cancellation_requeues_after_the_lease_expires() -> None:
    with pytest.raises(RetryRequested) as raised:
        await process_publication_wakeup(  # type: ignore[arg-type]
            CancelledOrchestrator(),
            job(attempts=PUBLICATION_MAX_UNEXPECTED_ATTEMPTS),
        )
    assert raised.value.delay == PUBLICATION_FAILURE_RETRY_DELAY
    assert raised.value.delay > DEFAULT_LEASE_DURATION
