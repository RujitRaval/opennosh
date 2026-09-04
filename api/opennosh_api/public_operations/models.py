from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CHAR,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from opennosh_api.orm import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class PublicComponentObservation(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "public_component_observations"
    __table_args__ = (
        CheckConstraint(
            "component_id ~ '^[a-z0-9]+(-[a-z0-9]+)*$'",
            name="component_id_safe",
        ),
        CheckConstraint(
            "state IN ('operational','degraded','outage','maintenance')",
            name="state_observed",
        ),
        CheckConstraint(
            "evidence_digest ~ '^[0-9a-f]{64}$'",
            name="evidence_digest_sha256",
        ),
        CheckConstraint(
            "jsonb_typeof(affected_versions) = 'array'",
            name="affected_versions_array",
        ),
        UniqueConstraint(
            "component_id",
            "observed_at",
            "evidence_digest",
            name="uq_public_component_observation_proof",
        ),
        Index(
            "ix_public_component_observations_latest",
            "component_id",
            "observed_at",
        ),
    )

    component_id: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    successful: Mapped[bool] = mapped_column(Boolean, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    affected_versions: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )


class PublicIncident(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "public_incidents"
    __table_args__ = (Index("ix_public_incidents_opened_at", "opened_at"),)

    title: Mapped[str] = mapped_column(String(160), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PublicIncidentEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "public_incident_events"
    __table_args__ = (
        CheckConstraint("sequence > 0", name="sequence_positive"),
        CheckConstraint(
            "state IN ('investigating','identified','monitoring','resolved')",
            name="state_allowed",
        ),
        CheckConstraint(
            "jsonb_typeof(affected_component_ids) = 'array'",
            name="affected_component_ids_array",
        ),
        CheckConstraint(
            "jsonb_typeof(affected_versions) = 'array'",
            name="affected_versions_array",
        ),
        CheckConstraint(
            "jsonb_typeof(recovery_evidence) = 'object'",
            name="recovery_evidence_object",
        ),
        CheckConstraint(
            "event_digest ~ '^[0-9a-f]{64}$'",
            name="event_digest_sha256",
        ),
        CheckConstraint(
            "(state = 'resolved') = (recovery_evidence <> '{}'::jsonb)",
            name="recovery_evidence_shape",
        ),
        UniqueConstraint("incident_id", "sequence", name="uq_public_incident_event_sequence"),
        UniqueConstraint("incident_id", "event_digest", name="uq_public_incident_event_digest"),
        Index("ix_public_incident_events_latest", "incident_id", "sequence"),
    )

    incident_id: Mapped[UUID] = mapped_column(
        ForeignKey("public_incidents.id", ondelete="RESTRICT"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    public_summary: Mapped[str] = mapped_column(String(1000), nullable=False)
    affected_component_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    affected_versions: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    guidance: Mapped[str] = mapped_column(String(1000), nullable=False)
    recovery_evidence: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    event_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = [
    "PublicComponentObservation",
    "PublicIncident",
    "PublicIncidentEvent",
]
