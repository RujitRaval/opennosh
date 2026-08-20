"""Index community foods by pack for version-safe loader lookups.

Revision ID: 20260820_0003
Revises: 20260819_0002
Create Date: 2026-08-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260820_0003"
down_revision: str | None = "20260819_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_foods_community_pack_id",
        "foods_community",
        ["pack_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_foods_community_pack_id", table_name="foods_community")
