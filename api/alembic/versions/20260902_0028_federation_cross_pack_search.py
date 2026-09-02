"""bind retained food search snapshots to verified federation release sets

Revision ID: 20260902_0028
Revises: 20260902_0027
Create Date: 2026-09-02 14:05:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260902_0028"
down_revision: str | Sequence[str] | None = "20260902_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "federation_projection_foods",
        sa.Column("equivalence_key", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "federation_projection_foods",
        sa.Column("nutrients_digest", sa.String(length=64), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_federation_projection_foods_equivalence_key_sha256"),
        "federation_projection_foods",
        "equivalence_key IS NULL OR equivalence_key ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        op.f("ck_federation_projection_foods_nutrients_digest_sha256"),
        "federation_projection_foods",
        "nutrients_digest IS NULL OR nutrients_digest ~ '^[0-9a-f]{64}$'",
    )

    op.add_column(
        "food_search_snapshots",
        sa.Column("federation_checkpoint_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "food_search_snapshots",
        sa.Column("release_set_digest", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "food_search_snapshots",
        sa.Column(
            "selected_pack_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.create_foreign_key(
        op.f(
            "fk_food_search_snapshots_federation_checkpoint_id_federation_projection_checkpoints"
        ),
        "food_search_snapshots",
        "federation_projection_checkpoints",
        ["federation_checkpoint_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_food_search_snapshots_release_set_binding_complete"),
        "food_search_snapshots",
        "(federation_checkpoint_id IS NULL) = (release_set_digest IS NULL)",
    )
    op.create_check_constraint(
        op.f("ck_food_search_snapshots_release_set_digest_sha256"),
        "food_search_snapshots",
        "release_set_digest IS NULL OR release_set_digest ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        op.f("ck_food_search_snapshots_selected_pack_ids_array"),
        "food_search_snapshots",
        "jsonb_typeof(selected_pack_ids) = 'array' AND jsonb_array_length(selected_pack_ids) <= 20",
    )
    op.create_index(
        "ix_food_search_snapshots_release_set",
        "food_search_snapshots",
        ["ranking_version", "federation_checkpoint_id", "created_at"],
        unique=False,
    )

    op.drop_constraint(
        op.f("ck_food_search_snapshot_items_source_allowed"),
        "food_search_snapshot_items",
        type_="check",
    )
    op.alter_column(
        "food_search_snapshot_items",
        "source_id",
        existing_type=sa.String(length=160),
        type_=sa.String(length=200),
        existing_nullable=False,
    )
    op.alter_column(
        "food_search_snapshot_items",
        "source_license",
        existing_type=sa.String(length=64),
        type_=sa.String(length=255),
        existing_nullable=True,
    )
    for column in (
        sa.Column("source_record_id", sa.String(length=120), nullable=True),
        sa.Column("verified_release_id", sa.Uuid(), nullable=True),
        sa.Column("release_version", sa.String(length=255), nullable=True),
        sa.Column("release_digest", sa.String(length=64), nullable=True),
        sa.Column("equivalence_group_id", sa.String(length=200), nullable=True),
        sa.Column("variant_id", sa.String(length=200), nullable=True),
        sa.Column("nutrients_digest", sa.String(length=64), nullable=True),
        sa.Column("conflict", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("variant_count", sa.Integer(), nullable=False, server_default="1"),
    ):
        op.add_column("food_search_snapshot_items", column)
    op.create_foreign_key(
        op.f(
            "fk_food_search_snapshot_items_verified_release_id_federation_verified_releases"
        ),
        "food_search_snapshot_items",
        "federation_verified_releases",
        ["verified_release_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_food_search_snapshot_items_source_allowed"),
        "food_search_snapshot_items",
        "source IN ('usda', 'community', 'federation')",
    )
    op.create_check_constraint(
        op.f("ck_food_search_snapshot_items_variant_count_positive"),
        "food_search_snapshot_items",
        "variant_count > 0",
    )
    op.create_check_constraint(
        op.f("ck_food_search_snapshot_items_federation_binding_complete"),
        "food_search_snapshot_items",
        "source <> 'federation' OR (source_record_id IS NOT NULL AND "
        "verified_release_id IS NOT NULL AND release_version IS NOT NULL AND "
        "release_digest IS NOT NULL AND equivalence_group_id IS NOT NULL AND "
        "variant_id IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_food_search_snapshot_items_release_digest_sha256"),
        "food_search_snapshot_items",
        "release_digest IS NULL OR release_digest ~ '^[0-9a-f]{64}$'",
    )
    op.create_index(
        "ix_food_search_snapshot_items_pack",
        "food_search_snapshot_items",
        ["snapshot_id", "pack_id"],
        unique=False,
    )
    op.create_index(
        "ix_food_search_snapshot_items_equivalence",
        "food_search_snapshot_items",
        ["snapshot_id", "equivalence_group_id"],
        unique=False,
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM food_search_snapshot_items WHERE source = 'federation'
            ) OR EXISTS (
                SELECT 1
                FROM food_search_snapshots
                WHERE federation_checkpoint_id IS NOT NULL
                   OR release_set_digest IS NOT NULL
                   OR selected_pack_ids <> '[]'::jsonb
            ) OR EXISTS (
                SELECT 1
                FROM federation_projection_foods
                WHERE equivalence_key IS NOT NULL OR nutrients_digest IS NOT NULL
            ) THEN
                RAISE EXCEPTION
                    'refusing downgrade while federation search identities exist';
            END IF;
        END
        $$
        """
    )
    op.drop_index(
        "ix_food_search_snapshot_items_equivalence",
        table_name="food_search_snapshot_items",
    )
    op.drop_index(
        "ix_food_search_snapshot_items_pack",
        table_name="food_search_snapshot_items",
    )
    for name in (
        "release_digest_sha256",
        "federation_binding_complete",
        "variant_count_positive",
        "source_allowed",
    ):
        op.drop_constraint(
            op.f(f"ck_food_search_snapshot_items_{name}"),
            "food_search_snapshot_items",
            type_="check",
        )
    op.drop_constraint(
        op.f(
            "fk_food_search_snapshot_items_verified_release_id_federation_verified_releases"
        ),
        "food_search_snapshot_items",
        type_="foreignkey",
    )
    for column in (
        "variant_count",
        "conflict",
        "nutrients_digest",
        "variant_id",
        "equivalence_group_id",
        "release_digest",
        "release_version",
        "verified_release_id",
        "source_record_id",
    ):
        op.drop_column("food_search_snapshot_items", column)
    op.alter_column(
        "food_search_snapshot_items",
        "source_license",
        existing_type=sa.String(length=255),
        type_=sa.String(length=64),
        existing_nullable=True,
    )
    op.alter_column(
        "food_search_snapshot_items",
        "source_id",
        existing_type=sa.String(length=200),
        type_=sa.String(length=160),
        existing_nullable=False,
    )
    op.create_check_constraint(
        op.f("ck_food_search_snapshot_items_source_allowed"),
        "food_search_snapshot_items",
        "source IN ('usda', 'community')",
    )

    op.drop_index(
        "ix_food_search_snapshots_release_set",
        table_name="food_search_snapshots",
    )
    for name in (
        "selected_pack_ids_array",
        "release_set_digest_sha256",
        "release_set_binding_complete",
    ):
        op.drop_constraint(
            op.f(f"ck_food_search_snapshots_{name}"),
            "food_search_snapshots",
            type_="check",
        )
    op.drop_constraint(
        op.f(
            "fk_food_search_snapshots_federation_checkpoint_id_federation_projection_checkpoints"
        ),
        "food_search_snapshots",
        type_="foreignkey",
    )
    op.drop_column("food_search_snapshots", "selected_pack_ids")
    op.drop_column("food_search_snapshots", "release_set_digest")
    op.drop_column("food_search_snapshots", "federation_checkpoint_id")

    op.drop_constraint(
        op.f("ck_federation_projection_foods_nutrients_digest_sha256"),
        "federation_projection_foods",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_federation_projection_foods_equivalence_key_sha256"),
        "federation_projection_foods",
        type_="check",
    )
    op.drop_column("federation_projection_foods", "nutrients_digest")
    op.drop_column("federation_projection_foods", "equivalence_key")
