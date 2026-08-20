"""enforce the workout and set logging contract

Revision ID: 20260820_0009
Revises: 20260820_0008
Create Date: 2026-08-20 00:09:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260820_0009"
down_revision: str | Sequence[str] | None = "20260820_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM workouts
                WHERE performed_at < TIMESTAMPTZ '0001-01-01 00:00:00.000001+00'
                   OR performed_at > TIMESTAMPTZ '9999-12-31 23:59:59.999998+00'
                   OR (notes IS NOT NULL AND length(notes) > 5000)
            ) OR EXISTS (
                SELECT 1
                FROM workout_sets
                WHERE set_index < 0 OR set_index >= 500
                   OR reps <= 0 OR reps > 100000
                   OR load_value < 0 OR load_value > 1000000
                   OR load_unit NOT IN (
                       'kg', 'lb', 'bodyweight', 'band', 'machine_units', 'rpe_only'
                   )
                   OR NOT (
                       (load_unit IN ('kg', 'lb', 'machine_units')
                        AND load_value IS NOT NULL)
                       OR (load_unit IN ('bodyweight', 'band') AND load_value IS NULL)
                       OR (load_unit = 'rpe_only' AND load_value IS NOT NULL
                           AND load_value BETWEEN 1 AND 10)
                   )
            ) THEN
                RAISE EXCEPTION 'Cannot migrate invalid legacy workouts or sets';
            END IF;
        END
        $$
        """
    )
    op.create_check_constraint(
        op.f("ck_workouts_performed_at_supported"),
        "workouts",
        "performed_at >= TIMESTAMPTZ '0001-01-01 00:00:00.000001+00' AND "
        "performed_at <= TIMESTAMPTZ '9999-12-31 23:59:59.999998+00'",
    )
    op.create_check_constraint(
        op.f("ck_workouts_notes_bounded"),
        "workouts",
        "notes IS NULL OR length(notes) <= 5000",
    )
    op.create_check_constraint(
        op.f("ck_workout_sets_set_index_bounded"),
        "workout_sets",
        "set_index < 500",
    )
    op.create_check_constraint(
        op.f("ck_workout_sets_reps_bounded"),
        "workout_sets",
        "reps <= 100000",
    )
    op.create_check_constraint(
        op.f("ck_workout_sets_load_value_bounded"),
        "workout_sets",
        "load_value IS NULL OR load_value <= 1000000",
    )
    op.create_check_constraint(
        op.f("ck_workout_sets_load_contract_valid"),
        "workout_sets",
        "(load_unit IN ('kg', 'lb', 'machine_units') AND load_value IS NOT NULL) OR "
        "(load_unit IN ('bodyweight', 'band') AND load_value IS NULL) OR "
        "(load_unit = 'rpe_only' AND load_value IS NOT NULL "
        "AND load_value BETWEEN 1 AND 10)",
    )


def downgrade() -> None:
    for table_name, name in (
        ("workout_sets", "load_contract_valid"),
        ("workout_sets", "load_value_bounded"),
        ("workout_sets", "reps_bounded"),
        ("workout_sets", "set_index_bounded"),
        ("workouts", "notes_bounded"),
        ("workouts", "performed_at_supported"),
    ):
        op.drop_constraint(op.f(f"ck_{table_name}_{name}"), table_name, type_="check")
