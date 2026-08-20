"""Add full-text and trigram indexes for unified food search.

Revision ID: 20260820_0004
Revises: 20260820_0003
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0004"
down_revision: str | None = "20260820_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_index(
        "ix_foods_reference_search_tsv",
        "foods_reference",
        [sa.text("to_tsvector('simple'::regconfig, coalesce(description, ''))")],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_foods_reference_description_trgm",
        "foods_reference",
        ["description"],
        postgresql_using="gin",
        postgresql_ops={"description": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_foods_community_search_tsv",
        "foods_community",
        [
            sa.text(
                "to_tsvector('simple'::regconfig, ((((("
                "coalesce(slug, ''::character varying)::text || ' '::text) || "
                "coalesce(name, ''::character varying)::text) || ' '::text) || "
                "coalesce(name_local, ''::character varying)::text) || ' '::text) || "
                "coalesce(category, ''::character varying)::text)"
            )
        ],
        postgresql_using="gin",
    )
    for index_name, column_name in (
        ("ix_foods_community_slug_trgm", "slug"),
        ("ix_foods_community_name_trgm", "name"),
        ("ix_foods_community_name_local_trgm", "name_local"),
    ):
        op.create_index(
            index_name,
            "foods_community",
            [column_name],
            postgresql_using="gin",
            postgresql_ops={column_name: "gin_trgm_ops"},
        )


def downgrade() -> None:
    for index_name, table_name in (
        ("ix_foods_community_name_local_trgm", "foods_community"),
        ("ix_foods_community_name_trgm", "foods_community"),
        ("ix_foods_community_slug_trgm", "foods_community"),
        ("ix_foods_community_search_tsv", "foods_community"),
        ("ix_foods_reference_description_trgm", "foods_reference"),
        ("ix_foods_reference_search_tsv", "foods_reference"),
    ):
        op.drop_index(index_name, table_name=table_name)
