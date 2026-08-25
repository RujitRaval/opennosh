from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncConnection


class JobLane(StrEnum):
    PUBLICATION = "publication"


class JobTraceContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    trace_id: str | None = Field(default=None, min_length=16, max_length=64)
    request_id: str | None = Field(default=None, min_length=1, max_length=128)


class JobMessage(BaseModel):
    """Versioned wake-up payload. Authority decisions and record data stay in PostgreSQL."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    lane: JobLane
    job_type: Literal["publication.wake"]
    subject_id: UUID
    idempotency_key: str = Field(min_length=16, max_length=255)
    workflow_revision: int | None = Field(default=None, ge=0)
    trace: JobTraceContext = Field(default_factory=JobTraceContext)

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        if not value.isascii() or any(character.isspace() for character in value):
            raise ValueError("Job idempotency keys must be printable ASCII without spaces")
        return value


class JobRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    message: JobMessage
    run_after: datetime
    priority: int = Field(default=0, ge=-32_768, le=32_767)
    deduplication_key: str = Field(min_length=16, max_length=255)

    @field_validator("run_after")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Job run_after must include a timezone")
        return value

    @field_validator("deduplication_key")
    @classmethod
    def validate_deduplication_key(cls, value: str) -> str:
        if not value.isascii() or any(character.isspace() for character in value):
            raise ValueError("Job deduplication keys must be printable ASCII without spaces")
        return value


class JobAttemptContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    queue_job_id: int = Field(gt=0)
    delivery_count: int = Field(ge=1)
    picked_at: datetime

    @field_validator("picked_at")
    @classmethod
    def require_picked_at_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Job picked_at must include a timezone")
        return value


class JobEnqueueResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: int | None
    enqueued: bool


class JobQueueHealth(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    adapter: str
    schema_version: str
    healthy: bool
    queued: int = Field(ge=0)
    eligible: int = Field(ge=0)


@runtime_checkable
class JobQueue(Protocol):
    async def enqueue(
        self, connection: AsyncConnection, request: JobRequest
    ) -> JobEnqueueResult: ...

    async def cancel(self, connection: AsyncConnection, job_id: int) -> None: ...

    async def health(self, connection: AsyncConnection) -> JobQueueHealth: ...
