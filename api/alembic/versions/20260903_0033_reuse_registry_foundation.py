"""add voluntary reuse declarations and immutable audit events

Revision ID: 20260903_0033
Revises: 20260902_0032
Create Date: 2026-09-03 22:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260903_0033"
down_revision: str | Sequence[str] | None = "20260902_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reuse_declarations",
        sa.Column("owner_actor_id", sa.Uuid(), nullable=False),
        sa.Column("organization_name", sa.String(length=160), nullable=False),
        sa.Column("organization_key", sa.String(length=160), nullable=False),
        sa.Column("project_name", sa.String(length=160), nullable=False),
        sa.Column("project_key", sa.String(length=160), nullable=False),
        sa.Column("project_url", sa.String(length=2048), nullable=True),
        sa.Column("use_case", sa.String(length=1000), nullable=False),
        sa.Column("region_level", sa.String(length=16), nullable=True),
        sa.Column("region_code", sa.String(length=3), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "state IN ('community_declared','verification_pending','verified','withdrawn')",
            name=op.f("ck_reuse_declarations_state_allowed"),
        ),
        sa.CheckConstraint("revision > 0", name=op.f("ck_reuse_declarations_revision_positive")),
        sa.CheckConstraint(
            "region_level IS NULL OR region_level IN ('country','macroregion')",
            name=op.f("ck_reuse_declarations_region_level_allowed"),
        ),
        sa.CheckConstraint(
            "(region_level IS NULL) = (region_code IS NULL)",
            name=op.f("ck_reuse_declarations_region_shape_complete"),
        ),
        sa.CheckConstraint(
            "(region_level = 'country' AND region_code ~ '^[A-Z]{2}$') OR "
            "(region_level = 'macroregion' AND region_code ~ '^[0-9]{3}$') OR "
            "region_level IS NULL",
            name=op.f("ck_reuse_declarations_region_code_valid"),
        ),
        sa.CheckConstraint(
            "(state = 'withdrawn') = (withdrawn_at IS NOT NULL)",
            name=op.f("ck_reuse_declarations_withdrawal_shape_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_actor_id"],
            ["users.id"],
            name=op.f("fk_reuse_declarations_owner_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reuse_declarations")),
        sa.UniqueConstraint(
            "owner_actor_id",
            "organization_key",
            "project_key",
            name="uq_reuse_declaration_owner_project",
        ),
    )
    op.create_index(
        "ix_reuse_declarations_owner_updated",
        "reuse_declarations",
        ["owner_actor_id", "updated_at"],
    )
    op.create_index(
        "ix_reuse_declarations_state_updated",
        "reuse_declarations",
        ["state", "updated_at"],
    )

    op.create_table(
        "reuse_declaration_events",
        sa.Column("declaration_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("declaration_revision", sa.Integer(), nullable=False),
        sa.Column("idempotency_key_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("request_hash", sa.CHAR(length=64), nullable=False),
        sa.Column(
            "evidence_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(length=1000), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "declaration_revision > 0",
            name=op.f("ck_reuse_declaration_events_declaration_revision_positive"),
        ),
        sa.CheckConstraint(
            "event_type IN ('declared','edited','submitted','verified','changes_requested',"
            "'rejected','withdrawn','restored')",
            name=op.f("ck_reuse_declaration_events_event_type_allowed"),
        ),
        sa.CheckConstraint(
            "idempotency_key_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_reuse_declaration_events_idempotency_key_hash_sha256"),
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_reuse_declaration_events_request_hash_sha256"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_json) = 'object'",
            name=op.f("ck_reuse_declaration_events_evidence_json_object"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_reuse_declaration_events_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["declaration_id"],
            ["reuse_declarations.id"],
            name=op.f("fk_reuse_declaration_events_declaration_id_reuse_declarations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reuse_declaration_events")),
        sa.UniqueConstraint(
            "actor_id",
            "idempotency_key_hash",
            name="uq_reuse_event_actor_idempotency",
        ),
        sa.UniqueConstraint(
            "declaration_id",
            "declaration_revision",
            name="uq_reuse_event_declaration_revision",
        ),
    )
    op.create_index(
        "ix_reuse_events_declaration_created",
        "reuse_declaration_events",
        ["declaration_id", "created_at"],
    )
    op.execute(
        """
        CREATE FUNCTION opennosh_reuse_event_immutable() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'reuse declaration events are append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER reuse_declaration_events_immutable
        BEFORE UPDATE OR DELETE ON reuse_declaration_events
        FOR EACH ROW EXECUTE FUNCTION opennosh_reuse_event_immutable()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS reuse_declaration_events_immutable ON reuse_declaration_events"
    )
    op.execute("DROP FUNCTION IF EXISTS opennosh_reuse_event_immutable()")
    op.drop_index("ix_reuse_events_declaration_created", table_name="reuse_declaration_events")
    op.drop_table("reuse_declaration_events")
    op.drop_index("ix_reuse_declarations_state_updated", table_name="reuse_declarations")
    op.drop_index("ix_reuse_declarations_owner_updated", table_name="reuse_declarations")
    op.drop_table("reuse_declarations")
