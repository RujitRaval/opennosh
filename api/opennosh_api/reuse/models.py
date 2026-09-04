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


class ReuseDeclaration(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "reuse_declarations"
    __table_args__ = (
        CheckConstraint(
            "state IN ('community_declared','verification_pending','verified','withdrawn')",
            name="state_allowed",
        ),
        CheckConstraint("revision > 0", name="revision_positive"),
        CheckConstraint(
            "region_level IS NULL OR region_level IN ('country','macroregion')",
            name="region_level_allowed",
        ),
        CheckConstraint(
            "(region_level IS NULL) = (region_code IS NULL)",
            name="region_shape_complete",
        ),
        CheckConstraint(
            "(region_level = 'country' AND region_code ~ '^[A-Z]{2}$') OR "
            "(region_level = 'macroregion' AND region_code ~ '^[0-9]{3}$') OR "
            "region_level IS NULL",
            name="region_code_valid",
        ),
        CheckConstraint(
            "(state = 'withdrawn') = (withdrawn_at IS NOT NULL)",
            name="withdrawal_shape_valid",
        ),
        UniqueConstraint(
            "owner_actor_id",
            "organization_key",
            "project_key",
            name="uq_reuse_declaration_owner_project",
        ),
        Index("ix_reuse_declarations_owner_updated", "owner_actor_id", "updated_at"),
        Index("ix_reuse_declarations_state_updated", "state", "updated_at"),
    )

    owner_actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    organization_name: Mapped[str] = mapped_column(String(160), nullable=False)
    organization_key: Mapped[str] = mapped_column(String(160), nullable=False)
    project_name: Mapped[str] = mapped_column(String(160), nullable=False)
    project_key: Mapped[str] = mapped_column(String(160), nullable=False)
    project_url: Mapped[str | None] = mapped_column(String(2048))
    use_case: Mapped[str] = mapped_column(String(1000), nullable=False)
    region_level: Mapped[str | None] = mapped_column(String(16))
    region_code: Mapped[str | None] = mapped_column(String(3))
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReuseDeclarationEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "reuse_declaration_events"
    __table_args__ = (
        CheckConstraint("declaration_revision > 0", name="declaration_revision_positive"),
        CheckConstraint(
            "event_type IN ('declared','edited','submitted','verified','changes_requested',"
            "'rejected','withdrawn','restored')",
            name="event_type_allowed",
        ),
        CheckConstraint(
            "idempotency_key_hash ~ '^[0-9a-f]{64}$'",
            name="idempotency_key_hash_sha256",
        ),
        CheckConstraint("request_hash ~ '^[0-9a-f]{64}$'", name="request_hash_sha256"),
        CheckConstraint("jsonb_typeof(evidence_json) = 'object'", name="evidence_json_object"),
        UniqueConstraint(
            "actor_id",
            "idempotency_key_hash",
            name="uq_reuse_event_actor_idempotency",
        ),
        UniqueConstraint(
            "declaration_id",
            "declaration_revision",
            name="uq_reuse_event_declaration_revision",
        ),
        Index("ix_reuse_events_declaration_created", "declaration_id", "created_at"),
    )

    declaration_id: Mapped[UUID] = mapped_column(
        ForeignKey("reuse_declarations.id", ondelete="RESTRICT"), nullable=False
    )
    actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    declaration_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    reason: Mapped[str | None] = mapped_column(String(1000))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = ["ReuseDeclaration", "ReuseDeclarationEvent"]
