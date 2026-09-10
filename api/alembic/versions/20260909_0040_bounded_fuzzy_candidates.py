"""index a safe upper bound for fuzzy name candidates without rewriting rows

Revision ID: 20260909_0040
Revises: 20260908_0039
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260909_0040"
down_revision: str | Sequence[str] | None = "20260908_0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "ix_food_search_snapshot_items_name_trigram_count"


def upgrade() -> None:
    # Online construction keeps ordinary catalogue reads and writes available.
    # A cancelled concurrent build can leave an invalid index; a retry repairs it.
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '2s'")
        op.execute("SET statement_timeout = '120s'")
        try:
            valid = op.get_bind().execute(
                sa.text(
                    "SELECT indisvalid FROM pg_catalog.pg_index "
                    "WHERE indexrelid = to_regclass(:name)"
                ),
                {"name": f"public.{INDEX}"},
            ).scalar_one_or_none()
            if valid is False:
                op.execute(f"DROP INDEX CONCURRENTLY public.{INDEX}")
            op.execute(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX} "
                "ON public.food_search_snapshot_items (cardinality(show_trgm(name)))"
            )
            op.execute("ANALYZE public.food_search_snapshot_items")
        finally:
            op.execute("RESET lock_timeout")
            op.execute("RESET statement_timeout")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '2s'")
        op.execute("SET statement_timeout = '120s'")
        try:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{INDEX}")
        finally:
            op.execute("RESET lock_timeout")
            op.execute("RESET statement_timeout")
