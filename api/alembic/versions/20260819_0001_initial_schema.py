"""Create the license-separated, tenant-aware initial schema.

Revision ID: 20260819_0001
Revises:
Create Date: 2026-08-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260819_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _uuid() -> sa.Uuid:
    return sa.Uuid(as_uuid=True)


def _id_column() -> sa.Column:
    return sa.Column(
        "id", _uuid(), primary_key=True, nullable=False, server_default=sa.text("gen_random_uuid()")
    )


def _owner_column() -> sa.Column:
    return sa.Column(
        "user_id",
        _uuid(),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "users",
        _id_column(),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "settings_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    op.create_table(
        "foods_reference",
        _id_column(),
        sa.Column("fdc_id", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("food_category", sa.String(length=255), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="usda"),
        sa.Column("license", sa.String(length=32), nullable=False, server_default="CC0"),
        sa.Column("nutrients_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("portions_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("source = 'usda'", name=op.f("ck_foods_reference_source_usda")),
        sa.CheckConstraint("license = 'CC0'", name=op.f("ck_foods_reference_license_cc0")),
        sa.PrimaryKeyConstraint("id", name="pk_foods_reference"),
        sa.UniqueConstraint("fdc_id", name="uq_foods_reference_fdc_id"),
    )

    op.create_table(
        "foods_community",
        _id_column(),
        sa.Column("pack_id", sa.String(length=160), nullable=False),
        sa.Column("pack_version", sa.String(length=64), nullable=False),
        sa.Column("slug", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("name_local", sa.String(length=255), nullable=True),
        sa.Column("locale", sa.String(length=35), nullable=True),
        sa.Column("category", sa.String(length=160), nullable=False),
        sa.Column("provenance", sa.String(length=64), nullable=False),
        sa.Column("source_uri", sa.String(length=2048), nullable=True),
        sa.Column("source_license", sa.String(length=64), nullable=False),
        sa.Column("source_note", sa.Text(), nullable=True),
        sa.Column("nutrients_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("portions_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("pack_license", sa.String(length=32), nullable=False, server_default="CC0-1.0"),
        sa.Column("contributed_by", sa.String(length=100), nullable=False),
        sa.CheckConstraint(
            "provenance IN ('lab_analysis', 'government_database', 'manufacturer_label', "
            "'published_recipe_calculation', 'own_measurement')",
            name=op.f("ck_foods_community_provenance_allowed"),
        ),
        sa.CheckConstraint(
            "source_license IN ('contributor-original', 'CC0-1.0', 'public-domain')",
            name=op.f("ck_foods_community_source_license_allowed"),
        ),
        sa.CheckConstraint(
            "pack_license = 'CC0-1.0'", name=op.f("ck_foods_community_pack_license_cc0")
        ),
        sa.PrimaryKeyConstraint("id", name="pk_foods_community"),
        sa.UniqueConstraint("slug", name="uq_foods_community_slug"),
    )

    op.create_table(
        "foods_odbl",
        _id_column(),
        sa.Column("barcode", sa.String(length=64), nullable=False),
        sa.Column("product_name", sa.String(length=500), nullable=False),
        sa.Column("brand", sa.String(length=255), nullable=True),
        sa.Column("nutrients_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="openfoodfacts"),
        sa.Column(
            "database_license", sa.String(length=32), nullable=False, server_default="ODbL-1.0"
        ),
        sa.Column(
            "contents_license", sa.String(length=32), nullable=False, server_default="DbCL-1.0"
        ),
        sa.Column("source_url", sa.String(length=2048), nullable=False),
        sa.Column("attribution_text", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "source = 'openfoodfacts'", name=op.f("ck_foods_odbl_source_openfoodfacts")
        ),
        sa.CheckConstraint(
            "database_license = 'ODbL-1.0'",
            name=op.f("ck_foods_odbl_database_license_odbl"),
        ),
        sa.CheckConstraint(
            "contents_license = 'DbCL-1.0'",
            name=op.f("ck_foods_odbl_contents_license_dbcl"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_foods_odbl"),
        sa.UniqueConstraint("barcode", name="uq_foods_odbl_barcode"),
    )

    op.create_table(
        "foods_custom",
        _id_column(),
        _owner_column(),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("nutrients_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("portions_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_foods_custom"),
    )
    op.create_index("ix_foods_custom_user_id", "foods_custom", ["user_id"], unique=False)

    op.create_table(
        "recipes",
        _id_column(),
        _owner_column(),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("yield_grams", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default="false"),
        sa.CheckConstraint("yield_grams > 0", name=op.f("ck_recipes_yield_grams_positive")),
        sa.PrimaryKeyConstraint("id", name="pk_recipes"),
        sa.UniqueConstraint("user_id", "id", name="uq_recipes_user_id_id"),
    )
    op.create_index("ix_recipes_user_id", "recipes", ["user_id"], unique=False)

    op.create_table(
        "exercises",
        _id_column(),
        sa.Column("slug", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "muscle_groups",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "equipment",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=160), nullable=False),
        sa.Column("source_url", sa.String(length=2048), nullable=False),
        sa.Column("derivative_source_url", sa.String(length=2048), nullable=True),
        sa.Column("license_spdx", sa.String(length=64), nullable=False),
        sa.Column("license_url", sa.String(length=2048), nullable=False),
        sa.Column("author", sa.String(length=255), nullable=True),
        sa.Column("author_url", sa.String(length=2048), nullable=True),
        sa.Column("attribution_text", sa.Text(), nullable=False),
        sa.Column(
            "translation_attribution_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_exercises"),
        sa.UniqueConstraint("slug", name="uq_exercises_slug"),
        sa.UniqueConstraint("source", "source_id", name="uq_exercises_source_id"),
    )

    op.create_table(
        "workouts",
        _id_column(),
        _owner_column(),
        sa.Column("performed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_workouts"),
        sa.UniqueConstraint("user_id", "id", name="uq_workouts_user_id_id"),
    )
    op.create_index("ix_workouts_user_id", "workouts", ["user_id"], unique=False)
    op.create_index(
        "ix_workouts_user_id_performed_at",
        "workouts",
        ["user_id", "performed_at"],
        unique=False,
    )

    op.create_table(
        "recipe_ingredients",
        _id_column(),
        sa.Column("user_id", _uuid(), nullable=False),
        sa.Column("recipe_id", _uuid(), nullable=False),
        sa.Column("food_source_table", sa.String(length=32), nullable=False),
        sa.Column("food_source_id", _uuid(), nullable=False),
        sa.Column("grams", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.CheckConstraint(
            "food_source_table IN ('foods_reference', 'foods_community', 'foods_odbl', "
            "'foods_custom')",
            name=op.f("ck_recipe_ingredients_food_source_allowed"),
        ),
        sa.CheckConstraint("grams > 0", name=op.f("ck_recipe_ingredients_grams_positive")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["user_id", "recipe_id"],
            ["recipes.user_id", "recipes.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_recipe_ingredients"),
    )
    op.create_index(
        "ix_recipe_ingredients_user_id", "recipe_ingredients", ["user_id"], unique=False
    )
    op.create_index(
        "ix_recipe_ingredients_recipe_id", "recipe_ingredients", ["recipe_id"], unique=False
    )

    op.create_table(
        "log_entries",
        _id_column(),
        _owner_column(),
        sa.Column("logged_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("meal_slot", sa.String(length=64), nullable=False),
        sa.Column("food_source_table", sa.String(length=32), nullable=False),
        sa.Column("food_source_id", _uuid(), nullable=False),
        sa.Column("grams", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column(
            "computed_nutrients_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.CheckConstraint(
            "food_source_table IN ('foods_reference', 'foods_community', 'foods_odbl', "
            "'foods_custom')",
            name=op.f("ck_log_entries_food_source_allowed"),
        ),
        sa.CheckConstraint("grams > 0", name=op.f("ck_log_entries_grams_positive")),
        sa.PrimaryKeyConstraint("id", name="pk_log_entries"),
    )
    op.create_index("ix_log_entries_user_id", "log_entries", ["user_id"], unique=False)
    op.create_index(
        "ix_log_entries_user_id_logged_at",
        "log_entries",
        ["user_id", "logged_at"],
        unique=False,
    )

    op.create_table(
        "body_metrics",
        _id_column(),
        _owner_column(),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metric_type", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_body_metrics"),
    )
    op.create_index("ix_body_metrics_user_id", "body_metrics", ["user_id"], unique=False)
    op.create_index(
        "ix_body_metrics_user_id_recorded_at",
        "body_metrics",
        ["user_id", "recorded_at"],
        unique=False,
    )

    op.create_table(
        "workout_sets",
        _id_column(),
        sa.Column("user_id", _uuid(), nullable=False),
        sa.Column("workout_id", _uuid(), nullable=False),
        sa.Column(
            "exercise_id",
            _uuid(),
            sa.ForeignKey("exercises.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("set_index", sa.Integer(), nullable=False),
        sa.Column("reps", sa.Integer(), nullable=False),
        sa.Column("load_value", sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column("load_unit", sa.String(length=32), nullable=False),
        sa.CheckConstraint("set_index >= 0", name=op.f("ck_workout_sets_set_index_nonnegative")),
        sa.CheckConstraint("reps > 0", name=op.f("ck_workout_sets_reps_positive")),
        sa.CheckConstraint(
            "load_value IS NULL OR load_value >= 0",
            name=op.f("ck_workout_sets_load_value_nonnegative"),
        ),
        sa.CheckConstraint(
            "load_unit IN ('kg', 'lb', 'bodyweight', 'band', 'machine_units', 'rpe_only')",
            name=op.f("ck_workout_sets_load_unit_allowed"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["user_id", "workout_id"],
            ["workouts.user_id", "workouts.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workout_sets"),
        sa.UniqueConstraint("workout_id", "set_index", name="uq_workout_sets_position"),
    )
    op.create_index("ix_workout_sets_user_id", "workout_sets", ["user_id"], unique=False)
    op.create_index("ix_workout_sets_workout_id", "workout_sets", ["workout_id"], unique=False)
    op.create_index("ix_workout_sets_exercise_id", "workout_sets", ["exercise_id"], unique=False)

    op.create_table(
        "targets",
        _id_column(),
        _owner_column(),
        sa.Column("day_type", sa.String(length=64), nullable=False),
        sa.Column("kcal", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("protein_g", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("carb_g", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("fat_g", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("active_from", sa.Date(), nullable=False),
        sa.CheckConstraint("kcal >= 0", name=op.f("ck_targets_kcal_nonnegative")),
        sa.CheckConstraint("protein_g >= 0", name=op.f("ck_targets_protein_nonnegative")),
        sa.CheckConstraint("carb_g >= 0", name=op.f("ck_targets_carb_nonnegative")),
        sa.CheckConstraint("fat_g >= 0", name=op.f("ck_targets_fat_nonnegative")),
        sa.PrimaryKeyConstraint("id", name="pk_targets"),
        sa.UniqueConstraint("user_id", "day_type", "active_from", name="uq_targets_schedule"),
    )
    op.create_index("ix_targets_user_id", "targets", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_targets_user_id", table_name="targets")
    op.drop_table("targets")
    op.drop_index("ix_workout_sets_exercise_id", table_name="workout_sets")
    op.drop_index("ix_workout_sets_workout_id", table_name="workout_sets")
    op.drop_index("ix_workout_sets_user_id", table_name="workout_sets")
    op.drop_table("workout_sets")
    op.drop_index("ix_body_metrics_user_id_recorded_at", table_name="body_metrics")
    op.drop_index("ix_body_metrics_user_id", table_name="body_metrics")
    op.drop_table("body_metrics")
    op.drop_index("ix_log_entries_user_id_logged_at", table_name="log_entries")
    op.drop_index("ix_log_entries_user_id", table_name="log_entries")
    op.drop_table("log_entries")
    op.drop_index("ix_recipe_ingredients_recipe_id", table_name="recipe_ingredients")
    op.drop_index("ix_recipe_ingredients_user_id", table_name="recipe_ingredients")
    op.drop_table("recipe_ingredients")
    op.drop_index("ix_workouts_user_id_performed_at", table_name="workouts")
    op.drop_index("ix_workouts_user_id", table_name="workouts")
    op.drop_table("workouts")
    op.drop_table("exercises")
    op.drop_index("ix_recipes_user_id", table_name="recipes")
    op.drop_table("recipes")
    op.drop_index("ix_foods_custom_user_id", table_name="foods_custom")
    op.drop_table("foods_custom")
    op.drop_table("foods_odbl")
    op.drop_table("foods_community")
    op.drop_table("foods_reference")
    op.drop_table("users")
