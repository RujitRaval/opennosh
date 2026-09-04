"""add append-only public status and incident evidence

Revision ID: 20260904_0036
Revises: 20260904_0035
Create Date: 2026-09-04 06:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260904_0036"
down_revision: str | Sequence[str] | None = "20260904_0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create_immutable_trigger(table: str) -> None:
    op.execute(
        f"CREATE TRIGGER guard_append_only_{table} "
        f"BEFORE UPDATE OR DELETE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION opennosh_public_operations_immutable()"
    )


def upgrade() -> None:
    op.create_table(
        "public_component_observations",
        sa.Column("component_id", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("successful", sa.Boolean(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_digest", sa.CHAR(length=64), nullable=False),
        sa.Column(
            "affected_versions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "component_id ~ '^[a-z0-9]+(-[a-z0-9]+)*$'",
            name=op.f("ck_public_component_observations_component_id_safe"),
        ),
        sa.CheckConstraint(
            "state IN ('operational','degraded','outage','maintenance')",
            name=op.f("ck_public_component_observations_state_observed"),
        ),
        sa.CheckConstraint(
            "evidence_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_public_component_observations_evidence_digest_sha256"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(affected_versions) = 'array'",
            name=op.f("ck_public_component_observations_affected_versions_array"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_public_component_observations")),
        sa.UniqueConstraint(
            "component_id",
            "observed_at",
            "evidence_digest",
            name="uq_public_component_observation_proof",
        ),
    )
    op.create_index(
        "ix_public_component_observations_latest",
        "public_component_observations",
        ["component_id", "observed_at"],
    )

    op.create_table(
        "public_incidents",
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_public_incidents")),
    )
    op.create_index("ix_public_incidents_opened_at", "public_incidents", ["opened_at"])

    op.create_table(
        "public_incident_events",
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("public_summary", sa.String(length=1000), nullable=False),
        sa.Column(
            "affected_component_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "affected_versions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("guidance", sa.String(length=1000), nullable=False),
        sa.Column(
            "recovery_evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("event_digest", sa.CHAR(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "sequence > 0", name=op.f("ck_public_incident_events_sequence_positive")
        ),
        sa.CheckConstraint(
            "state IN ('investigating','identified','monitoring','resolved')",
            name=op.f("ck_public_incident_events_state_allowed"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(affected_component_ids) = 'array'",
            name=op.f("ck_public_incident_events_affected_component_ids_array"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(affected_versions) = 'array'",
            name=op.f("ck_public_incident_events_affected_versions_array"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(recovery_evidence) = 'object'",
            name=op.f("ck_public_incident_events_recovery_evidence_object"),
        ),
        sa.CheckConstraint(
            "event_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_public_incident_events_event_digest_sha256"),
        ),
        sa.CheckConstraint(
            "(state = 'resolved') = (recovery_evidence <> '{}'::jsonb)",
            name=op.f("ck_public_incident_events_recovery_evidence_shape"),
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["public_incidents.id"],
            name=op.f("fk_public_incident_events_incident_id_public_incidents"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_public_incident_events")),
        sa.UniqueConstraint("incident_id", "sequence", name="uq_public_incident_event_sequence"),
        sa.UniqueConstraint("incident_id", "event_digest", name="uq_public_incident_event_digest"),
    )
    op.create_index(
        "ix_public_incident_events_latest",
        "public_incident_events",
        ["incident_id", "sequence"],
    )

    op.execute(
        """
        CREATE FUNCTION opennosh_public_operations_immutable() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'public operations evidence is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table in (
        "public_component_observations",
        "public_incidents",
        "public_incident_events",
    ):
        _create_immutable_trigger(table)


def downgrade() -> None:
    for table in (
        "public_incident_events",
        "public_incidents",
        "public_component_observations",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS guard_append_only_{table} ON {table}")
    op.execute("DROP FUNCTION IF EXISTS opennosh_public_operations_immutable()")
    op.drop_index("ix_public_incident_events_latest", table_name="public_incident_events")
    op.drop_table("public_incident_events")
    op.drop_index("ix_public_incidents_opened_at", table_name="public_incidents")
    op.drop_table("public_incidents")
    op.drop_index(
        "ix_public_component_observations_latest",
        table_name="public_component_observations",
    )
    op.drop_table("public_component_observations")
