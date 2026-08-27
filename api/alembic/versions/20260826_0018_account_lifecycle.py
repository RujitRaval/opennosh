"""Add self-custodied account recovery tokens.

Revision ID: 20260826_0018
Revises: 20260826_0017
Create Date: 2026-08-26 17:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260826_0018"
down_revision: str | Sequence[str] | None = "20260826_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("recovery_token_hash", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "recovery_token_hash")
