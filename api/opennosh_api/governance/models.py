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
        CheckConstraint(
            "outcome IN ('approved','changes_requested','rejected')",
            name="outcome_allowed",
        ),
        CheckConstraint("source_draft_version > 0", name="draft_version_positive"),
        CheckConstraint(
            "approved_payload_digest IS NULL OR approved_payload_digest ~ '^[0-9a-f]{64}$'",
            name="approved_payload_digest_sha256",
        ),
        CheckConstraint(
            "expected_base_commit IS NULL OR "
            "expected_base_commit ~ '^[0-9a-f]{40}([0-9a-f]{24})?$'",
            name="expected_base_commit_hash",
        ),
        CheckConstraint(
            "(outcome = 'approved' AND approved_payload_digest IS NOT NULL "
            "AND approved_changes_json IS NOT NULL AND expected_base_commit IS NOT NULL "
            "AND required_checks_json IS NOT NULL AND forge_target IS NOT NULL) OR "
            "(outcome IN ('changes_requested','rejected') "
            "AND approved_payload_digest IS NULL AND approved_changes_json IS NULL "
            "AND expected_base_commit IS NULL AND required_checks_json IS NULL "
            "AND forge_target IS NULL)",
            name="outcome_shape_valid",
        ),
        CheckConstraint(
            "prior_decision_id IS NULL OR prior_decision_id != id",
            name="prior_decision_not_self",
        ),
        UniqueConstraint(
            "prior_decision_id",
            name="uq_governance_decision_successor",
        ),
        Index(
            "uq_governance_decision_initial_draft_version",
            "source_draft_id",
            "source_draft_version",
            unique=True,
            postgresql_where=text("prior_decision_id IS NULL"),
        ),
        Index("ix_governance_decisions_pack_decided", "pack_id", "decided_at"),
    )

    source_draft_id: Mapped[UUID] = mapped_column(
        ForeignKey("contribution_drafts.id", ondelete="RESTRICT"), nullable=False
    )
    prior_decision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("governance_decisions.id", ondelete="RESTRICT")
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
    approved_payload_digest: Mapped[str | None] = mapped_column(String(64))
    approved_changes_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    expected_base_commit: Mapped[str | None] = mapped_column(String(64))
    required_checks_json: Mapped[list[str] | None] = mapped_column(JSONB(none_as_null=True))
    forge_target: Mapped[str | None] = mapped_column(String(512))
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


class GovernanceReviewCase(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "governance_review_cases"
    __table_args__ = (
        CheckConstraint("source_draft_version > 0", name="draft_version_positive"),
        CheckConstraint("revision > 0", name="revision_positive"),
        CheckConstraint(
            "state IN ('pending','in_review','changes_requested','approved','rejected',"
            "'disputed','appealed','reopened','closed')",
            name="state_allowed",
        ),
        CheckConstraint(
            "acknowledged_at IS NULL OR assigned_steward_actor_id IS NOT NULL",
            name="acknowledgement_requires_assignment",
        ),
        CheckConstraint(
            "(pause_reason IS NULL AND next_review_at IS NULL) OR "
            "(pause_reason IS NOT NULL AND next_review_at IS NOT NULL)",
            name="pause_shape_complete",
        ),
        CheckConstraint(
            "closed_at IS NULL OR state = 'closed'",
            name="closed_time_matches_state",
        ),
        UniqueConstraint(
            "source_draft_id",
            "source_draft_version",
            name="uq_governance_review_case_draft_version",
        ),
        Index("ix_governance_review_cases_queue", "state", "next_review_at", "opened_at"),
        Index("ix_governance_review_cases_pack_opened", "pack_id", "opened_at"),
    )

    source_draft_id: Mapped[UUID] = mapped_column(
        ForeignKey("contribution_drafts.id", ondelete="RESTRICT"), nullable=False
    )
    source_draft_version: Mapped[int] = mapped_column(Integer, nullable=False)
    pack_id: Mapped[str] = mapped_column(String(160), nullable=False)
    contributor_actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    submitted_fields_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    assigned_steward_actor_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pause_reason: Mapped[str | None] = mapped_column(String(1000))
    next_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GovernanceReviewEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "governance_review_events"
    __table_args__ = (
        CheckConstraint("sequence > 0", name="sequence_positive"),
        CheckConstraint(
            "idempotency_key_hash IS NULL OR idempotency_key_hash ~ '^[0-9a-f]{64}$'",
            name="idempotency_key_hash_valid",
        ),
        CheckConstraint(
            "request_hash IS NULL OR request_hash ~ '^[0-9a-f]{64}$'",
            name="request_hash_valid",
        ),
        CheckConstraint(
            "event_type IN ('opened','claimed','released','recused','paused','resumed',"
            "'changes_requested','contributor_responded','approved','rejected',"
            "'dispute_opened','dispute_resolved','appeal_opened','appeal_resolved',"
            "'reopened','closed')",
            name="event_type_allowed",
        ),
        UniqueConstraint("review_case_id", "sequence", name="uq_governance_review_event_order"),
        UniqueConstraint(
            "review_case_id",
            "idempotency_key_hash",
            name="uq_governance_review_event_idempotency",
        ),
        Index("ix_governance_review_events_case_time", "review_case_id", "occurred_at"),
    )

    review_case_id: Mapped[UUID] = mapped_column(
        ForeignKey("governance_review_cases.id", ondelete="RESTRICT"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    public_reason: Mapped[str | None] = mapped_column(String(2000))
    idempotency_key_hash: Mapped[str | None] = mapped_column(String(64))
    request_hash: Mapped[str | None] = mapped_column(String(64))
    details_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GovernanceReviewPrivateNote(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "governance_review_private_notes"
    __table_args__ = (
        Index("ix_governance_review_private_notes_case_time", "review_case_id", "noted_at"),
    )

    review_case_id: Mapped[UUID] = mapped_column(
        ForeignKey("governance_review_cases.id", ondelete="RESTRICT"), nullable=False
    )
    author_actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    note: Mapped[str] = mapped_column(String(4000), nullable=False)
    noted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GovernanceDispute(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "governance_disputes"
    __table_args__ = (
        CheckConstraint("revision > 0", name="revision_positive"),
        CheckConstraint("state IN ('open','resolved')", name="state_allowed"),
        CheckConstraint(
            "category IN ('evidence','accuracy','rights','process','other')",
            name="category_allowed",
        ),
        CheckConstraint(
            "(state = 'open' AND resolution IS NULL AND resolved_by_actor_id IS NULL "
            "AND resolved_at IS NULL) OR "
            "(state = 'resolved' AND resolution IS NOT NULL "
            "AND resolved_by_actor_id IS NOT NULL AND resolved_at IS NOT NULL)",
            name="resolution_shape_valid",
        ),
        Index(
            "uq_governance_disputes_active_case",
            "review_case_id",
            unique=True,
            postgresql_where=text("state = 'open'"),
        ),
        Index("ix_governance_disputes_pack_opened", "pack_id", "opened_at"),
    )

    review_case_id: Mapped[UUID] = mapped_column(
        ForeignKey("governance_review_cases.id", ondelete="RESTRICT"), nullable=False
    )
    decision_id: Mapped[UUID] = mapped_column(
        ForeignKey("governance_decisions.id", ondelete="RESTRICT"), nullable=False
    )
    pack_id: Mapped[str] = mapped_column(String(160), nullable=False)
    opened_by_actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    public_reason: Mapped[str] = mapped_column(String(2000), nullable=False)
    requested_remedy: Mapped[str] = mapped_column(String(1000), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolution: Mapped[str | None] = mapped_column(String(2000))
    resolved_by_actor_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GovernanceAppeal(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "governance_appeals"
    __table_args__ = (
        CheckConstraint("revision > 0", name="revision_positive"),
        CheckConstraint("state IN ('open','resolved','reopened')", name="state_allowed"),
        CheckConstraint(
            "(state IN ('open','reopened') AND resolution IS NULL "
            "AND decided_by_actor_id IS NULL AND resolved_at IS NULL) OR "
            "(state = 'resolved' AND resolution IS NOT NULL "
            "AND decided_by_actor_id IS NOT NULL AND resolved_at IS NOT NULL)",
            name="resolution_shape_valid",
        ),
        CheckConstraint(
            "decided_by_actor_id IS NULL OR decided_by_actor_id != original_deciding_actor_id",
            name="independent_decider",
        ),
        UniqueConstraint("dispute_id", name="uq_governance_appeal_dispute"),
        Index("ix_governance_appeals_opened", "opened_at"),
    )

    dispute_id: Mapped[UUID] = mapped_column(
        ForeignKey("governance_disputes.id", ondelete="RESTRICT"), nullable=False
    )
    review_case_id: Mapped[UUID] = mapped_column(
        ForeignKey("governance_review_cases.id", ondelete="RESTRICT"), nullable=False
    )
    opened_by_actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    original_deciding_actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    public_reason: Mapped[str] = mapped_column(String(2000), nullable=False)
    requested_remedy: Mapped[str] = mapped_column(String(1000), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolution: Mapped[str | None] = mapped_column(String(2000))
    decided_by_actor_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


__all__ = [
    "GovernanceAppeal",
    "GovernanceDecision",
    "GovernanceDispute",
    "GovernanceMergeAuthorization",
    "GovernancePublicationIntervention",
    "GovernancePublicationPause",
    "GovernanceRecusal",
    "GovernanceReviewCase",
    "GovernanceReviewEvent",
    "GovernanceReviewPrivateNote",
    "GovernanceRoleAssignment",
]
