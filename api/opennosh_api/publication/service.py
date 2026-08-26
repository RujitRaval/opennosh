from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.jobs import JobLane, JobMessage, JobQueue, JobRequest, JobTraceContext
from opennosh_api.publication.models import PublicationIntent


class PublicationIntentConflictError(RuntimeError):
    pass


class CreatePublicationIntent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_draft_id: UUID
    source_draft_version: int = Field(gt=0)
    reviewed_decision_id: UUID
    approving_actor_id: UUID
    pack_id: str = Field(min_length=1, max_length=160)
    record_id: str = Field(min_length=1, max_length=160)
    approved_payload_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_base_commit: str = Field(pattern=r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
    required_checks: tuple[str, ...] = Field(min_length=1, max_length=32)
    forge_target: str = Field(min_length=1, max_length=512)
    idempotency_key: str = Field(min_length=16, max_length=255)
    trace: JobTraceContext = Field(default_factory=JobTraceContext)


def _matches_existing_intent(intent: PublicationIntent, command: CreatePublicationIntent) -> bool:
    return (
        intent.source_draft_id == command.source_draft_id
        and intent.source_draft_version == command.source_draft_version
        and intent.reviewed_decision_id == command.reviewed_decision_id
        and intent.approving_actor_id == command.approving_actor_id
        and intent.pack_id == command.pack_id
        and intent.record_id == command.record_id
        and intent.approved_payload_digest == command.approved_payload_digest
        and intent.expected_base_commit == command.expected_base_commit
        and intent.required_checks_json == list(command.required_checks)
        and intent.forge_target == command.forge_target
    )


def _idempotency_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def create_publication_intent(
    session: AsyncSession,
    queue: JobQueue,
    command: CreatePublicationIntent,
    *,
    now: datetime,
    id_generator: Callable[[], UUID] = uuid4,
) -> PublicationIntent:
    """Persist the reviewed intent and queue wake-up in the caller's transaction."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Publication time must include a timezone")

    intent_id = id_generator()
    key_hash = _idempotency_hash(command.idempotency_key)
    values = {
        "id": intent_id,
        "source_draft_id": command.source_draft_id,
        "source_draft_version": command.source_draft_version,
        "reviewed_decision_id": command.reviewed_decision_id,
        "approving_actor_id": command.approving_actor_id,
        "pack_id": command.pack_id,
        "record_id": command.record_id,
        "approved_payload_digest": command.approved_payload_digest,
        "expected_base_commit": command.expected_base_commit,
        "required_checks_json": list(command.required_checks),
        "forge_target": command.forge_target,
        "idempotency_key_hash": key_hash,
        "next_attempt_at": now,
    }
    created_id = await session.scalar(
        insert(PublicationIntent)
        .values(**values)
        .on_conflict_do_nothing()
        .returning(PublicationIntent.id)
    )
    if created_id is None:
        existing = await session.scalar(
            select(PublicationIntent).where(PublicationIntent.idempotency_key_hash == key_hash)
        )
        if existing is None:
            raise PublicationIntentConflictError(
                "Draft version already has a different publication intent"
            )
        if not _matches_existing_intent(existing, command):
            raise PublicationIntentConflictError(
                "Idempotency key is already bound to a different publication intent"
            )
        intent = existing
    else:
        created_intent = await session.get(PublicationIntent, created_id)
        if created_intent is None:
            raise RuntimeError("Inserted publication intent could not be reloaded")
        intent = created_intent

    connection = await session.connection()
    await queue.enqueue(
        connection,
        JobRequest(
            message=JobMessage(
                lane=JobLane.PUBLICATION,
                job_type="publication.wake",
                subject_id=intent.id,
                idempotency_key=command.idempotency_key,
                workflow_revision=intent.workflow_revision,
                trace=command.trace,
            ),
            run_after=intent.next_attempt_at,
            priority=0,
            deduplication_key=f"publication:{key_hash}",
        ),
    )
    return intent
