"""stabilize retained food search index latency

Revision ID: 20260905_0037
Revises: 20260904_0036
Create Date: 2026-09-05 02:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260905_0037"
down_revision: str | Sequence[str] | None = "20260904_0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEARCH_GIN_INDEXES = (
    "ix_food_search_snapshot_items_search_tsv",
    "ix_food_search_snapshot_items_source_id_trgm",
    "ix_food_search_snapshot_items_name_trgm",
    "ix_food_search_snapshot_items_name_local_trgm",
)


def upgrade() -> None:
    # Snapshot refreshes are bulk writes followed immediately by latency-sensitive
    # reads. Bypass GIN's pending list so a fresh snapshot is searchable at commit.
    for index_name in SEARCH_GIN_INDEXES:
        op.execute(f"ALTER INDEX {index_name} SET (fastupdate = off)")
        op.execute(f"SELECT gin_clean_pending_list('{index_name}'::regclass)")


def downgrade() -> None:
    for index_name in SEARCH_GIN_INDEXES:
        op.execute(f"ALTER INDEX {index_name} SET (fastupdate = on)")
