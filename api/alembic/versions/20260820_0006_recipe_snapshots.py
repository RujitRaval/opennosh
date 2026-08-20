"""Preserve private recipe ingredient snapshots and allow recipe log sources.

Revision ID: 20260820_0006
Revises: 20260820_0005
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260820_0006"
down_revision: str | None = "20260820_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _backfill_ingredient_snapshots(
    *, table: str, source_key: str, source_name: str, owner_predicate: str = ""
) -> None:
    op.execute(
        f"""
        UPDATE recipe_ingredients AS ingredient
        SET food_source_key = {source_key},
            food_name = {source_name},
            computed_nutrients_json = (
                SELECT jsonb_build_object(
                    'basis', 'computed',
                    'grams', ingredient.grams::text,
                    'nutrients', jsonb_object_agg(
                        nutrient.key,
                        to_jsonb(
                            ((nutrient.value #>> '{{}}')::numeric
                             * ingredient.grams / 100)::text
                        )
                    )
                )
                FROM jsonb_each(food.nutrients_json -> 'nutrients') AS nutrient
            )
        FROM {table} AS food
        WHERE ingredient.food_source_table = '{table}'
          AND ingredient.food_source_id = food.id
          AND jsonb_typeof(food.nutrients_json -> 'nutrients') = 'object'
          {owner_predicate}
        """
    )


def upgrade() -> None:
    op.alter_column(
        "recipes",
        "yield_grams",
        existing_type=sa.Numeric(12, 3),
        type_=sa.Numeric(),
        existing_nullable=False,
    )
    op.add_column("recipe_ingredients", sa.Column("position", sa.Integer(), nullable=True))
    op.add_column(
        "recipe_ingredients", sa.Column("food_source_key", sa.String(160), nullable=True)
    )
    op.add_column(
        "recipe_ingredients", sa.Column("food_name", sa.String(500), nullable=True)
    )
    op.add_column(
        "recipe_ingredients",
        sa.Column(
            "computed_nutrients_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.execute(
        """
        WITH positions AS (
            SELECT id,
                   row_number() OVER (PARTITION BY recipe_id ORDER BY id) - 1 AS position
            FROM recipe_ingredients
        )
        UPDATE recipe_ingredients AS ingredient
        SET position = positions.position
        FROM positions
        WHERE ingredient.id = positions.id
        """
    )

    _backfill_ingredient_snapshots(
        table="foods_reference",
        source_key="food.fdc_id",
        source_name="food.description",
    )
    _backfill_ingredient_snapshots(
        table="foods_community",
        source_key="food.slug",
        source_name="food.name",
    )
    _backfill_ingredient_snapshots(
        table="foods_odbl",
        source_key="food.barcode",
        source_name="food.product_name",
    )
    _backfill_ingredient_snapshots(
        table="foods_custom",
        source_key="food.id::text",
        source_name="food.name",
        owner_predicate="AND ingredient.user_id = food.user_id",
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM recipes AS recipe
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM recipe_ingredients AS ingredient
                    WHERE ingredient.recipe_id = recipe.id
                      AND ingredient.user_id = recipe.user_id
                )
            ) THEN
                RAISE EXCEPTION
                    'Cannot migrate recipes without ingredients';
            END IF;
        END
        $$
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM recipe_ingredients
                WHERE food_source_key IS NULL
                   OR food_name IS NULL
                   OR computed_nutrients_json IS NULL
                   OR jsonb_typeof(computed_nutrients_json -> 'nutrients')
                      IS DISTINCT FROM 'object'
                   OR NOT coalesce(
                       (computed_nutrients_json -> 'nutrients')
                       ?& ARRAY['energy_kcal', 'protein_g', 'fat_g', 'carbohydrate_g'],
                       false
                   )
            ) THEN
                RAISE EXCEPTION
                    'Cannot migrate recipe ingredients with missing, cross-tenant, '
                    'or invalid source foods';
            END IF;
        END
        $$
        """
    )
    op.alter_column("recipe_ingredients", "food_source_key", nullable=False)
    op.alter_column("recipe_ingredients", "food_name", nullable=False)
    op.alter_column("recipe_ingredients", "computed_nutrients_json", nullable=False)
    op.alter_column("recipe_ingredients", "position", nullable=False)
    op.alter_column(
        "recipe_ingredients",
        "grams",
        existing_type=sa.Numeric(12, 3),
        type_=sa.Numeric(),
        existing_nullable=False,
    )
    op.create_check_constraint(
        op.f("ck_recipe_ingredients_position_nonnegative"),
        "recipe_ingredients",
        "position >= 0",
    )
    op.create_unique_constraint(
        "uq_recipe_ingredients_position",
        "recipe_ingredients",
        ["recipe_id", "position"],
    )

    op.drop_constraint(
        op.f("ck_log_entries_food_source_allowed"), "log_entries", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_log_entries_food_source_allowed"),
        "log_entries",
        "food_source_table IN ('foods_reference', 'foods_community', 'foods_odbl', "
        "'foods_custom', 'recipes')",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_log_entries_food_source_allowed"), "log_entries", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_log_entries_food_source_allowed"),
        "log_entries",
        "food_source_table IN ('foods_reference', 'foods_community', 'foods_odbl', "
        "'foods_custom')",
    )
    op.drop_constraint(
        "uq_recipe_ingredients_position", "recipe_ingredients", type_="unique"
    )
    op.drop_constraint(
        op.f("ck_recipe_ingredients_position_nonnegative"),
        "recipe_ingredients",
        type_="check",
    )
    # Retain both unbounded NUMERIC types so exact v0006 quantities and yields
    # are not silently rounded during rollback.
    op.drop_column("recipe_ingredients", "computed_nutrients_json")
    op.drop_column("recipe_ingredients", "food_name")
    op.drop_column("recipe_ingredients", "food_source_key")
    op.drop_column("recipe_ingredients", "position")
