"""Add bounded, non-overlapping nutrition target schedules.

Revision ID: 20260820_0007
Revises: 20260820_0006
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0007"
down_revision: str | None = "20260820_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # These literals snapshot the issue #14 schema policy. Future policy changes
    # must use a new migration; runtime constants must not rewrite old migrations.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.add_column("targets", sa.Column("active_until", sa.Date(), nullable=True))
    op.add_column(
        "targets",
        sa.Column(
            "below_floor_confirmed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "targets",
        sa.Column(
            "safety_review_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "targets",
        sa.Column(
            "safety_floor_kcal",
            sa.Numeric(10, 2),
            nullable=False,
            server_default=sa.text("1200"),
        ),
    )
    op.execute(
        """
        WITH ranges AS (
            SELECT id,
                   lead(active_from) OVER (
                       PARTITION BY user_id, day_type ORDER BY active_from, id
                   ) - 1 AS active_until
            FROM targets
        )
        UPDATE targets AS target
        SET active_until = ranges.active_until
        FROM ranges
        WHERE target.id = ranges.id
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM targets
                WHERE day_type NOT IN ('training', 'rest')
                   OR kcal > 20000
                   OR protein_g > 2000
                   OR carb_g > 2000
                   OR fat_g > 2000
            ) THEN
                RAISE EXCEPTION 'Cannot migrate invalid legacy nutrition targets';
            END IF;
            IF EXISTS (
                SELECT 1 FROM targets GROUP BY user_id HAVING count(*) > 1000
            ) THEN
                RAISE EXCEPTION 'Cannot migrate target schedules with over 1000 items';
            END IF;
        END
        $$
        """
    )
    op.execute(
        """
        UPDATE targets
        SET safety_review_required = true
        """
    )
    for name in (
        "kcal_nonnegative",
        "protein_nonnegative",
        "carb_nonnegative",
        "fat_nonnegative",
    ):
        op.drop_constraint(op.f(f"ck_targets_{name}"), "targets", type_="check")
    op.create_check_constraint(
        op.f("ck_targets_day_type_allowed"),
        "targets",
        "day_type IN ('training', 'rest')",
    )
    op.create_check_constraint(
        op.f("ck_targets_kcal_bounded"), "targets", "kcal >= 0 AND kcal <= 20000"
    )
    op.create_check_constraint(
        op.f("ck_targets_protein_bounded"),
        "targets",
        "protein_g >= 0 AND protein_g <= 2000",
    )
    op.create_check_constraint(
        op.f("ck_targets_carb_bounded"),
        "targets",
        "carb_g >= 0 AND carb_g <= 2000",
    )
    op.create_check_constraint(
        op.f("ck_targets_fat_bounded"),
        "targets",
        "fat_g >= 0 AND fat_g <= 2000",
    )
    op.create_check_constraint(
        op.f("ck_targets_safety_floor_positive"),
        "targets",
        "safety_floor_kcal > 0",
    )
    op.create_check_constraint(
        op.f("ck_targets_safety_state_valid"),
        "targets",
        "(safety_review_required AND NOT below_floor_confirmed) OR "
        "(NOT safety_review_required AND "
        "((kcal >= safety_floor_kcal AND NOT below_floor_confirmed) OR "
        "(kcal < safety_floor_kcal AND below_floor_confirmed)))",
    )
    op.create_check_constraint(
        op.f("ck_targets_active_range_ordered"),
        "targets",
        "active_until IS NULL OR active_until >= active_from",
    )
    op.execute(
        """
        ALTER TABLE targets
        ADD CONSTRAINT excl_targets_active_range
        EXCLUDE USING gist (
            user_id WITH =,
            day_type WITH =,
            daterange(active_from, active_until, '[]') WITH &&
        )
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE targets DROP CONSTRAINT excl_targets_active_range")
    for name in (
        "active_range_ordered",
        "safety_state_valid",
        "safety_floor_positive",
        "fat_bounded",
        "carb_bounded",
        "protein_bounded",
        "kcal_bounded",
        "day_type_allowed",
    ):
        op.drop_constraint(op.f(f"ck_targets_{name}"), "targets", type_="check")
    op.create_check_constraint(
        op.f("ck_targets_kcal_nonnegative"), "targets", "kcal >= 0"
    )
    op.create_check_constraint(
        op.f("ck_targets_protein_nonnegative"), "targets", "protein_g >= 0"
    )
    op.create_check_constraint(
        op.f("ck_targets_carb_nonnegative"), "targets", "carb_g >= 0"
    )
    op.create_check_constraint(
        op.f("ck_targets_fat_nonnegative"), "targets", "fat_g >= 0"
    )
    op.drop_column("targets", "safety_floor_kcal")
    op.drop_column("targets", "safety_review_required")
    op.drop_column("targets", "below_floor_confirmed")
    op.drop_column("targets", "active_until")
