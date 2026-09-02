"""index publication receipt lineage for scoped mission projections

Revision ID: 20260902_0031
Revises: 20260902_0030
Create Date: 2026-09-02 22:30:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260902_0031"
down_revision: str | Sequence[str] | None = "20260902_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_publication_receipts_prior_digest",
        "publication_receipts",
        ["prior_receipt_digest"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_publication_receipts_prior_digest",
        table_name="publication_receipts",
    )
