"""add retained food search projection snapshots

Revision ID: 20260823_0011
Revises: 20260820_0010
Create Date: 2026-08-23 00:11:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260823_0011"
down_revision: str | Sequence[str] | None = "20260820_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "food_search_snapshots",
        sa.Column(
            "id",
            sa.Uuid(),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("ranking_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "ranking_version > 0",
            name=op.f("ck_food_search_snapshots_ranking_version_positive"),
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name=op.f("ck_food_search_snapshots_expiry_after_creation"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_food_search_snapshots")),
    )
    op.create_index(
        "ix_food_search_snapshots_created_at",
        "food_search_snapshots",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_food_search_snapshots_expires_at"),
        "food_search_snapshots",
        ["expires_at"],
        unique=False,
    )
    op.create_table(
        "food_search_snapshot_items",
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("name_local", sa.String(length=255), nullable=True),
        sa.Column("locale", sa.String(length=35), nullable=True),
        sa.Column("category", sa.String(length=255), nullable=True),
        sa.Column("license", sa.String(length=64), nullable=False),
        sa.Column("source_uri", sa.String(length=2048), nullable=True),
        sa.Column("source_license", sa.String(length=64), nullable=True),
        sa.Column("contributed_by", sa.String(length=100), nullable=True),
        sa.Column("pack_id", sa.String(length=160), nullable=True),
        sa.Column("pack_version", sa.String(length=64), nullable=True),
        sa.Column("provenance", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "source IN ('usda', 'community')",
            name=op.f("ck_food_search_snapshot_items_source_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["food_search_snapshots.id"],
            name=op.f(
                "fk_food_search_snapshot_items_snapshot_id_food_search_snapshots"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "snapshot_id",
            "source",
            "source_id",
            name=op.f("pk_food_search_snapshot_items"),
        ),
    )
    op.execute(
        """
        CREATE INDEX ix_food_search_snapshot_items_search_tsv
        ON food_search_snapshot_items
        USING gin (
            to_tsvector(
                'simple'::regconfig,
                (((((coalesce(source_id, '') || ' ') || coalesce(name, '')) || ' ') ||
                coalesce(name_local, '')) || ' ') || coalesce(category, '')
            )
        )
        """
    )
    for index_name, column_name in (
        ("ix_food_search_snapshot_items_source_id_trgm", "source_id"),
        ("ix_food_search_snapshot_items_name_trgm", "name"),
        ("ix_food_search_snapshot_items_name_local_trgm", "name_local"),
    ):
        op.execute(
            f"""
            CREATE INDEX {index_name}
            ON food_search_snapshot_items
            USING gin ({column_name} gin_trgm_ops)
            """
        )


def downgrade() -> None:
    op.drop_table("food_search_snapshot_items")
    op.drop_index(
        op.f("ix_food_search_snapshots_expires_at"),
        table_name="food_search_snapshots",
    )
    op.drop_index(
        "ix_food_search_snapshots_created_at",
        table_name="food_search_snapshots",
    )
    op.drop_table("food_search_snapshots")
