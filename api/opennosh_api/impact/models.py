from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import CHAR, CheckConstraint, DateTime, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from opennosh_api.orm import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class ImpactSnapshot(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "impact_snapshots"
    __table_args__ = (
        CheckConstraint("schema_version = '1.0'", name="schema_version_supported"),
        CheckConstraint("metric_definition_version = '1.0'", name="metric_version_supported"),
        CheckConstraint("state IN ('zero','live')", name="state_released"),
        CheckConstraint("digest ~ '^[0-9a-f]{64}$'", name="digest_sha256"),
        CheckConstraint("jsonb_typeof(snapshot_json) = 'object'", name="snapshot_json_object"),
        UniqueConstraint("digest", name="uq_impact_snapshots_digest"),
        UniqueConstraint("source_checkpoint_id", name="uq_impact_snapshots_checkpoint"),
        Index("ix_impact_snapshots_observed_at", "observed_at"),
    )

    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, server_default="1.0")
    metric_definition_version: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="1.0"
    )
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_checkpoint_id: Mapped[str] = mapped_column(String(160), nullable=False)
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)


__all__ = ["ImpactSnapshot"]
