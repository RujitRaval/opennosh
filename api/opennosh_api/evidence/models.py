from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from opennosh_api.orm import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class EvidenceUploadSession(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "evidence_upload_sessions"
    __table_args__ = (
        CheckConstraint("source_draft_version > 0", name="source_draft_version_positive"),
        CheckConstraint(
            "state IN ('initiated','uploaded','sanitizing','sanitized','attached',"
            "'preserved','expired','failed')",
            name="state_allowed",
        ),
        CheckConstraint(
            "declared_media_type IN ('image/jpeg','image/png','image/webp')",
            name="declared_media_type_allowed",
        ),
        CheckConstraint(
            "declared_byte_length BETWEEN 1 AND 10485760",
            name="declared_byte_length_bounded",
        ),
        CheckConstraint(
            "observed_byte_length IS NULL OR observed_byte_length BETWEEN 1 AND 10485760",
            name="observed_byte_length_bounded",
        ),
        CheckConstraint(
            "observed_sha256 IS NULL OR observed_sha256 ~ '^[0-9a-f]{64}$'",
            name="observed_sha256_valid",
        ),
        CheckConstraint(
            "observed_revision_sha256 IS NULL "
            "OR observed_revision_sha256 ~ '^[0-9a-f]{64}$'",
            name="observed_revision_sha256_valid",
        ),
        CheckConstraint("capability_hash ~ '^[0-9a-f]{64}$'", name="capability_hash_valid"),
        CheckConstraint(
            "idempotency_key_hash ~ '^[0-9a-f]{64}$'",
            name="idempotency_key_hash_valid",
        ),
        CheckConstraint("request_hash ~ '^[0-9a-f]{64}$'", name="request_hash_valid"),
        CheckConstraint(
            "object_key ~ '^quarantine/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
            "[89ab][0-9a-f]{3}-[0-9a-f]{12}$'",
            name="object_key_valid",
        ),
        CheckConstraint(
            "expires_at > created_at AND expires_at <= created_at + interval '10 minutes'",
            name="expiry_bounded",
        ),
        CheckConstraint(
            "uploaded_at IS NULL OR uploaded_at >= created_at",
            name="upload_after_creation",
        ),
        CheckConstraint(
            "failed_at IS NULL OR failed_at >= created_at",
            name="failure_after_creation",
        ),
        CheckConstraint(
            "(failure_code IS NULL) = (failed_at IS NULL)",
            name="failure_consistent",
        ),
        CheckConstraint(
            "state != 'failed' OR failure_code IS NOT NULL",
            name="failed_state_typed",
        ),
        CheckConstraint(
            "(state IN ('initiated','expired') AND observed_byte_length IS NULL "
            "AND observed_sha256 IS NULL AND uploaded_at IS NULL "
            "AND failure_code IS NULL AND failed_at IS NULL "
            "AND sanitized_object_key IS NULL AND attached_evidence_id IS NULL "
            "AND preserved_at IS NULL) OR "
            "(state = 'failed' AND failure_code IS NOT NULL AND failed_at IS NOT NULL "
            "AND sanitized_object_key IS NULL AND attached_evidence_id IS NULL "
            "AND preserved_at IS NULL) OR "
            "(state IN ('uploaded','sanitizing') "
            "AND observed_byte_length IS NOT NULL AND observed_sha256 IS NOT NULL "
            "AND uploaded_at IS NOT NULL AND failure_code IS NULL AND failed_at IS NULL "
            "AND sanitized_object_key IS NULL AND attached_evidence_id IS NULL "
            "AND preserved_at IS NULL) OR "
            "(state = 'sanitized' AND observed_byte_length IS NOT NULL "
            "AND observed_sha256 IS NOT NULL AND uploaded_at IS NOT NULL "
            "AND failure_code IS NULL AND failed_at IS NULL "
            "AND sanitized_object_key IS NOT NULL "
            "AND attached_evidence_id IS NULL AND preserved_at IS NULL) OR "
            "(state = 'attached' AND observed_byte_length IS NOT NULL "
            "AND observed_sha256 IS NOT NULL AND uploaded_at IS NOT NULL "
            "AND failure_code IS NULL AND failed_at IS NULL "
            "AND sanitized_object_key IS NOT NULL "
            "AND attached_evidence_id IS NOT NULL AND preserved_at IS NULL) OR "
            "(state = 'preserved' AND observed_byte_length IS NOT NULL "
            "AND observed_sha256 IS NOT NULL AND uploaded_at IS NOT NULL "
            "AND failure_code IS NULL AND failed_at IS NULL "
            "AND sanitized_object_key IS NOT NULL "
            "AND attached_evidence_id IS NOT NULL AND preserved_at IS NOT NULL)",
            name="state_shape_valid",
        ),
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint(
            "failure_code IS NULL OR failure_code IN ('object_missing','size_mismatch',"
            "'size_exceeded','media_type_mismatch','object_changed','capability_invalid',"
            "'expired','storage_unavailable','signature_mismatch','decode_failed',"
            "'pixel_limit_exceeded','animation_unsupported','metadata_rewrite_failed',"
            "'sanitized_size_exceeded','malware_detected','scanner_unavailable',"
            "'sanitized_storage_unavailable','sanitized_storage_conflict')",
            name="failure_code_allowed",
        ),
        CheckConstraint(
            "sanitized_object_key IS NULL OR sanitized_object_key ~ "
            "'^sanitized/[0-9a-f]{64}\\.png$'",
            name="sanitized_object_key_valid",
        ),
        CheckConstraint(
            "sanitized_media_type IS NULL OR sanitized_media_type = 'image/png'",
            name="sanitized_media_type_allowed",
        ),
        CheckConstraint(
            "sanitized_byte_length IS NULL OR sanitized_byte_length BETWEEN 1 AND 10485760",
            name="sanitized_byte_length_bounded",
        ),
        CheckConstraint(
            "sanitized_sha256 IS NULL OR sanitized_sha256 ~ '^[0-9a-f]{64}$'",
            name="sanitized_sha256_valid",
        ),
        CheckConstraint(
            "sanitized_width IS NULL OR sanitized_width BETWEEN 1 AND 20000",
            name="sanitized_width_bounded",
        ),
        CheckConstraint(
            "sanitized_height IS NULL OR sanitized_height BETWEEN 1 AND 20000",
            name="sanitized_height_bounded",
        ),
        CheckConstraint(
            "sanitized_width IS NULL OR sanitized_height IS NULL "
            "OR sanitized_width * sanitized_height <= 20000000",
            name="sanitized_pixels_bounded",
        ),
        CheckConstraint(
            "(sanitized_object_key IS NULL AND sanitized_media_type IS NULL "
            "AND sanitized_byte_length IS NULL AND sanitized_sha256 IS NULL "
            "AND sanitized_width IS NULL AND sanitized_height IS NULL "
            "AND sanitized_at IS NULL) OR "
            "(sanitized_object_key IS NOT NULL AND sanitized_media_type IS NOT NULL "
            "AND sanitized_byte_length IS NOT NULL AND sanitized_sha256 IS NOT NULL "
            "AND sanitized_width IS NOT NULL AND sanitized_height IS NOT NULL "
            "AND sanitized_at IS NOT NULL)",
            name="sanitized_result_consistent",
        ),
        CheckConstraint(
            "(attached_evidence_id IS NULL) = (attached_at IS NULL)",
            name="attachment_consistent",
        ),
        CheckConstraint(
            "sanitized_at IS NULL OR sanitized_at >= uploaded_at",
            name="sanitized_after_upload",
        ),
        CheckConstraint(
            "attached_at IS NULL OR attached_at >= sanitized_at",
            name="attachment_after_sanitized",
        ),
        CheckConstraint(
            "preserved_at IS NULL OR preserved_at >= attached_at",
            name="preserved_after_attachment",
        ),
        UniqueConstraint(
            "user_id",
            "draft_id",
            "idempotency_key_hash",
            name="uq_evidence_upload_user_draft_idempotency",
        ),
        UniqueConstraint("object_key", name="uq_evidence_upload_object_key"),
        UniqueConstraint(
            "attached_evidence_id",
            name="uq_evidence_upload_attached_evidence",
        ),
        Index(
            "ix_evidence_upload_user_draft_created",
            "user_id",
            "draft_id",
            text("created_at DESC"),
        ),
        Index("ix_evidence_upload_state_expires", "state", "expires_at"),
        Index(
            "ix_evidence_upload_draft_version",
            "draft_id",
            "source_draft_version",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    draft_id: Mapped[UUID] = mapped_column(
        ForeignKey("contribution_drafts.id", ondelete="CASCADE"), nullable=False
    )
    source_draft_version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, server_default="initiated")
    object_key: Mapped[str] = mapped_column(String(255), nullable=False)
    declared_media_type: Mapped[str] = mapped_column(String(64), nullable=False)
    declared_byte_length: Mapped[int] = mapped_column(Integer, nullable=False)
    observed_byte_length: Mapped[int | None] = mapped_column(Integer)
    observed_sha256: Mapped[str | None] = mapped_column(CHAR(64))
    observed_revision_sha256: Mapped[str | None] = mapped_column(CHAR(64))
    capability_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(40))
    sanitized_object_key: Mapped[str | None] = mapped_column(String(255))
    sanitized_media_type: Mapped[str | None] = mapped_column(String(64))
    sanitized_byte_length: Mapped[int | None] = mapped_column(Integer)
    sanitized_sha256: Mapped[str | None] = mapped_column(CHAR(64))
    sanitized_width: Mapped[int | None] = mapped_column(Integer)
    sanitized_height: Mapped[int | None] = mapped_column(Integer)
    sanitized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attached_evidence_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("evidence_manifests.id", ondelete="RESTRICT")
    )
    attached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    preserved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class EvidenceManifestRecord(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "evidence_manifests"
    __table_args__ = (
        CheckConstraint("source_draft_version > 0", name="source_draft_version_positive"),
        CheckConstraint("schema_version = '1.0'", name="schema_version_supported"),
        CheckConstraint(
            "evidence_class IN ('sanitized_media', 'versioned_public_dataset', "
            "'public_document', 'maintainer_attestation')",
            name="evidence_class_allowed",
        ),
        CheckConstraint(
            "public_state IS NULL OR public_state IN ('evidence_preserved', "
            "'source_verified', 'reference_preserved', 'reference_only', 'attested', "
            "'tombstoned')",
            name="public_state_allowed",
        ),
        CheckConstraint(
            "(preservation_failure_code IS NULL) = (preservation_failed_at IS NULL) "
            "AND (preservation_failure_code IS NULL OR public_state IS NULL)",
            name="preservation_failure_consistent",
        ),
        CheckConstraint("manifest_digest ~ '^[0-9a-f]{64}$'", name="manifest_digest_sha256"),
        UniqueConstraint(
            "source_draft_id",
            "source_draft_version",
            name="uq_evidence_manifest_draft_version",
        ),
        Index("ix_evidence_manifests_draft_created", "source_draft_id", "created_at"),
    )

    source_draft_id: Mapped[UUID] = mapped_column(
        ForeignKey("contribution_drafts.id", ondelete="RESTRICT"), nullable=False
    )
    source_draft_version: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    evidence_class: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    public_state: Mapped[str | None] = mapped_column(String(40))
    preservation_failure_code: Mapped[str | None] = mapped_column(String(120))
    preservation_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvidenceDurableAcknowledgement(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "evidence_durable_acknowledgements"
    __table_args__ = (
        CheckConstraint("schema_version = '1.0'", name="schema_version_supported"),
        CheckConstraint("manifest_digest ~ '^[0-9a-f]{64}$'", name="manifest_digest_sha256"),
        CheckConstraint("content_digest ~ '^[0-9a-f]{64}$'", name="content_digest_sha256"),
        UniqueConstraint(
            "evidence_id",
            "acknowledgement_kind",
            "destination",
            name="uq_evidence_acknowledgement_kind_destination",
        ),
        Index(
            "ix_evidence_acknowledgements_evidence_verified",
            "evidence_id",
            "verified_at",
        ),
    )

    evidence_id: Mapped[UUID] = mapped_column(
        ForeignKey("evidence_manifests.id", ondelete="RESTRICT"), nullable=False
    )
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    evidence_class: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    acknowledgement_kind: Mapped[str] = mapped_column(String(80), nullable=False)
    destination: Mapped[str] = mapped_column(String(2048), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    external_reference: Mapped[str] = mapped_column(String(2048), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    adapter_identity: Mapped[str] = mapped_column(String(255), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(80), nullable=False)


class EvidenceRemovalTombstone(CreatedAtMixin, Base):
    __tablename__ = "evidence_removal_tombstones"
    __table_args__ = (
        CheckConstraint("schema_version = '1.0'", name="schema_version_supported"),
        CheckConstraint("manifest_digest ~ '^[0-9a-f]{64}$'", name="manifest_digest_sha256"),
        CheckConstraint(
            "prior_state IN ('evidence_preserved', 'source_verified', "
            "'reference_preserved', 'reference_only', 'attested')",
            name="prior_state_allowed",
        ),
    )

    evidence_id: Mapped[UUID] = mapped_column(
        ForeignKey("evidence_manifests.id", ondelete="RESTRICT"), primary_key=True
    )
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    manifest_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    prior_state: Mapped[str] = mapped_column(String(40), nullable=False)
    reason: Mapped[str] = mapped_column(String(2000), nullable=False)
    removed_by_actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    removed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
