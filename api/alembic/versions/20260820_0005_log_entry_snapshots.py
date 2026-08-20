"""Preserve food identity and original quantity on log entries.

Revision ID: 20260820_0005
Revises: 20260820_0004
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0005"
down_revision: str | None = "20260820_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("log_entries", sa.Column("food_source_key", sa.String(160), nullable=True))
    op.add_column("log_entries", sa.Column("food_name", sa.String(500), nullable=True))
    op.add_column("log_entries", sa.Column("quantity_amount", sa.Numeric(), nullable=True))
    op.add_column("log_entries", sa.Column("quantity_unit", sa.String(16), nullable=True))
    op.add_column("log_entries", sa.Column("portion_name", sa.String(80), nullable=True))
    op.execute(
        """
        UPDATE log_entries
        SET food_source_key = food_source_id::text,
            food_name = food_source_table || ':' || food_source_id::text,
            quantity_amount = grams,
            quantity_unit = 'g'
        """
    )
    op.alter_column("log_entries", "food_source_key", nullable=False)
    op.alter_column("log_entries", "food_name", nullable=False)
    op.alter_column("log_entries", "quantity_amount", nullable=False)
    op.alter_column("log_entries", "quantity_unit", nullable=False)
    op.alter_column(
        "log_entries",
        "grams",
        existing_type=sa.Numeric(12, 3),
        type_=sa.Numeric(),
        existing_nullable=False,
    )
    op.create_check_constraint(
        op.f("ck_log_entries_quantity_amount_positive"),
        "log_entries",
        "quantity_amount > 0",
    )
    op.create_check_constraint(
        op.f("ck_log_entries_quantity_unit_allowed"),
        "log_entries",
        "quantity_unit IN ('g', 'ml', 'portion')",
    )
    op.create_check_constraint(
        op.f("ck_log_entries_portion_name_matches_unit"),
        "log_entries",
        "(quantity_unit = 'portion' AND portion_name IS NOT NULL) OR "
        "(quantity_unit <> 'portion' AND portion_name IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_log_entries_portion_name_matches_unit"),
        "log_entries",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_log_entries_quantity_unit_allowed"),
        "log_entries",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_log_entries_quantity_amount_positive"),
        "log_entries",
        type_="check",
    )
    op.alter_column(
        "log_entries",
        "grams",
        existing_type=sa.Numeric(),
        type_=sa.Numeric(12, 3),
        existing_nullable=False,
    )
    op.drop_column("log_entries", "portion_name")
    op.drop_column("log_entries", "quantity_unit")
    op.drop_column("log_entries", "quantity_amount")
    op.drop_column("log_entries", "food_name")
    op.drop_column("log_entries", "food_source_key")
