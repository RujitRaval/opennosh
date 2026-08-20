"""Constrain private body metric types, units, and values.

Revision ID: 20260820_0008
Revises: 20260820_0007
Create Date: 2026-08-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260820_0008"
down_revision: str | None = "20260820_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # These literals snapshot the issue #15 API contract. Future enum changes
    # must use a new migration rather than changing historical migrations.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM body_metrics
                WHERE metric_type NOT IN (
                    'body_weight', 'body_fat_percentage', 'height',
                    'waist_circumference', 'hip_circumference',
                    'chest_circumference', 'neck_circumference',
                    'upper_arm_circumference', 'thigh_circumference'
                )
                   OR unit NOT IN ('kg', 'lb', 'percent', 'cm', 'in')
                   OR NOT (
                       (metric_type = 'body_weight' AND unit IN ('kg', 'lb'))
                       OR (metric_type = 'body_fat_percentage' AND unit = 'percent')
                       OR (
                           metric_type IN (
                               'height', 'waist_circumference', 'hip_circumference',
                               'chest_circumference', 'neck_circumference',
                               'upper_arm_circumference', 'thigh_circumference'
                           ) AND unit IN ('cm', 'in')
                       )
                   )
                   OR value <= 0
                   OR value > 1000000
                   OR recorded_at < TIMESTAMPTZ '0001-01-01 00:00:00.000001+00'
                   OR recorded_at > TIMESTAMPTZ '9999-12-31 23:59:59.999998+00'
            ) THEN
                RAISE EXCEPTION 'Cannot migrate invalid legacy body metrics';
            END IF;
        END
        $$
        """
    )
    op.create_check_constraint(
        op.f("ck_body_metrics_metric_type_allowed"),
        "body_metrics",
        "metric_type IN ('body_weight', 'body_fat_percentage', 'height', "
        "'waist_circumference', 'hip_circumference', 'chest_circumference', "
        "'neck_circumference', 'upper_arm_circumference', 'thigh_circumference')",
    )
    op.create_check_constraint(
        op.f("ck_body_metrics_unit_allowed"),
        "body_metrics",
        "unit IN ('kg', 'lb', 'percent', 'cm', 'in')",
    )
    op.create_check_constraint(
        op.f("ck_body_metrics_type_unit_valid"),
        "body_metrics",
        "(metric_type = 'body_weight' AND unit IN ('kg', 'lb')) OR "
        "(metric_type = 'body_fat_percentage' AND unit = 'percent') OR "
        "(metric_type IN ('height', 'waist_circumference', 'hip_circumference', "
        "'chest_circumference', 'neck_circumference', 'upper_arm_circumference', "
        "'thigh_circumference') AND unit IN ('cm', 'in'))",
    )
    op.create_check_constraint(
        op.f("ck_body_metrics_value_bounded"),
        "body_metrics",
        "value > 0 AND value <= 1000000",
    )
    op.create_check_constraint(
        op.f("ck_body_metrics_recorded_at_supported"),
        "body_metrics",
        "recorded_at >= TIMESTAMPTZ '0001-01-01 00:00:00.000001+00' AND "
        "recorded_at <= TIMESTAMPTZ '9999-12-31 23:59:59.999998+00'",
    )


def downgrade() -> None:
    for name in (
        "value_bounded",
        "recorded_at_supported",
        "type_unit_valid",
        "unit_allowed",
        "metric_type_allowed",
    ):
        op.drop_constraint(op.f(f"ck_body_metrics_{name}"), "body_metrics", type_="check")
