from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.evidence.contracts import EvidenceAcknowledgement
from opennosh_api.jobs import JobLane, JobMessage, JobQueue, JobRequest, JobTraceContext
from opennosh_api.publication.models import PublicationIntent, PublicationReceiptRecord
from opennosh_api.publication.receipts import ReceiptEventType


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
    event_type: ReceiptEventType = ReceiptEventType.PUBLICATION
    prior_receipt_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    evidence_manifest_digests: tuple[str, ...] = Field(min_length=1, max_length=128)
    evidence_acknowledgements: tuple[EvidenceAcknowledgement, ...] = Field(
        min_length=1, max_length=128
    )
    trace: JobTraceContext = Field(default_factory=JobTraceContext)

    @field_validator("evidence_manifest_digests")
    @classmethod
    def validate_evidence_manifest_digests(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("Evidence manifest digests must be sorted and unique")
        if any(len(item) != 64 or any(c not in "0123456789abcdef" for c in item) for item in value):
            raise ValueError("Evidence manifest digests must be lowercase SHA-256 values")
        return value

    @field_validator("evidence_acknowledgements")
    @classmethod
    def validate_evidence_acknowledgements(
        cls, value: tuple[EvidenceAcknowledgement, ...]
    ) -> tuple[EvidenceAcknowledgement, ...]:
        identities = tuple((item.kind.value, item.destination) for item in value)
        if identities != tuple(sorted(set(identities))):
            raise ValueError("Evidence acknowledgements must be sorted and unique by destination")
        return value

    @model_validator(mode="after")
    def validate_receipt_lineage(self) -> CreatePublicationIntent:
        if self.event_type is ReceiptEventType.PUBLICATION:
            if self.prior_receipt_digest is not None:
                raise ValueError("Initial publication cannot link a prior receipt")
        elif self.prior_receipt_digest is None:
            raise ValueError("Corrections and revocations require a prior receipt")
        acknowledged_manifests = {item.manifest_digest for item in self.evidence_acknowledgements}
        if acknowledged_manifests != set(self.evidence_manifest_digests):
            raise ValueError("Evidence acknowledgements must cover every evidence manifest")
        return self


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
        and intent.event_type == command.event_type.value
        and intent.prior_receipt_digest == command.prior_receipt_digest
        and intent.evidence_manifest_digests_json == list(command.evidence_manifest_digests)
        and _canonical_evidence_acknowledgements(intent.evidence_acknowledgements_json)
        == tuple(item.model_dump(mode="json") for item in command.evidence_acknowledgements)
    )


def _canonical_evidence_acknowledgements(
    values: list[dict[str, object]],
) -> tuple[dict[str, object], ...]:
    return tuple(
        EvidenceAcknowledgement.model_validate(value).model_dump(mode="json") for value in values
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

    if command.prior_receipt_digest is not None:
        prior = await session.scalar(
            select(PublicationReceiptRecord).where(
                PublicationReceiptRecord.receipt_digest == command.prior_receipt_digest
            )
        )
        if prior is None:
            raise PublicationIntentConflictError("Prior receipt does not exist")
        if prior.pack_id != command.pack_id or prior.record_id != command.record_id:
            raise PublicationIntentConflictError("Prior receipt belongs to a different record")

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
        "event_type": command.event_type.value,
        "prior_receipt_digest": command.prior_receipt_digest,
        "evidence_manifest_digests_json": list(command.evidence_manifest_digests),
        "evidence_acknowledgements_json": [
            item.model_dump(mode="json") for item in command.evidence_acknowledgements
        ],
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
