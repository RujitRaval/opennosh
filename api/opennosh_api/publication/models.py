from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
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
        CheckConstraint("workflow_revision >= 0", name="workflow_revision_non_negative"),
        CheckConstraint(
            "state IN ('pending', 'running', 'retrying', 'blocked', 'failed', 'published', "
            "'committed', 'signed', 'publish_blocked', 'publish_retrying', "
            "'quarantined')",
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
        CheckConstraint(
            "event_type IN ('publication', 'correction', 'revocation')",
            name="event_type_allowed",
        ),
        CheckConstraint(
            "(event_type = 'publication' AND prior_receipt_digest IS NULL) OR "
            "(event_type IN ('correction', 'revocation') AND "
            "prior_receipt_digest IS NOT NULL)",
            name="receipt_lineage_consistent",
        ),
        CheckConstraint(
            "prior_receipt_digest IS NULL OR prior_receipt_digest ~ '^[0-9a-f]{64}$'",
            name="prior_receipt_digest_sha256",
        ),
        CheckConstraint(
            "jsonb_typeof(evidence_manifest_digests_json) = 'array' AND "
            "jsonb_array_length(evidence_manifest_digests_json) BETWEEN 1 AND 128",
            name="evidence_manifest_digests_bounded",
        ),
        CheckConstraint(
            "jsonb_typeof(evidence_acknowledgements_json) = 'array' AND "
            "jsonb_array_length(evidence_acknowledgements_json) BETWEEN 1 AND 128",
            name="evidence_acknowledgements_bounded",
        ),
        ForeignKeyConstraint(
            ["prior_receipt_digest"],
            ["publication_receipts.receipt_digest"],
            name="fk_pub_intent_prior_receipt",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        CheckConstraint(
            "prior_publication_intent_id IS NULL OR prior_publication_intent_id != id",
            name="prior_publication_intent_not_self",
        ),
        UniqueConstraint(
            "prior_publication_intent_id",
            name="uq_publication_intent_successor",
        ),
        Index(
            "uq_publication_intent_initial_draft_version",
            "source_draft_id",
            "source_draft_version",
            unique=True,
            postgresql_where=text("prior_publication_intent_id IS NULL"),
        ),
        Index(
            "ix_publication_intents_claim",
            "state",
            "next_attempt_at",
            "id",
            postgresql_where=text("state IN ('pending', 'retrying', 'publish_retrying')"),
        ),
    )

    source_draft_id: Mapped[UUID] = mapped_column(
        ForeignKey("contribution_drafts.id", ondelete="RESTRICT"), nullable=False
    )
    prior_publication_intent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("publication_intents.id", ondelete="RESTRICT")
    )
    source_draft_version: Mapped[int] = mapped_column(Integer, nullable=False)
    reviewed_decision_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    approving_actor_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, server_default="1.0")
    workflow_version: Mapped[str] = mapped_column(String(16), nullable=False, server_default="1.0")
    workflow_revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
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
    event_type: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="publication"
    )
    prior_receipt_digest: Mapped[str | None] = mapped_column(String(64))
    evidence_manifest_digests_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    evidence_acknowledgements_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False
    )
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
        CheckConstraint("ordinal >= 0", name="ordinal_non_negative"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_non_negative"),
        CheckConstraint(
            "state IN ('pending', 'leased', 'retrying', 'blocked', 'failed', 'verified')",
            name="state_allowed",
        ),
        UniqueConstraint(
            "publication_intent_id",
            "step_name",
            "destination",
            "step_version",
            name="uq_publication_steps_intent_name_destination_version",
        ),
        UniqueConstraint(
            "publication_intent_id",
            "ordinal",
            name="uq_publication_steps_intent_ordinal",
        ),
        Index(
            "uq_publication_steps_one_lease_per_intent",
            "publication_intent_id",
            unique=True,
            postgresql_where=text("state = 'leased'"),
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
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    destination: Mapped[str] = mapped_column(String(512), nullable=False)
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


class PublicationReceiptRecord(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "publication_receipts"
    __table_args__ = (
        CheckConstraint("schema_version = '1.0'", name="schema_version_supported"),
        CheckConstraint("receipt_digest ~ '^[0-9a-f]{64}$'", name="receipt_digest_sha256"),
        CheckConstraint(
            "event_type IN ('publication', 'correction', 'revocation')",
            name="event_type_allowed",
        ),
        CheckConstraint(
            "(event_type = 'publication' AND prior_receipt_digest IS NULL) OR "
            "(event_type IN ('correction', 'revocation') AND "
            "prior_receipt_digest IS NOT NULL)",
            name="lineage_consistent",
        ),
        CheckConstraint(
            "prior_receipt_digest IS NULL OR prior_receipt_digest ~ '^[0-9a-f]{64}$'",
            name="prior_receipt_digest_sha256",
        ),
        Index("ix_publication_receipts_prior_digest", "prior_receipt_digest"),
        Index("ix_publication_receipts_pack_time", "pack_id", "published_at"),
    )

    publication_intent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("publication_intents.id", ondelete="RESTRICT"), unique=True
    )
    publication_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, unique=True)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    receipt_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    prior_receipt_digest: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("publication_receipts.receipt_digest", ondelete="RESTRICT"),
    )
    pack_id: Mapped[str] = mapped_column(String(160), nullable=False)
    record_id: Mapped[str] = mapped_column(String(160), nullable=False)
    envelope_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    signature_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    registry_reference: Mapped[str] = mapped_column(String(1024), nullable=False)
    artifact_reference: Mapped[str] = mapped_column(String(1024), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reconciled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


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

    publication_intent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("publication_intents.id", ondelete="RESTRICT"), unique=True
    )
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, server_default="1.0")
    repository: Mapped[str] = mapped_column(String(512), nullable=False)
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    pack_id: Mapped[str] = mapped_column(String(160), nullable=False)
    record_id: Mapped[str] = mapped_column(String(160), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    receipt_digest: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("publication_receipts.receipt_digest", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
