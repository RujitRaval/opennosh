from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
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


class GovernanceRoleAssignment(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "governance_role_assignments"
    __table_args__ = (
        CheckConstraint("role IN ('steward')", name="role_allowed"),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= granted_at",
            name="revocation_after_grant",
        ),
        CheckConstraint(
            "(revoked_at IS NULL AND revoked_by_actor_id IS NULL AND "
            "revocation_reason IS NULL) OR "
            "(revoked_at IS NOT NULL AND revoked_by_actor_id IS NOT NULL AND "
            "revocation_reason IS NOT NULL)",
            name="revocation_audit_complete",
        ),
        UniqueConstraint("pack_id", "actor_id", "role", name="uq_governance_role_scope"),
        Index("ix_governance_roles_actor_scope", "actor_id", "pack_id", "role"),
    )

    pack_id: Mapped[str] = mapped_column(String(160), nullable=False)
    actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    granted_by_actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    grant_reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_by_actor_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    revocation_reason: Mapped[str | None] = mapped_column(String(1000))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GovernanceRecusal(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "governance_recusals"
    __table_args__ = (
        UniqueConstraint(
            "source_draft_id",
            "actor_id",
            name="uq_governance_recusal_draft_actor",
        ),
        Index("ix_governance_recusals_actor_pack", "actor_id", "pack_id"),
    )

    pack_id: Mapped[str] = mapped_column(String(160), nullable=False)
    source_draft_id: Mapped[UUID] = mapped_column(
        ForeignKey("contribution_drafts.id", ondelete="RESTRICT"), nullable=False
    )
    actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    recused_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GovernanceDecision(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "governance_decisions"
    __table_args__ = (
        CheckConstraint("outcome = 'approved'", name="outcome_allowed"),
        CheckConstraint("source_draft_version > 0", name="draft_version_positive"),
        CheckConstraint(
            "approved_payload_digest ~ '^[0-9a-f]{64}$'",
            name="approved_payload_digest_sha256",
        ),
        CheckConstraint(
            "expected_base_commit ~ '^[0-9a-f]{40}([0-9a-f]{24})?$'",
            name="expected_base_commit_hash",
        ),
        UniqueConstraint(
            "source_draft_id",
            "source_draft_version",
            name="uq_governance_decision_draft_version",
        ),
        Index("ix_governance_decisions_pack_decided", "pack_id", "decided_at"),
    )

    source_draft_id: Mapped[UUID] = mapped_column(
        ForeignKey("contribution_drafts.id", ondelete="RESTRICT"), nullable=False
    )
    source_draft_version: Mapped[int] = mapped_column(Integer, nullable=False)
    pack_id: Mapped[str] = mapped_column(String(160), nullable=False)
    record_id: Mapped[str] = mapped_column(String(160), nullable=False)
    contributor_actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    deciding_actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(String(2000), nullable=False)
    approved_payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_changes_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    expected_base_commit: Mapped[str] = mapped_column(String(64), nullable=False)
    required_checks_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    forge_target: Mapped[str] = mapped_column(String(512), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GovernancePublicationPause(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "governance_publication_pauses"
    __table_args__ = (
        CheckConstraint(
            "resumed_at IS NULL OR resumed_at >= paused_at",
            name="resume_after_pause",
        ),
        CheckConstraint(
            "(resumed_at IS NULL AND resumed_by_actor_id IS NULL AND resume_reason IS NULL) OR "
            "(resumed_at IS NOT NULL AND resumed_by_actor_id IS NOT NULL AND "
            "resume_reason IS NOT NULL)",
            name="resume_audit_complete",
        ),
        Index(
            "uq_governance_publication_pauses_active_pack",
            "pack_id",
            unique=True,
            postgresql_where=text("resumed_at IS NULL"),
        ),
        Index("ix_governance_publication_pauses_pack_time", "pack_id", "paused_at"),
    )

    pack_id: Mapped[str] = mapped_column(String(160), nullable=False)
    paused_by_actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    pause_reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    paused_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resumed_by_actor_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    resume_reason: Mapped[str | None] = mapped_column(String(1000))
    resumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class GovernancePublicationIntervention(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "governance_publication_interventions"
    __table_args__ = (
        CheckConstraint(
            "action IN ('changes_requested', 'rejected')",
            name="action_allowed",
        ),
        UniqueConstraint(
            "publication_intent_id",
            name="uq_governance_intervention_publication",
        ),
        Index("ix_governance_interventions_pack_time", "pack_id", "intervened_at"),
    )

    publication_intent_id: Mapped[UUID] = mapped_column(
        ForeignKey("publication_intents.id", ondelete="RESTRICT"), nullable=False
    )
    source_draft_id: Mapped[UUID] = mapped_column(
        ForeignKey("contribution_drafts.id", ondelete="RESTRICT"), nullable=False
    )
    pack_id: Mapped[str] = mapped_column(String(160), nullable=False)
    actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    intervened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GovernanceMergeAuthorization(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "governance_merge_authorizations"
    __table_args__ = (
        CheckConstraint(
            "head_commit ~ '^[0-9a-f]{40}([0-9a-f]{24})?$'",
            name="head_commit_hash",
        ),
        CheckConstraint(
            "approved_payload_digest ~ '^[0-9a-f]{64}$'",
            name="payload_digest_sha256",
        ),
        UniqueConstraint(
            "publication_intent_id",
            name="uq_governance_merge_authorization_publication",
        ),
        Index("ix_governance_merge_authorizations_pack_time", "pack_id", "authorized_at"),
    )

    publication_intent_id: Mapped[UUID] = mapped_column(
        ForeignKey("publication_intents.id", ondelete="RESTRICT"), nullable=False
    )
    decision_id: Mapped[UUID] = mapped_column(
        ForeignKey("governance_decisions.id", ondelete="RESTRICT"), nullable=False
    )
    pack_id: Mapped[str] = mapped_column(String(160), nullable=False)
    head_commit: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    authorized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = [
    "GovernanceDecision",
    "GovernanceMergeAuthorization",
    "GovernancePublicationIntervention",
    "GovernancePublicationPause",
    "GovernanceRecusal",
    "GovernanceRoleAssignment",
]
