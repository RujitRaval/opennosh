from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from opennosh_api.orm import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class PublicationIntent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "publication_intents"
    __table_args__ = (
        CheckConstraint("schema_version = '1.0'", name="schema_version_supported"),
        CheckConstraint("workflow_version = '1.0'", name="workflow_version_supported"),
        CheckConstraint("source_draft_version > 0", name="source_draft_version_positive"),
        CheckConstraint(
            "state IN ('pending', 'running', 'retrying', 'blocked', 'failed', 'published')",
            name="state_allowed",
        ),
        CheckConstraint(
            "approved_payload_digest ~ '^[0-9a-f]{64}$'",
            name="approved_payload_digest_sha256",
        ),
        CheckConstraint(
            "expected_base_commit ~ '^[0-9a-f]{40}([0-9a-f]{24})?$'",
            name="expected_base_commit_hash",
        ),
        CheckConstraint(
            "idempotency_key_hash ~ '^[0-9a-f]{64}$'",
            name="idempotency_key_hash_sha256",
        ),
        UniqueConstraint(
            "source_draft_id",
            "source_draft_version",
            name="uq_publication_intents_source_draft_version",
        ),
        Index(
            "ix_publication_intents_claim",
            "state",
            "next_attempt_at",
            "id",
            postgresql_where=text("state IN ('pending', 'retrying')"),
        ),
    )

    source_draft_id: Mapped[UUID] = mapped_column(
        ForeignKey("contribution_drafts.id", ondelete="RESTRICT"), nullable=False
    )
    source_draft_version: Mapped[int] = mapped_column(Integer, nullable=False)
    reviewed_decision_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    approving_actor_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, server_default="1.0")
    workflow_version: Mapped[str] = mapped_column(String(16), nullable=False, server_default="1.0")
    state: Mapped[str] = mapped_column(String(32), nullable=False, server_default="pending")
    pack_id: Mapped[str] = mapped_column(String(160), nullable=False)
    record_id: Mapped[str] = mapped_column(String(160), nullable=False)
    approved_payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_base_commit: Mapped[str] = mapped_column(String(64), nullable=False)
    required_checks_json: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    forge_target: Mapped[str] = mapped_column(String(512), nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_failure_code: Mapped[str | None] = mapped_column(String(120))
    last_failure_context_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PublicationStep(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "publication_steps"
    __table_args__ = (
        CheckConstraint("workflow_version = '1.0'", name="workflow_version_supported"),
        CheckConstraint("step_version > 0", name="step_version_positive"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_non_negative"),
        CheckConstraint(
            "state IN ('pending', 'leased', 'retrying', 'blocked', 'failed', 'verified')",
            name="state_allowed",
        ),
        UniqueConstraint(
            "publication_intent_id",
            "step_name",
            "step_version",
            name="uq_publication_steps_intent_name_version",
        ),
        Index(
            "ix_publication_steps_claim",
            "state",
            "next_attempt_at",
            "lease_expires_at",
            "id",
            postgresql_where=text("state IN ('pending', 'retrying', 'leased')"),
        ),
    )

    publication_intent_id: Mapped[UUID] = mapped_column(
        ForeignKey("publication_intents.id", ondelete="CASCADE"), nullable=False
    )
    workflow_version: Mapped[str] = mapped_column(String(16), nullable=False, server_default="1.0")
    step_name: Mapped[str] = mapped_column(String(120), nullable=False)
    step_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    state: Mapped[str] = mapped_column(String(32), nullable=False, server_default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    queue_job_id: Mapped[int | None] = mapped_column(BigInteger)
    lease_token: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), unique=True)
    lease_owner: Mapped[str | None] = mapped_column(String(160))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    input_digest: Mapped[str | None] = mapped_column(String(64))
    observation_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    failure_code: Mapped[str | None] = mapped_column(String(120))
    failure_context_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DurableAcknowledgement(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "publication_durable_acknowledgements"
    __table_args__ = (
        CheckConstraint("schema_version = '1.0'", name="schema_v1"),
        CheckConstraint("content_digest ~ '^[0-9a-f]{64}$'", name="content_digest_sha256"),
        UniqueConstraint(
            "publication_intent_id",
            "acknowledgement_kind",
            "destination",
            name="uq_publication_acknowledgements_intent_kind_destination",
        ),
    )

    publication_intent_id: Mapped[UUID] = mapped_column(
        ForeignKey("publication_intents.id", ondelete="CASCADE"), nullable=False
    )
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, server_default="1.0")
    acknowledgement_kind: Mapped[str] = mapped_column(String(80), nullable=False)
    destination: Mapped[str] = mapped_column(String(512), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    external_reference: Mapped[str | None] = mapped_column(String(1024))
    context_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AcceptedEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "accepted_events"
    __table_args__ = (
        CheckConstraint("schema_version = '1.0'", name="schema_version_supported"),
        CheckConstraint(
            "commit_sha ~ '^[0-9a-f]{40}([0-9a-f]{24})?$'",
            name="commit_sha_hash",
        ),
        CheckConstraint(
            "receipt_digest IS NULL OR receipt_digest ~ '^[0-9a-f]{64}$'",
            name="receipt_digest_sha256",
        ),
        UniqueConstraint(
            "repository",
            "commit_sha",
            "pack_id",
            "record_id",
            name="uq_accepted_events_canonical_record",
        ),
        Index("ix_accepted_events_published_type", "published_at", "event_type"),
    )

    publication_intent_id: Mapped[UUID] = mapped_column(
        ForeignKey("publication_intents.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, server_default="1.0")
    repository: Mapped[str] = mapped_column(String(512), nullable=False)
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    pack_id: Mapped[str] = mapped_column(String(160), nullable=False)
    record_id: Mapped[str] = mapped_column(String(160), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    receipt_digest: Mapped[str | None] = mapped_column(String(64))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
