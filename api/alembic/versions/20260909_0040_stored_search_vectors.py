"""store snapshot search vectors without removing rolling-deploy indexes

Revision ID: 20260909_0040
Revises: 20260908_0039
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TSVECTOR

revision: str = "20260909_0040"
down_revision: str | Sequence[str] | None = "20260908_0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VECTOR = """
to_tsvector('simple'::regconfig,
    (((((coalesce(source_id, '') || ' ') || coalesce(name, '')) || ' ') ||
    coalesce(name_local, '')) || ' ') || coalesce(category, ''))
"""
NEW_INDEX = "ix_food_search_snapshot_items_stored_tsv"
OLD_INDEXES = (
    "ix_food_search_snapshot_items_search_tsv",
    "ix_food_search_snapshot_items_source_id_trgm",
    "ix_food_search_snapshot_items_name_trgm",
    "ix_food_search_snapshot_items_name_local_trgm",
)


def _finalizer(indexes: tuple[str, ...]) -> None:
    statements = "\n".join(
        f"PERFORM pg_catalog.gin_clean_pending_list('public.{name}'::regclass);" for name in indexes
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.opennosh_flush_food_search_gin_pending_lists()
        RETURNS void LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            {statements}
            ANALYZE public.food_search_snapshot_items;
        END;
        $function$
        """
    )


def upgrade() -> None:
    # Fail rather than queue an exclusive rewrite behind live traffic indefinitely.
    op.execute("SET LOCAL lock_timeout = '2s'")
    op.execute("SET LOCAL statement_timeout = '30s'")
    op.add_column(
        "food_search_snapshot_items",
        sa.Column("search_vector", TSVECTOR(), sa.Computed(VECTOR, persisted=True)),
    )
    op.create_index(
        NEW_INDEX,
        "food_search_snapshot_items",
        ["search_vector"],
        postgresql_using="gin",
        postgresql_with={"fastupdate": "on"},
    )
    # Old instances still use the expression index throughout the rolling deploy.
    _finalizer((*OLD_INDEXES, NEW_INDEX))


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '2s'")
    op.execute("SET LOCAL statement_timeout = '30s'")
    _finalizer(OLD_INDEXES)
    op.drop_index(NEW_INDEX, table_name="food_search_snapshot_items")
    op.drop_column("food_search_snapshot_items", "search_vector")
