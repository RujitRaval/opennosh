"""add immutable privacy-safe impact snapshots

Revision ID: 20260904_0034
Revises: 20260903_0033
Create Date: 2026-09-04 04:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260904_0034"
down_revision: str | Sequence[str] | None = "20260903_0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "impact_snapshots",
        sa.Column("schema_version", sa.String(length=16), server_default="1.0", nullable=False),
        sa.Column(
            "metric_definition_version", sa.String(length=16), server_default="1.0", nullable=False
        ),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_checkpoint_id", sa.String(length=160), nullable=False),
        sa.Column(
            "snapshot_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("digest", sa.CHAR(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "schema_version = '1.0'", name=op.f("ck_impact_snapshots_schema_version_supported")
        ),
        sa.CheckConstraint(
            "metric_definition_version = '1.0'",
            name=op.f("ck_impact_snapshots_metric_version_supported"),
        ),
        sa.CheckConstraint(
            "state IN ('zero','live')", name=op.f("ck_impact_snapshots_state_released")
        ),
        sa.CheckConstraint(
            "digest ~ '^[0-9a-f]{64}$'", name=op.f("ck_impact_snapshots_digest_sha256")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(snapshot_json) = 'object'",
            name=op.f("ck_impact_snapshots_snapshot_json_object"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_impact_snapshots")),
        sa.UniqueConstraint("digest", name="uq_impact_snapshots_digest"),
        sa.UniqueConstraint("source_checkpoint_id", name="uq_impact_snapshots_checkpoint"),
    )
    op.create_index("ix_impact_snapshots_observed_at", "impact_snapshots", ["observed_at"])
    op.execute(
        """
        CREATE FUNCTION opennosh_impact_snapshot_immutable() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'impact snapshots are append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER impact_snapshots_immutable
        BEFORE UPDATE OR DELETE ON impact_snapshots
        FOR EACH ROW EXECUTE FUNCTION opennosh_impact_snapshot_immutable()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS impact_snapshots_immutable ON impact_snapshots")
    op.execute("DROP FUNCTION IF EXISTS opennosh_impact_snapshot_immutable()")
    op.drop_index("ix_impact_snapshots_observed_at", table_name="impact_snapshots")
    op.drop_table("impact_snapshots")
