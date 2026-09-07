"""buffer and flush retained food search index writes

Revision ID: 20260907_0038
Revises: 20260905_0037
Create Date: 2026-09-07 23:30:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260907_0038"
down_revision: str | Sequence[str] | None = "20260905_0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEARCH_GIN_INDEXES = (
    "ix_food_search_snapshot_items_search_tsv",
    "ix_food_search_snapshot_items_source_id_trgm",
    "ix_food_search_snapshot_items_name_trgm",
    "ix_food_search_snapshot_items_name_local_trgm",
)


def upgrade() -> None:
    for index_name in SEARCH_GIN_INDEXES:
        op.execute(f"ALTER INDEX {index_name} SET (fastupdate = on)")

    op.execute(
        """
        CREATE FUNCTION public.opennosh_flush_food_search_gin_pending_lists()
        RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            PERFORM pg_catalog.gin_clean_pending_list(
                'public.ix_food_search_snapshot_items_search_tsv'::regclass
            );
            PERFORM pg_catalog.gin_clean_pending_list(
                'public.ix_food_search_snapshot_items_source_id_trgm'::regclass
            );
            PERFORM pg_catalog.gin_clean_pending_list(
                'public.ix_food_search_snapshot_items_name_trgm'::regclass
            );
            PERFORM pg_catalog.gin_clean_pending_list(
                'public.ix_food_search_snapshot_items_name_local_trgm'::regclass
            );
        END;
        $function$
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.opennosh_flush_food_search_gin_pending_lists() FROM PUBLIC"
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION public.opennosh_flush_food_search_gin_pending_lists()")
    for index_name in SEARCH_GIN_INDEXES:
        op.execute(f"SELECT gin_clean_pending_list('{index_name}'::regclass)")
        op.execute(f"ALTER INDEX {index_name} SET (fastupdate = off)")
