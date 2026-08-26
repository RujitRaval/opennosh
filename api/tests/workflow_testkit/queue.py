from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime

from opennosh_api.jobs import JobEnqueueResult, JobQueueHealth, JobRequest
from sqlalchemy.ext.asyncio import AsyncConnection


@dataclass(frozen=True, slots=True)
class PersistentQueuedJob:
    job_id: int
    request: JobRequest
    cancelled: bool = False


@dataclass(frozen=True, slots=True)
class PersistentQueueSnapshot:
    next_job_id: int
    jobs: tuple[PersistentQueuedJob, ...]


class PersistentJobQueue:
    """Contract-faithful in-memory JobQueue with state outside worker instances."""

    schema_version = "1.0"

    def __init__(self, clock: Callable[[], datetime]) -> None:
        self._clock = clock
        self._next_job_id = 1
        self._jobs: dict[int, PersistentQueuedJob] = {}

    async def enqueue(self, connection: AsyncConnection, request: JobRequest) -> JobEnqueueResult:
        del connection
        duplicate = next(
            (
                job
                for job in self._jobs.values()
                if not job.cancelled and job.request.deduplication_key == request.deduplication_key
            ),
            None,
        )
        if duplicate is not None:
            return JobEnqueueResult(job_id=None, enqueued=False)
        job = PersistentQueuedJob(job_id=self._next_job_id, request=request)
        self._jobs[job.job_id] = job
        self._next_job_id += 1
        return JobEnqueueResult(job_id=job.job_id, enqueued=True)

    async def cancel(self, connection: AsyncConnection, job_id: int) -> None:
        del connection
        job = self._jobs.get(job_id)
        if job is not None:
            self._jobs[job_id] = replace(job, cancelled=True)

    async def health(self, connection: AsyncConnection) -> JobQueueHealth:
        del connection
        now = self._clock()
        active = tuple(job for job in self._jobs.values() if not job.cancelled)
        return JobQueueHealth(
            adapter="testkit-persistent-queue:1",
            schema_version=self.schema_version,
            healthy=True,
            queued=len(active),
            eligible=sum(job.request.run_after <= now for job in active),
        )

    def snapshot(self) -> PersistentQueueSnapshot:
        return PersistentQueueSnapshot(
            next_job_id=self._next_job_id,
            jobs=tuple(self._jobs.values()),
        )

    def restore(self, snapshot: PersistentQueueSnapshot) -> None:
        self._next_job_id = snapshot.next_job_id
        self._jobs = {job.job_id: job for job in snapshot.jobs}

    @property
    def jobs(self) -> tuple[PersistentQueuedJob, ...]:
        return tuple(self._jobs.values())
