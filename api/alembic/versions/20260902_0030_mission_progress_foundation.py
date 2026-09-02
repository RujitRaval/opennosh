"""add versioned mission facts and accepted-event progress projections

Revision ID: 20260902_0030
Revises: 20260902_0029
Create Date: 2026-09-02 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260902_0030"
down_revision: str | Sequence[str] | None = "20260902_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _identity_columns() -> tuple[sa.Column[object], sa.Column[object]]:
    return (
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def upgrade() -> None:
    op.create_table(
        "mission_definitions",
        sa.Column("mission_id", sa.Uuid(), nullable=False),
        sa.Column("definition_version", sa.Integer(), nullable=False),
        sa.Column("prior_definition_id", sa.Uuid(), nullable=True),
        sa.Column("gap_kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("summary", sa.String(length=1000), nullable=False),
        sa.Column("target_pack_id", sa.String(length=160), nullable=False),
        sa.Column("target_dataset", sa.String(length=256), nullable=False),
        sa.Column("acceptance_target", sa.Integer(), nullable=False),
        sa.Column("acceptance_criteria", sa.String(length=2000), nullable=False),
        sa.Column("definition_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("proposed_by_actor_id", sa.Uuid(), nullable=False),
        sa.Column("responsible_steward_actor_id", sa.Uuid(), nullable=False),
        sa.Column("defined_at", sa.DateTime(timezone=True), nullable=False),
        *_identity_columns(),
        sa.CheckConstraint(
            "definition_version > 0",
            name=op.f("ck_mission_definitions_definition_version_positive"),
        ),
        sa.CheckConstraint(
            "gap_kind IN ('cuisine','locale','institution','dataset','missing_field')",
            name=op.f("ck_mission_definitions_gap_kind_allowed"),
        ),
        sa.CheckConstraint(
            "acceptance_target BETWEEN 1 AND 100000",
            name=op.f("ck_mission_definitions_target_bounded"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(definition_json) = 'object'",
            name=op.f("ck_mission_definitions_definition_json_object"),
        ),
        sa.CheckConstraint(
            "prior_definition_id IS NULL OR prior_definition_id != id",
            name=op.f("ck_mission_definitions_prior_definition_not_self"),
        ),
        sa.CheckConstraint(
            "(definition_version = 1) = (prior_definition_id IS NULL)",
            name=op.f("ck_mission_definitions_definition_chain_shape"),
        ),
        sa.ForeignKeyConstraint(
            ["prior_definition_id", "mission_id"],
            ["mission_definitions.id", "mission_definitions.mission_id"],
            name="fk_mission_definition_prior_same_mission",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["proposed_by_actor_id"],
            ["users.id"],
            name=op.f("fk_mission_definitions_proposed_by_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["responsible_steward_actor_id"],
            ["users.id"],
            name=op.f("fk_mission_definitions_responsible_steward_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mission_definitions")),
        sa.UniqueConstraint("id", "mission_id", name="uq_mission_definition_id_mission"),
        sa.UniqueConstraint(
            "mission_id", "definition_version", name="uq_mission_definition_version"
        ),
        sa.UniqueConstraint("prior_definition_id", name="uq_mission_definition_successor"),
    )
    op.create_index(
        "ix_mission_definitions_pack_version",
        "mission_definitions",
        ["target_pack_id", "defined_at"],
    )

    op.create_table(
        "mission_lifecycle_events",
        sa.Column("mission_id", sa.Uuid(), nullable=False),
        sa.Column("definition_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("prior_event_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=24), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("public_reason", sa.String(length=2000), nullable=False),
        sa.Column("next_review_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("release_receipt_digest", sa.String(length=64), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        *_identity_columns(),
        sa.CheckConstraint(
            "sequence > 0", name=op.f("ck_mission_lifecycle_events_sequence_positive")
        ),
        sa.CheckConstraint(
            "action IN ('propose','approve','pause','resume','complete','release','close')",
            name=op.f("ck_mission_lifecycle_events_action_allowed"),
        ),
        sa.CheckConstraint(
            "(action = 'pause' AND next_review_at IS NOT NULL) OR "
            "(action != 'pause' AND next_review_at IS NULL)",
            name=op.f("ck_mission_lifecycle_events_pause_review_shape"),
        ),
        sa.CheckConstraint(
            "(action = 'release' AND release_receipt_digest IS NOT NULL) OR "
            "(action != 'release' AND release_receipt_digest IS NULL)",
            name=op.f("ck_mission_lifecycle_events_release_receipt_shape"),
        ),
        sa.CheckConstraint(
            "prior_event_id IS NULL OR prior_event_id != id",
            name=op.f("ck_mission_lifecycle_events_prior_event_not_self"),
        ),
        sa.CheckConstraint(
            "(sequence = 1) = (prior_event_id IS NULL)",
            name=op.f("ck_mission_lifecycle_events_event_chain_shape"),
        ),
        sa.CheckConstraint(
            "(sequence = 1) = (action = 'propose')",
            name=op.f("ck_mission_lifecycle_events_proposal_first"),
        ),
        sa.ForeignKeyConstraint(
            ["definition_id", "mission_id"],
            ["mission_definitions.id", "mission_definitions.mission_id"],
            name="fk_mission_lifecycle_definition_same_mission",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["prior_event_id", "mission_id"],
            ["mission_lifecycle_events.id", "mission_lifecycle_events.mission_id"],
            name="fk_mission_lifecycle_prior_same_mission",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_mission_lifecycle_events_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["release_receipt_digest"],
            ["publication_receipts.receipt_digest"],
            name=op.f("fk_mission_lifecycle_events_release_receipt_digest_publication_receipts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mission_lifecycle_events")),
        sa.UniqueConstraint("id", "mission_id", name="uq_mission_lifecycle_id_mission"),
        sa.UniqueConstraint("mission_id", "sequence", name="uq_mission_lifecycle_sequence"),
        sa.UniqueConstraint("prior_event_id", name="uq_mission_lifecycle_successor"),
    )
    op.create_index(
        "ix_mission_lifecycle_latest",
        "mission_lifecycle_events",
        ["mission_id", "sequence"],
    )

    op.create_table(
        "mission_contribution_bindings",
        sa.Column("mission_id", sa.Uuid(), nullable=False),
        sa.Column("definition_id", sa.Uuid(), nullable=False),
        sa.Column("source_draft_id", sa.Uuid(), nullable=False),
        sa.Column("source_draft_version", sa.Integer(), nullable=False),
        sa.Column("bound_by_actor_id", sa.Uuid(), nullable=False),
        sa.Column("bound_at", sa.DateTime(timezone=True), nullable=False),
        *_identity_columns(),
        sa.CheckConstraint(
            "source_draft_version > 0",
            name=op.f("ck_mission_contribution_bindings_draft_version_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["definition_id", "mission_id"],
            ["mission_definitions.id", "mission_definitions.mission_id"],
            name="fk_mission_binding_definition_same_mission",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_draft_id"],
            ["contribution_drafts.id"],
            name=op.f("fk_mission_contribution_bindings_source_draft_id_contribution_drafts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["bound_by_actor_id"],
            ["users.id"],
            name=op.f("fk_mission_contribution_bindings_bound_by_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mission_contribution_bindings")),
        sa.UniqueConstraint(
            "source_draft_id",
            "source_draft_version",
            name="uq_mission_binding_draft_version",
        ),
    )
    op.create_index(
        "ix_mission_bindings_definition",
        "mission_contribution_bindings",
        ["definition_id", "bound_at"],
    )

    op.create_table(
        "mission_progress_checkpoints",
        sa.Column("mission_id", sa.Uuid(), nullable=False),
        sa.Column("definition_id", sa.Uuid(), nullable=False),
        sa.Column("accepted_count", sa.Integer(), nullable=False),
        sa.Column("matched_event_count", sa.Integer(), nullable=False),
        sa.Column("event_set_digest", sa.String(length=64), nullable=False),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=False),
        *_identity_columns(),
        sa.CheckConstraint(
            "accepted_count >= 0",
            name=op.f("ck_mission_progress_checkpoints_accepted_count_nonnegative"),
        ),
        sa.CheckConstraint(
            "matched_event_count >= accepted_count",
            name=op.f("ck_mission_progress_checkpoints_matched_count_consistent"),
        ),
        sa.CheckConstraint(
            "event_set_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_mission_progress_checkpoints_event_set_digest_sha256"),
        ),
        sa.ForeignKeyConstraint(
            ["definition_id", "mission_id"],
            ["mission_definitions.id", "mission_definitions.mission_id"],
            name="fk_mission_checkpoint_definition_same_mission",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mission_progress_checkpoints")),
        sa.UniqueConstraint(
            "id", "definition_id", "mission_id", name="uq_mission_checkpoint_scope"
        ),
        sa.UniqueConstraint(
            "definition_id", "event_set_digest", name="uq_mission_progress_event_set"
        ),
    )
    op.create_index(
        "ix_mission_progress_built",
        "mission_progress_checkpoints",
        ["definition_id", "built_at"],
    )

    op.create_table(
        "mission_progress_records",
        sa.Column("checkpoint_id", sa.Uuid(), nullable=False),
        sa.Column("accepted_event_id", sa.Uuid(), nullable=False),
        sa.Column("repository", sa.String(length=512), nullable=False),
        sa.Column("pack_id", sa.String(length=160), nullable=False),
        sa.Column("record_id", sa.String(length=160), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        *_identity_columns(),
        sa.ForeignKeyConstraint(
            ["checkpoint_id"],
            ["mission_progress_checkpoints.id"],
            name=op.f("fk_mission_progress_records_checkpoint_id_mission_progress_checkpoints"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["accepted_event_id"],
            ["accepted_events.id"],
            name=op.f("fk_mission_progress_records_accepted_event_id_accepted_events"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mission_progress_records")),
        sa.UniqueConstraint(
            "checkpoint_id",
            "repository",
            "pack_id",
            "record_id",
            name="uq_mission_progress_record",
        ),
        sa.UniqueConstraint(
            "checkpoint_id",
            "accepted_event_id",
            name="uq_mission_progress_accepted_event",
        ),
    )
    op.create_index(
        "ix_mission_progress_records_checkpoint",
        "mission_progress_records",
        ["checkpoint_id"],
    )

    op.create_table(
        "mission_progress_activations",
        sa.Column("mission_id", sa.Uuid(), nullable=False),
        sa.Column("definition_id", sa.Uuid(), nullable=False),
        sa.Column("checkpoint_id", sa.Uuid(), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        *_identity_columns(),
        sa.ForeignKeyConstraint(
            ["checkpoint_id", "definition_id", "mission_id"],
            [
                "mission_progress_checkpoints.id",
                "mission_progress_checkpoints.definition_id",
                "mission_progress_checkpoints.mission_id",
            ],
            name="fk_mission_activation_checkpoint_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mission_progress_activations")),
        sa.UniqueConstraint("definition_id", name="uq_mission_progress_activation_definition"),
    )

    op.execute(
        """
        CREATE FUNCTION prohibit_mission_fact_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'mission facts are append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table in (
        "mission_definitions",
        "mission_lifecycle_events",
        "mission_contribution_bindings",
        "mission_progress_checkpoints",
        "mission_progress_records",
    ):
        op.execute(
            f"CREATE TRIGGER guard_append_only_{table} BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prohibit_mission_fact_mutation()"
        )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM mission_definitions) "
        "THEN RAISE EXCEPTION 'refusing downgrade while mission facts exist'; "
        "END IF; END $$"
    )
    op.drop_table("mission_progress_activations")
    for table in (
        "mission_progress_records",
        "mission_progress_checkpoints",
        "mission_contribution_bindings",
        "mission_lifecycle_events",
        "mission_definitions",
    ):
        op.execute(f"DROP TRIGGER guard_append_only_{table} ON {table}")
    op.drop_table("mission_progress_records")
    op.drop_table("mission_progress_checkpoints")
    op.drop_table("mission_contribution_bindings")
    op.drop_table("mission_lifecycle_events")
    op.drop_table("mission_definitions")
    op.execute("DROP FUNCTION prohibit_mission_fact_mutation()")
