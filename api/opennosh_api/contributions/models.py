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
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from opennosh_api.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class ContributionDraft(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "contribution_drafts"
    __table_args__ = (
        CheckConstraint("draft_version > 0", name="draft_version_positive"),
        CheckConstraint("workflow_version = '1'", name="workflow_version_supported"),
        CheckConstraint(
            "review_state IN ('draft', 'in_review', 'changes_requested', 'approved', "
            "'publication_pending', 'published')",
            name="review_state_allowed",
        ),
        UniqueConstraint(
            "user_id",
            "client_draft_id",
            name="uq_contribution_drafts_user_client_draft_unique",
        ),
        Index("ix_contribution_drafts_user_updated", "user_id", "updated_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_draft_id: Mapped[str | None] = mapped_column(String(120))
    workflow_version: Mapped[str] = mapped_column(String(16), nullable=False, server_default="1")
    draft_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    review_state: Mapped[str] = mapped_column(String(32), nullable=False, server_default="draft")
    fields_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    duplicate_candidates_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    submission_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), unique=True)
    submission_key_hash: Mapped[str | None] = mapped_column(String(64))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ContributionDraftOperation(Base):
    __tablename__ = "contribution_draft_operations"

    draft_id: Mapped[UUID] = mapped_column(
        ForeignKey("contribution_drafts.id", ondelete="CASCADE"), primary_key=True
    )
    operation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    resulting_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
