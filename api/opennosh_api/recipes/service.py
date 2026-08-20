from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.auth.dependencies import CurrentSession
from opennosh_api.auth.tenant import delete_owned_resource
from opennosh_api.logs.schemas import FoodLogReference, FoodLogSource
from opennosh_api.logs.service import (
    canonical_food_source_id,
    profile_for_food,
    resolve_foods,
)
from opennosh_api.models import FoodSourceTable, Recipe, RecipeIngredient
from opennosh_api.nutrition import (
    NutrientProfile,
    NutrientSnapshot,
    Quantity,
    QuantityUnit,
    convert_quantity,
    deterministic_add,
    deterministic_divide,
    deterministic_multiply,
)
from opennosh_api.recipes.schemas import (
    RecipeFoodSource,
    RecipeIngredientFood,
    RecipeIngredientResponse,
    RecipeIngredientWrite,
    RecipeListResponse,
    RecipeResponse,
    RecipeWrite,
)

RECIPE_LIST_LIMIT_DEFAULT = 50
RECIPE_LIST_LIMIT_MAX = 100
RECIPE_LIST_OFFSET_MAX = 10_000

_TABLE_TO_SOURCE = {
    FoodSourceTable.REFERENCE.value: RecipeFoodSource.USDA,
    FoodSourceTable.COMMUNITY.value: RecipeFoodSource.COMMUNITY,
    FoodSourceTable.ODBL.value: RecipeFoodSource.OPEN_FOOD_FACTS,
    FoodSourceTable.CUSTOM.value: RecipeFoodSource.CUSTOM,
}


class RecipeInputError(ValueError):
    """A safe, user-actionable recipe validation error."""


class RecipeSourceNotFound(LookupError):
    """An ingredient source is absent or inaccessible to the current owner."""


@dataclass(frozen=True)
class RecipeComposition:
    total: NutrientSnapshot
    profile: NutrientProfile


def compose_recipe(
    snapshots: Iterable[NutrientSnapshot], *, yield_grams: Decimal
) -> RecipeComposition:
    totals: dict[str, Decimal] = {}
    count = 0
    for snapshot in snapshots:
        count += 1
        for code, amount in snapshot.nutrients.items():
            totals[code] = deterministic_add(totals.get(code, Decimal(0)), amount)
    if count == 0:
        raise RecipeInputError("A recipe must contain at least one ingredient")

    try:
        total = NutrientSnapshot.model_validate(
            {"grams": yield_grams, "nutrients": totals},
            context={"authoritative_source": True},
        )
        profile = NutrientProfile.model_validate(
            {
                "nutrients": {
                    code: deterministic_divide(
                        deterministic_multiply(amount, Decimal(100)), yield_grams
                    )
                    for code, amount in totals.items()
                }
            },
            context={"authoritative_source": True},
        )
    except ValueError as error:
        raise RecipeInputError(f"Recipe yield produces an invalid composition: {error}") from error
    return RecipeComposition(total=total, profile=profile)


def _stored_snapshot(ingredient: RecipeIngredient) -> NutrientSnapshot:
    return NutrientSnapshot.model_validate(
        ingredient.computed_nutrients_json,
        context={"authoritative_source": True},
    )


def composition_from_ingredients(
    ingredients: Sequence[RecipeIngredient], *, yield_grams: Decimal
) -> RecipeComposition:
    return compose_recipe(
        (_stored_snapshot(ingredient) for ingredient in ingredients),
        yield_grams=yield_grams,
    )


def _ingredient_response(ingredient: RecipeIngredient) -> RecipeIngredientResponse:
    try:
        source = _TABLE_TO_SOURCE[ingredient.food_source_table]
    except KeyError as error:  # pragma: no cover - database constraint is authoritative
        raise RuntimeError("Stored recipe ingredient has an unsupported food source") from error
    return RecipeIngredientResponse(
        id=ingredient.id,
        position=ingredient.position,
        food=RecipeIngredientFood(
            source=source,
            source_id=ingredient.food_source_key,
            name=ingredient.food_name,
        ),
        grams=ingredient.grams,
        snapshot=_stored_snapshot(ingredient).rounded_for_api(),
    )


def recipe_response(
    recipe: Recipe, ingredients: Sequence[RecipeIngredient]
) -> RecipeResponse:
    composition = composition_from_ingredients(ingredients, yield_grams=recipe.yield_grams)
    return RecipeResponse(
        id=recipe.id,
        name=recipe.name,
        yield_grams=recipe.yield_grams,
        is_public=False,
        ingredients=[_ingredient_response(ingredient) for ingredient in ingredients],
        total=composition.total.rounded_for_api(),
        nutrients_per_100g=dict(composition.profile.nutrients.items()),
    )


async def _build_ingredients(
    database: AsyncSession,
    *,
    recipe_id: UUID,
    payloads: Sequence[RecipeIngredientWrite],
    current: CurrentSession,
) -> list[RecipeIngredient]:
    references = [
        FoodLogReference(
            source=FoodLogSource(payload.food.source.value),
            source_id=payload.food.source_id,
        )
        for payload in payloads
    ]
    resolved_foods = await resolve_foods(database, references, current)
    ingredients: list[RecipeIngredient] = []
    for position, (payload, reference) in enumerate(zip(payloads, references, strict=True)):
        food = resolved_foods.get(
            (reference.source, canonical_food_source_id(reference))
        )
        if food is None:
            raise RecipeSourceNotFound(f"Ingredient {position + 1} food not found")
        try:
            snapshot = convert_quantity(
                profile_for_food(food),
                Quantity(amount=payload.grams, unit=QuantityUnit.GRAM),
            )
        except ValueError as error:
            raise RecipeInputError(f"Ingredient {position + 1}: {error}") from error
        ingredients.append(
            RecipeIngredient(
                user_id=current.user_id,
                recipe_id=recipe_id,
                position=position,
                food_source_table=food.table.value,
                food_source_id=food.internal_id,
                food_source_key=food.source_key,
                food_name=food.name,
                grams=snapshot.grams,
                computed_nutrients_json=snapshot.model_dump(mode="json"),
            )
        )
    return ingredients


async def create_recipe(
    database: AsyncSession, payload: RecipeWrite, current: CurrentSession
) -> RecipeResponse:
    recipe = Recipe(
        user_id=current.user_id,
        name=payload.name,
        yield_grams=payload.yield_grams,
        is_public=False,
    )
    database.add(recipe)
    await database.flush()
    ingredients = await _build_ingredients(
        database,
        recipe_id=recipe.id,
        payloads=payload.ingredients,
        current=current,
    )
    composition_from_ingredients(ingredients, yield_grams=recipe.yield_grams)
    database.add_all(ingredients)
    await database.commit()
    return recipe_response(recipe, ingredients)


async def list_recipes(
    database: AsyncSession,
    *,
    current: CurrentSession,
    limit: int,
    offset: int,
) -> RecipeListResponse:
    page = (
        select(Recipe.id)
        .where(Recipe.user_id == current.user_id)
        .order_by(Recipe.name, Recipe.id)
        .offset(offset)
        .limit(limit + 1)
        .subquery()
    )
    rows = (
        await database.execute(
            select(Recipe, RecipeIngredient)
            .join(page, Recipe.id == page.c.id)
            .outerjoin(
                RecipeIngredient,
                and_(
                    RecipeIngredient.recipe_id == Recipe.id,
                    RecipeIngredient.user_id == Recipe.user_id,
                ),
            )
            .order_by(
                Recipe.name,
                Recipe.id,
                RecipeIngredient.position,
                RecipeIngredient.id,
            )
        )
    ).all()
    recipes: dict[UUID, Recipe] = {}
    ingredients: dict[UUID, list[RecipeIngredient]] = {}
    for recipe, ingredient in rows:
        recipes.setdefault(recipe.id, recipe)
        ingredients.setdefault(recipe.id, [])
        if ingredient is not None:
            ingredients[recipe.id].append(ingredient)
    page_recipes = list(recipes.values())
    visible = page_recipes[:limit]
    items = [
        recipe_response(recipe, ingredients[recipe.id]) for recipe in visible
    ]
    return RecipeListResponse(
        items=items,
        limit=limit,
        offset=offset,
        has_more=len(page_recipes) > limit,
    )


async def get_recipe(
    database: AsyncSession, recipe_id: UUID, current: CurrentSession
) -> RecipeResponse | None:
    resolved = await resolve_owned_recipe(database, recipe_id, current)
    if resolved is None:
        return None
    recipe, ingredients = resolved
    return recipe_response(recipe, ingredients)


async def update_recipe(
    database: AsyncSession,
    recipe_id: UUID,
    payload: RecipeWrite,
    current: CurrentSession,
) -> RecipeResponse | None:
    recipe = await database.scalar(
        select(Recipe)
        .where(Recipe.id == recipe_id, Recipe.user_id == current.user_id)
        .with_for_update()
    )
    if recipe is None:
        return None
    ingredients = await _build_ingredients(
        database,
        recipe_id=recipe.id,
        payloads=payload.ingredients,
        current=current,
    )
    composition_from_ingredients(ingredients, yield_grams=payload.yield_grams)
    recipe.name = payload.name
    recipe.yield_grams = payload.yield_grams
    recipe.is_public = False
    await database.execute(
        delete(RecipeIngredient).where(
            RecipeIngredient.recipe_id == recipe.id,
            RecipeIngredient.user_id == current.user_id,
        )
    )
    database.add_all(ingredients)
    await database.commit()
    return recipe_response(recipe, ingredients)


async def delete_recipe(
    database: AsyncSession, recipe_id: UUID, current: CurrentSession
) -> bool:
    deleted = await delete_owned_resource(
        database, Recipe, resource_id=recipe_id, current=current
    )
    if deleted:
        await database.commit()
    return deleted


async def resolve_owned_recipe(
    database: AsyncSession, recipe_id: UUID, current: CurrentSession
) -> tuple[Recipe, list[RecipeIngredient]] | None:
    rows = (
        await database.execute(
            select(Recipe, RecipeIngredient)
            .outerjoin(
                RecipeIngredient,
                and_(
                    RecipeIngredient.recipe_id == Recipe.id,
                    RecipeIngredient.user_id == Recipe.user_id,
                ),
            )
            .where(
                Recipe.id == recipe_id,
                Recipe.user_id == current.user_id,
            )
            .order_by(RecipeIngredient.position, RecipeIngredient.id)
        )
    ).all()
    if not rows:
        return None
    recipe = rows[0][0]
    ingredients = [ingredient for _, ingredient in rows if ingredient is not None]
    if not ingredients:
        raise RecipeInputError("Recipe must contain at least one ingredient")
    return recipe, ingredients
