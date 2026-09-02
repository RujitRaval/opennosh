"""add append-only pack installation state and installed projections

Revision ID: 20260902_0029
Revises: 20260902_0028
Create Date: 2026-09-02 16:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260902_0029"
down_revision: str | Sequence[str] | None = "20260902_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "federation_pack_installation_events",
        sa.Column("repository_id", sa.BigInteger(), nullable=False),
        sa.Column("pack_id", sa.String(length=160), nullable=False),
        sa.Column("verified_release_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("prior_event_id", sa.Uuid(), nullable=True),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("reason_digest", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "repository_id > 0",
            name=op.f("ck_federation_pack_installation_events_repository_id_positive"),
        ),
        sa.CheckConstraint(
            "action IN ('install','update','rollback','remove')",
            name=op.f("ck_federation_pack_installation_events_action_allowed"),
        ),
        sa.CheckConstraint(
            "generation > 0",
            name=op.f("ck_federation_pack_installation_events_generation_positive"),
        ),
        sa.CheckConstraint(
            "reason_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_federation_pack_installation_events_reason_digest_sha256"),
        ),
        sa.CheckConstraint(
            "(action = 'remove') = (verified_release_id IS NULL)",
            name=op.f("ck_federation_pack_installation_events_release_binding_matches_action"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_federation_pack_installation_events_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["prior_event_id"],
            ["federation_pack_installation_events.id"],
            name=op.f(
                "fk_federation_pack_installation_events_prior_event_id_federation_pack_installation_events"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["verified_release_id"],
            ["federation_verified_releases.id"],
            name=op.f(
                "fk_federation_pack_installation_events_verified_release_id_federation_verified_releases"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_federation_pack_installation_events")),
        sa.UniqueConstraint(
            "repository_id", "pack_id", "generation", name="uq_federation_install_scope_gen"
        ),
    )
    op.create_index(
        "ix_federation_install_scope_latest",
        "federation_pack_installation_events",
        ["repository_id", "pack_id", "generation"],
    )
    op.execute(
        "CREATE TRIGGER guard_append_only_federation_pack_installation_events "
        "BEFORE UPDATE OR DELETE ON federation_pack_installation_events "
        "FOR EACH ROW EXECUTE FUNCTION prohibit_federation_projection_fact_mutation()"
    )

    op.add_column(
        "federation_projection_checkpoints",
        sa.Column("mode", sa.String(length=16), nullable=False, server_default="registry"),
    )
    op.drop_constraint(
        "uq_federation_projection_release_set", "federation_projection_checkpoints", type_="unique"
    )
    op.drop_constraint(
        op.f("ck_federation_projection_checkpoints_release_count_positive"),
        "federation_projection_checkpoints",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_federation_projection_checkpoints_record_count_positive"),
        "federation_projection_checkpoints",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_federation_projection_checkpoints_mode_allowed"),
        "federation_projection_checkpoints",
        "mode IN ('registry','installed')",
    )
    op.create_check_constraint(
        op.f("ck_federation_projection_checkpoints_release_count_nonnegative"),
        "federation_projection_checkpoints",
        "release_count >= 0",
    )
    op.create_check_constraint(
        op.f("ck_federation_projection_checkpoints_record_count_nonnegative"),
        "federation_projection_checkpoints",
        "record_count >= 0",
    )
    op.create_check_constraint(
        op.f("ck_federation_projection_checkpoints_projection_counts_consistent"),
        "federation_projection_checkpoints",
        "(release_count = 0 AND record_count = 0) OR "
        "(release_count > 0 AND record_count > 0)",
    )
    op.create_check_constraint(
        op.f("ck_federation_projection_checkpoints_registry_projection_nonempty"),
        "federation_projection_checkpoints",
        "mode = 'installed' OR (release_count > 0 AND record_count > 0)",
    )
    op.create_unique_constraint(
        "uq_federation_projection_mode_release_set",
        "federation_projection_checkpoints",
        ["mode", "release_set_digest"],
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM federation_pack_installation_events) "
        "OR EXISTS (SELECT 1 FROM federation_projection_checkpoints WHERE mode = 'installed') "
        "THEN RAISE EXCEPTION 'refusing downgrade while pack installation facts exist'; "
        "END IF; END $$"
    )
    op.drop_constraint(
        "uq_federation_projection_mode_release_set",
        "federation_projection_checkpoints",
        type_="unique",
    )
    for name in (
        "registry_projection_nonempty",
        "projection_counts_consistent",
        "record_count_nonnegative",
        "release_count_nonnegative",
        "mode_allowed",
    ):
        op.drop_constraint(
            op.f(f"ck_federation_projection_checkpoints_{name}"),
            "federation_projection_checkpoints",
            type_="check",
        )
    op.create_check_constraint(
        op.f("ck_federation_projection_checkpoints_record_count_positive"),
        "federation_projection_checkpoints",
        "record_count > 0",
    )
    op.create_check_constraint(
        op.f("ck_federation_projection_checkpoints_release_count_positive"),
        "federation_projection_checkpoints",
        "release_count > 0",
    )
    op.create_unique_constraint(
        "uq_federation_projection_release_set",
        "federation_projection_checkpoints",
        ["release_set_digest"],
    )
    op.drop_column("federation_projection_checkpoints", "mode")
    op.execute(
        "DROP TRIGGER guard_append_only_federation_pack_installation_events "
        "ON federation_pack_installation_events"
    )
    op.drop_index(
        "ix_federation_install_scope_latest", table_name="federation_pack_installation_events"
    )
    op.drop_table("federation_pack_installation_events")
