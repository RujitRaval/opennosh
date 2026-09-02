from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from opennosh_api.orm import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class MissionDefinition(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "mission_definitions"
    __table_args__ = (
        CheckConstraint("definition_version > 0", name="definition_version_positive"),
        CheckConstraint(
            "gap_kind IN ('cuisine','locale','institution','dataset','missing_field')",
            name="gap_kind_allowed",
        ),
        CheckConstraint("acceptance_target BETWEEN 1 AND 100000", name="target_bounded"),
        CheckConstraint("jsonb_typeof(definition_json) = 'object'", name="definition_json_object"),
        CheckConstraint(
            "prior_definition_id IS NULL OR prior_definition_id != id",
            name="prior_definition_not_self",
        ),
        CheckConstraint(
            "(definition_version = 1) = (prior_definition_id IS NULL)",
            name="definition_chain_shape",
        ),
        ForeignKeyConstraint(
            ["prior_definition_id", "mission_id"],
            ["mission_definitions.id", "mission_definitions.mission_id"],
            name="fk_mission_definition_prior_same_mission",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "mission_id", name="uq_mission_definition_id_mission"),
        UniqueConstraint("mission_id", "definition_version", name="uq_mission_definition_version"),
        UniqueConstraint("prior_definition_id", name="uq_mission_definition_successor"),
        Index("ix_mission_definitions_pack_version", "target_pack_id", "defined_at"),
    )

    mission_id: Mapped[UUID] = mapped_column(nullable=False)
    definition_version: Mapped[int] = mapped_column(Integer, nullable=False)
    prior_definition_id: Mapped[UUID | None] = mapped_column()
    gap_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    summary: Mapped[str] = mapped_column(String(1000), nullable=False)
    target_pack_id: Mapped[str] = mapped_column(String(160), nullable=False)
    target_dataset: Mapped[str] = mapped_column(String(256), nullable=False)
    acceptance_target: Mapped[int] = mapped_column(Integer, nullable=False)
    acceptance_criteria: Mapped[str] = mapped_column(String(2000), nullable=False)
    definition_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    proposed_by_actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    responsible_steward_actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    defined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MissionLifecycleEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "mission_lifecycle_events"
    __table_args__ = (
        CheckConstraint("sequence > 0", name="sequence_positive"),
        CheckConstraint(
            "action IN ('propose','approve','pause','resume','complete','release','close')",
            name="action_allowed",
        ),
        CheckConstraint(
            "(action = 'pause' AND next_review_at IS NOT NULL) OR "
            "(action != 'pause' AND next_review_at IS NULL)",
            name="pause_review_shape",
        ),
        CheckConstraint(
            "(action = 'release' AND release_receipt_digest IS NOT NULL) OR "
            "(action != 'release' AND release_receipt_digest IS NULL)",
            name="release_receipt_shape",
        ),
        CheckConstraint(
            "prior_event_id IS NULL OR prior_event_id != id",
            name="prior_event_not_self",
        ),
        CheckConstraint(
            "(sequence = 1) = (prior_event_id IS NULL)",
            name="event_chain_shape",
        ),
        CheckConstraint(
            "(sequence = 1) = (action = 'propose')",
            name="proposal_first",
        ),
        ForeignKeyConstraint(
            ["definition_id", "mission_id"],
            ["mission_definitions.id", "mission_definitions.mission_id"],
            name="fk_mission_lifecycle_definition_same_mission",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["prior_event_id", "mission_id"],
            ["mission_lifecycle_events.id", "mission_lifecycle_events.mission_id"],
            name="fk_mission_lifecycle_prior_same_mission",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "mission_id", name="uq_mission_lifecycle_id_mission"),
        UniqueConstraint("mission_id", "sequence", name="uq_mission_lifecycle_sequence"),
        UniqueConstraint("prior_event_id", name="uq_mission_lifecycle_successor"),
        Index("ix_mission_lifecycle_latest", "mission_id", "sequence"),
    )

    mission_id: Mapped[UUID] = mapped_column(nullable=False)
    definition_id: Mapped[UUID] = mapped_column(nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    prior_event_id: Mapped[UUID | None] = mapped_column()
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    public_reason: Mapped[str] = mapped_column(String(2000), nullable=False)
    next_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    release_receipt_digest: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("publication_receipts.receipt_digest", ondelete="RESTRICT")
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MissionContributionBinding(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "mission_contribution_bindings"
    __table_args__ = (
        CheckConstraint("source_draft_version > 0", name="draft_version_positive"),
        ForeignKeyConstraint(
            ["definition_id", "mission_id"],
            ["mission_definitions.id", "mission_definitions.mission_id"],
            name="fk_mission_binding_definition_same_mission",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "source_draft_id", "source_draft_version", name="uq_mission_binding_draft_version"
        ),
        Index("ix_mission_bindings_definition", "definition_id", "bound_at"),
    )

    mission_id: Mapped[UUID] = mapped_column(nullable=False)
    definition_id: Mapped[UUID] = mapped_column(nullable=False)
    source_draft_id: Mapped[UUID] = mapped_column(
        ForeignKey("contribution_drafts.id", ondelete="RESTRICT"), nullable=False
    )
    source_draft_version: Mapped[int] = mapped_column(Integer, nullable=False)
    bound_by_actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    bound_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MissionProgressCheckpoint(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "mission_progress_checkpoints"
    __table_args__ = (
        CheckConstraint("accepted_count >= 0", name="accepted_count_nonnegative"),
        CheckConstraint("matched_event_count >= accepted_count", name="matched_count_consistent"),
        CheckConstraint("event_set_digest ~ '^[0-9a-f]{64}$'", name="event_set_digest_sha256"),
        ForeignKeyConstraint(
            ["definition_id", "mission_id"],
            ["mission_definitions.id", "mission_definitions.mission_id"],
            name="fk_mission_checkpoint_definition_same_mission",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "id",
            "definition_id",
            "mission_id",
            name="uq_mission_checkpoint_scope",
        ),
        UniqueConstraint("definition_id", "event_set_digest", name="uq_mission_progress_event_set"),
        Index("ix_mission_progress_built", "definition_id", "built_at"),
    )

    mission_id: Mapped[UUID] = mapped_column(nullable=False)
    definition_id: Mapped[UUID] = mapped_column(nullable=False)
    accepted_count: Mapped[int] = mapped_column(Integer, nullable=False)
    matched_event_count: Mapped[int] = mapped_column(Integer, nullable=False)
    event_set_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    built_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MissionProgressRecord(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "mission_progress_records"
    __table_args__ = (
        UniqueConstraint(
            "checkpoint_id", "repository", "pack_id", "record_id", name="uq_mission_progress_record"
        ),
        UniqueConstraint(
            "checkpoint_id", "accepted_event_id", name="uq_mission_progress_accepted_event"
        ),
        Index("ix_mission_progress_records_checkpoint", "checkpoint_id"),
    )

    checkpoint_id: Mapped[UUID] = mapped_column(
        ForeignKey("mission_progress_checkpoints.id", ondelete="RESTRICT"), nullable=False
    )
    accepted_event_id: Mapped[UUID] = mapped_column(
        ForeignKey("accepted_events.id", ondelete="RESTRICT"), nullable=False
    )
    repository: Mapped[str] = mapped_column(String(512), nullable=False)
    pack_id: Mapped[str] = mapped_column(String(160), nullable=False)
    record_id: Mapped[str] = mapped_column(String(160), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MissionProgressActivation(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "mission_progress_activations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["checkpoint_id", "definition_id", "mission_id"],
            [
                "mission_progress_checkpoints.id",
                "mission_progress_checkpoints.definition_id",
                "mission_progress_checkpoints.mission_id",
            ],
            name="fk_mission_activation_checkpoint_scope",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("definition_id", name="uq_mission_progress_activation_definition"),
    )

    mission_id: Mapped[UUID] = mapped_column(nullable=False)
    definition_id: Mapped[UUID] = mapped_column(nullable=False)
    checkpoint_id: Mapped[UUID] = mapped_column(nullable=False)
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
