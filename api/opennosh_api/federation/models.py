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
    LargeBinary,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from opennosh_api.orm import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class FederationInvitation(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "federation_invitations"
    __table_args__ = (
        CheckConstraint("octet_length(token_hash) = 32", name="token_hash_sha256"),
        CheckConstraint("github_account_id > 0", name="github_account_id_positive"),
        CheckConstraint("repository_id > 0", name="repository_id_positive"),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        CheckConstraint(
            "expires_at <= created_at + interval '24 hours'",
            name="expiry_within_24_hours",
        ),
        CheckConstraint(
            "consumed_at IS NULL OR consumed_at >= created_at",
            name="consumption_after_creation",
        ),
        UniqueConstraint("token_hash", name="uq_federation_invitation_token_hash"),
        UniqueConstraint(
            "repository_id",
            "pack_id",
            name="uq_federation_invitation_scope",
        ),
    )

    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    github_account_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    github_login: Mapped[str] = mapped_column(String(100), nullable=False)
    repository_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    repository: Mapped[str] = mapped_column(String(201), nullable=False)
    pack_id: Mapped[str] = mapped_column(String(160), nullable=False)
    inviter_actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FederationMaintainer(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "federation_maintainers"
    __table_args__ = (
        CheckConstraint("github_account_id > 0", name="github_account_id_positive"),
        CheckConstraint("github_app_installation_id > 0", name="installation_id_positive"),
        CheckConstraint("repository_id > 0", name="repository_id_positive"),
        CheckConstraint(
            "state IN ('requested','verified','active','quarantined','revoked')",
            name="state_allowed",
        ),
        CheckConstraint(
            "current_role_key_fingerprint ~ '^[0-9a-f]{64}$'",
            name="current_key_fingerprint_sha256",
        ),
        UniqueConstraint(
            "github_account_id",
            "repository_id",
            "pack_id",
            name="uq_federation_maintainer_identity_scope",
        ),
        Index(
            "uq_federation_active_repository_pack",
            "repository_id",
            "pack_id",
            unique=True,
            postgresql_where=text("state = 'active'"),
        ),
        Index("ix_federation_maintainers_scope_state", "repository_id", "pack_id", "state"),
    )

    github_account_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    github_login: Mapped[str] = mapped_column(String(100), nullable=False)
    github_app_installation_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    repository_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    repository: Mapped[str] = mapped_column(String(201), nullable=False)
    pack_id: Mapped[str] = mapped_column(String(160), nullable=False)
    current_role_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    current_role_key_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    inviter_actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quarantined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FederationRoleKey(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "federation_role_keys"
    __table_args__ = (
        CheckConstraint(
            "public_key_fingerprint ~ '^[0-9a-f]{64}$'",
            name="public_key_fingerprint_sha256",
        ),
        CheckConstraint(
            "retired_at IS NULL OR retired_at >= activated_at",
            name="retirement_after_activation",
        ),
        CheckConstraint("prior_key_id IS NULL OR prior_key_id != id", name="prior_key_not_self"),
        UniqueConstraint("key_id", name="uq_federation_role_key_id"),
        UniqueConstraint("public_key_fingerprint", name="uq_federation_role_key_fingerprint"),
        Index(
            "uq_federation_unretired_maintainer_key",
            "maintainer_id",
            unique=True,
            postgresql_where=text("retired_at IS NULL"),
        ),
    )

    maintainer_id: Mapped[UUID] = mapped_column(
        ForeignKey("federation_maintainers.id", ondelete="RESTRICT"), nullable=False
    )
    key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    public_key: Mapped[str] = mapped_column(String(64), nullable=False)
    public_key_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rotated_by_actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    prior_key_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("federation_role_keys.id", ondelete="RESTRICT")
    )


class FederationAuditEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "federation_audit_events"
    __table_args__ = (
        CheckConstraint(
            "payload_digest ~ '^[0-9a-f]{64}$'",
            name="payload_digest_sha256",
        ),
        Index("ix_federation_audit_maintainer_created", "maintainer_id", "created_at"),
        Index("ix_federation_audit_invitation_created", "invitation_id", "created_at"),
    )

    maintainer_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("federation_maintainers.id", ondelete="RESTRICT")
    )
    invitation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("federation_invitations.id", ondelete="RESTRICT")
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)


class FederationRelease(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Immutable verification fact for one maintainer-signed governed release."""

    __tablename__ = "federation_releases"
    __table_args__ = (
        CheckConstraint("repository_id > 0", name="repository_id_positive"),
        CheckConstraint("statement_digest ~ '^[0-9a-f]{64}$'", name="statement_digest_sha256"),
        CheckConstraint("manifest_digest ~ '^[0-9a-f]{64}$'", name="manifest_digest_sha256"),
        CheckConstraint("receipt_digest ~ '^[0-9a-f]{64}$'", name="receipt_digest_sha256"),
        CheckConstraint("signature ~ '^[A-Za-z0-9_-]{86}$'", name="signature_base64url"),
        CheckConstraint("jsonb_typeof(statement_json) = 'object'", name="statement_json_object"),
        CheckConstraint(
            "receipt_published_at <= issued_at", name="receipt_publication_before_issue"
        ),
        CheckConstraint(
            "verified_at + INTERVAL '5 minutes' >= issued_at",
            name="verification_within_clock_skew",
        ),
        UniqueConstraint("statement_digest", name="uq_federation_release_statement_digest"),
        UniqueConstraint(
            "repository_id",
            "pack_id",
            "release_version",
            name="uq_federation_release_scope_version",
        ),
        UniqueConstraint(
            "repository_id",
            "pack_id",
            "publication_id",
            name="uq_federation_release_scope_publication",
        ),
        Index(
            "ix_federation_releases_scope_order",
            "repository_id",
            "pack_id",
            "receipt_published_at",
        ),
    )

    maintainer_id: Mapped[UUID] = mapped_column(
        ForeignKey("federation_maintainers.id", ondelete="RESTRICT"), nullable=False
    )
    role_key_id: Mapped[UUID] = mapped_column(
        ForeignKey("federation_role_keys.id", ondelete="RESTRICT"), nullable=False
    )
    accepted_event_id: Mapped[UUID] = mapped_column(
        ForeignKey("accepted_events.id", ondelete="RESTRICT"), nullable=False
    )
    repository_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    repository: Mapped[str] = mapped_column(String(201), nullable=False)
    pack_id: Mapped[str] = mapped_column(String(160), nullable=False)
    publication_id: Mapped[UUID] = mapped_column(nullable=False)
    release_version: Mapped[str] = mapped_column(String(255), nullable=False)
    statement_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    statement_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    receipt_digest: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("publication_receipts.receipt_digest", ondelete="RESTRICT"),
        nullable=False,
    )
    public_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    signature: Mapped[str] = mapped_column(String(86), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    receipt_published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FederationVerifiedRelease(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Immutable normalized artifact verification for one release-ledger row."""

    __tablename__ = "federation_verified_releases"
    __table_args__ = (
        CheckConstraint("pack_license = 'CC0-1.0'", name="pack_license_allowed"),
        CheckConstraint("manifest_digest ~ '^[0-9a-f]{64}$'", name="manifest_digest_sha256"),
        CheckConstraint("artifact_digest ~ '^[0-9a-f]{64}$'", name="artifact_digest_sha256"),
        CheckConstraint("record_set_digest ~ '^[0-9a-f]{64}$'", name="record_set_digest_sha256"),
        CheckConstraint("artifact_size_bytes > 0", name="artifact_size_positive"),
        CheckConstraint("record_count > 0", name="record_count_positive"),
        CheckConstraint("jsonb_typeof(records_json) = 'array'", name="records_json_array"),
        UniqueConstraint("release_id", name="uq_federation_verified_release_release"),
        Index("ix_federation_verified_releases_verified", "verified_at"),
    )

    release_id: Mapped[UUID] = mapped_column(
        ForeignKey("federation_releases.id", ondelete="RESTRICT"), nullable=False
    )
    pack_version: Mapped[str] = mapped_column(String(64), nullable=False)
    pack_license: Mapped[str] = mapped_column(String(32), nullable=False)
    manifest_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    artifact_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    records_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    record_set_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FederationReleaseStatusEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Append-only eligibility decision; any quarantine event is terminal."""

    __tablename__ = "federation_release_status_events"
    __table_args__ = (
        CheckConstraint("state IN ('verified','quarantined')", name="state_allowed"),
        CheckConstraint("reason_digest ~ '^[0-9a-f]{64}$'", name="reason_digest_sha256"),
        Index("ix_federation_release_status_latest", "release_id", "occurred_at"),
    )

    release_id: Mapped[UUID] = mapped_column(
        ForeignKey("federation_releases.id", ondelete="RESTRICT"), nullable=False
    )
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    reason_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FederationPackInstallationEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Append-only local selection of one verified release for a pack scope."""

    __tablename__ = "federation_pack_installation_events"
    __table_args__ = (
        CheckConstraint("repository_id > 0", name="repository_id_positive"),
        CheckConstraint(
            "action IN ('install','update','rollback','remove')", name="action_allowed"
        ),
        CheckConstraint("generation > 0", name="generation_positive"),
        CheckConstraint("reason_digest ~ '^[0-9a-f]{64}$'", name="reason_digest_sha256"),
        CheckConstraint(
            "(action = 'remove') = (verified_release_id IS NULL)",
            name="release_binding_matches_action",
        ),
        UniqueConstraint(
            "repository_id", "pack_id", "generation", name="uq_federation_install_scope_gen"
        ),
        Index(
            "ix_federation_install_scope_latest",
            "repository_id",
            "pack_id",
            "generation",
        ),
    )

    repository_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    pack_id: Mapped[str] = mapped_column(String(160), nullable=False)
    verified_release_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("federation_verified_releases.id", ondelete="RESTRICT")
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    prior_event_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("federation_pack_installation_events.id", ondelete="RESTRICT")
    )
    actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    reason_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FederationProjectionCheckpoint(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Immutable release-set identity for one complete candidate projection."""

    __tablename__ = "federation_projection_checkpoints"
    __table_args__ = (
        CheckConstraint("release_set_digest ~ '^[0-9a-f]{64}$'", name="release_set_sha256"),
        CheckConstraint("jsonb_typeof(release_set_json) = 'array'", name="release_set_json_array"),
        CheckConstraint("mode IN ('registry','installed')", name="mode_allowed"),
        CheckConstraint("release_count >= 0", name="release_count_nonnegative"),
        CheckConstraint("record_count >= 0", name="record_count_nonnegative"),
        CheckConstraint(
            "(release_count = 0 AND record_count = 0) OR "
            "(release_count > 0 AND record_count > 0)",
            name="projection_counts_consistent",
        ),
        CheckConstraint(
            "mode = 'installed' OR (release_count > 0 AND record_count > 0)",
            name="registry_projection_nonempty",
        ),
        UniqueConstraint(
            "mode", "release_set_digest", name="uq_federation_projection_mode_release_set"
        ),
        Index("ix_federation_projection_checkpoints_built", "built_at"),
    )

    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="registry")
    release_set_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    release_set_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    release_count: Mapped[int] = mapped_column(Integer, nullable=False)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False)
    built_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FederationProjectionRelease(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "federation_projection_releases"
    __table_args__ = (
        CheckConstraint("ordinal >= 0", name="ordinal_nonnegative"),
        UniqueConstraint(
            "checkpoint_id",
            "verified_release_id",
            name="uq_federation_projection_release_membership",
        ),
        UniqueConstraint(
            "checkpoint_id",
            "ordinal",
            name="uq_federation_projection_release_ordinal",
        ),
    )

    checkpoint_id: Mapped[UUID] = mapped_column(
        ForeignKey("federation_projection_checkpoints.id", ondelete="RESTRICT"), nullable=False
    )
    verified_release_id: Mapped[UUID] = mapped_column(
        ForeignKey("federation_verified_releases.id", ondelete="RESTRICT"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)


class FederationProjectionFood(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "federation_projection_foods"
    __table_args__ = (
        CheckConstraint("pack_license = 'CC0-1.0'", name="pack_license_allowed"),
        CheckConstraint("jsonb_typeof(nutrients_json) = 'object'", name="nutrients_json_object"),
        CheckConstraint("jsonb_typeof(portions_json) = 'array'", name="portions_json_array"),
        CheckConstraint(
            "equivalence_key IS NULL OR equivalence_key ~ '^[0-9a-f]{64}$'",
            name="equivalence_key_sha256",
        ),
        CheckConstraint(
            "nutrients_digest IS NULL OR nutrients_digest ~ '^[0-9a-f]{64}$'",
            name="nutrients_digest_sha256",
        ),
        UniqueConstraint(
            "checkpoint_id",
            "verified_release_id",
            "source_record_id",
            name="uq_federation_projection_food_source",
        ),
        Index("ix_federation_projection_foods_checkpoint_pack", "checkpoint_id", "pack_id"),
        Index("ix_federation_projection_foods_checkpoint_name", "checkpoint_id", "name"),
    )

    checkpoint_id: Mapped[UUID] = mapped_column(
        ForeignKey("federation_projection_checkpoints.id", ondelete="RESTRICT"), nullable=False
    )
    verified_release_id: Mapped[UUID] = mapped_column(
        ForeignKey("federation_verified_releases.id", ondelete="RESTRICT"), nullable=False
    )
    source_record_id: Mapped[str] = mapped_column(String(120), nullable=False)
    pack_id: Mapped[str] = mapped_column(String(160), nullable=False)
    pack_version: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    name_local: Mapped[str | None] = mapped_column(String(160))
    locale: Mapped[str] = mapped_column(String(35), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    provenance: Mapped[str] = mapped_column(String(64), nullable=False)
    source_uri: Mapped[str | None] = mapped_column(String(2048))
    source_license: Mapped[str] = mapped_column(String(255), nullable=False)
    source_note: Mapped[str | None] = mapped_column(String(1000))
    nutrients_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    portions_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    pack_license: Mapped[str] = mapped_column(String(32), nullable=False)
    contributed_by: Mapped[str] = mapped_column(String(100), nullable=False)
    equivalence_key: Mapped[str | None] = mapped_column(String(64))
    nutrients_digest: Mapped[str | None] = mapped_column(String(64))


class FederationProjectionActivation(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Pointer-last activation fact; newest row selects the active checkpoint."""

    __tablename__ = "federation_projection_activations"
    __table_args__ = (
        CheckConstraint("reason_digest ~ '^[0-9a-f]{64}$'", name="reason_digest_sha256"),
        Index("ix_federation_projection_activations_latest", "activated_at"),
    )

    checkpoint_id: Mapped[UUID] = mapped_column(
        ForeignKey("federation_projection_checkpoints.id", ondelete="RESTRICT"), nullable=False
    )
    actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    reason_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
