"""finalize retained food search statistics

Revision ID: 20260908_0039
Revises: 20260907_0038
Create Date: 2026-09-08 04:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260908_0039"
down_revision: str | Sequence[str] | None = "20260907_0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _replace_flush_function(*, analyze: bool) -> None:
    analyze_statement = "ANALYZE public.food_search_snapshot_items;" if analyze else ""
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.opennosh_flush_food_search_gin_pending_lists()
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
            {analyze_statement}
        END;
        $function$
        """
    )


def upgrade() -> None:
    # A bulk snapshot refresh changes the table distribution immediately before
    # latency-sensitive reads. Publish fresh planner statistics in the same
    # bounded SECURITY DEFINER finalizer that already flushes the reviewed GIN
    # indexes, so the runtime role cannot analyze arbitrary relations.
    _replace_flush_function(analyze=True)


def downgrade() -> None:
    _replace_flush_function(analyze=False)
