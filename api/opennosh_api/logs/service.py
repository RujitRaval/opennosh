from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import Select, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.auth.dependencies import CurrentSession
from opennosh_api.auth.tenant import delete_owned_resource, get_owned_resource
from opennosh_api.logs.schemas import (
    DailyTotalsResponse,
    FoodLogReference,
    FoodLogSource,
    LogEntryCreate,
    LogEntryListResponse,
    LogEntryResponse,
    LoggedFood,
    LogQuantity,
)
from opennosh_api.models import (
    FoodCommunity,
    FoodCustom,
    FoodOdbl,
    FoodReference,
    FoodSourceTable,
    LogEntry,
)
from opennosh_api.nutrition import (
    HouseholdPortion,
    NutrientProfile,
    NutrientSnapshot,
    Quantity,
    QuantityUnit,
    convert_quantity,
    deterministic_multiply,
)

LOG_LIST_LIMIT_DEFAULT = 100
LOG_LIST_LIMIT_MAX = 100
LOG_LIST_OFFSET_MAX = 10_000
TIMEZONE_MAX_LENGTH = 64

_SOURCE_TO_TABLE = {
    FoodLogSource.USDA: FoodSourceTable.REFERENCE,
    FoodLogSource.COMMUNITY: FoodSourceTable.COMMUNITY,
    FoodLogSource.OPEN_FOOD_FACTS: FoodSourceTable.ODBL,
    FoodLogSource.CUSTOM: FoodSourceTable.CUSTOM,
    FoodLogSource.RECIPE: FoodSourceTable.RECIPE,
}
_TABLE_TO_SOURCE = {table.value: source for source, table in _SOURCE_TO_TABLE.items()}


class FoodLogInputError(ValueError):
    """A safe, user-actionable food-log validation error."""


@dataclass(frozen=True)
class ResolvedFood:
    table: FoodSourceTable
    internal_id: UUID
    source_key: str
    name: str
    nutrients_json: dict[str, Any]
    portions_json: list[dict[str, Any]]
    authoritative: bool = False
    recipe_yield_grams: Decimal | None = None


def resolve_timezone(requested: str | None, settings_json: dict[str, Any]) -> ZoneInfo:
    configured = settings_json.get("timezone")
    if requested is not None:
        name = requested
    elif isinstance(configured, str):
        name = configured
    else:
        name = "UTC"
    if not 1 <= len(name) <= TIMEZONE_MAX_LENGTH or "\x00" in name:
        raise FoodLogInputError("timezone must be a valid IANA timezone name")
    parts = name.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise FoodLogInputError("timezone must be a valid IANA timezone name")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise FoodLogInputError("timezone must be a valid IANA timezone name") from error


def utc_day_bounds(day: date, timezone: ZoneInfo) -> tuple[datetime, datetime]:
    try:
        start_local = datetime.combine(day, time.min, tzinfo=timezone)
        end_local = datetime.combine(day + timedelta(days=1), time.min, tzinfo=timezone)
        return start_local.astimezone(UTC), end_local.astimezone(UTC)
    except OverflowError as error:
        raise FoodLogInputError("day is outside the supported timezone range") from error


async def resolve_foods(
    database: AsyncSession,
    references: Sequence[FoodLogReference],
    current: CurrentSession,
) -> dict[tuple[FoodLogSource, str], ResolvedFood]:
    """Resolve regular food references in at most one query per license store."""
    if any(reference.source is FoodLogSource.RECIPE for reference in references):
        raise ValueError("Batch food resolution does not accept recipe references")
    source_ids = {
        source: {
            canonical_food_source_id(reference)
            for reference in references
            if reference.source is source
        }
        for source in (
            FoodLogSource.USDA,
            FoodLogSource.COMMUNITY,
            FoodLogSource.OPEN_FOOD_FACTS,
            FoodLogSource.CUSTOM,
        )
    }
    resolved: dict[tuple[FoodLogSource, str], ResolvedFood] = {}
    if source_ids[FoodLogSource.USDA]:
        reference_foods = (
            await database.scalars(
                select(FoodReference).where(
                    FoodReference.fdc_id.in_(source_ids[FoodLogSource.USDA])
                )
            )
        ).all()
        for reference_food in reference_foods:
            resolved[(FoodLogSource.USDA, reference_food.fdc_id)] = ResolvedFood(
                table=FoodSourceTable.REFERENCE,
                internal_id=reference_food.id,
                source_key=reference_food.fdc_id,
                name=reference_food.description,
                nutrients_json=reference_food.nutrients_json,
                portions_json=reference_food.portions_json,
                authoritative=True,
            )
    if source_ids[FoodLogSource.COMMUNITY]:
        community_foods = (
            await database.scalars(
                select(FoodCommunity).where(
                    FoodCommunity.slug.in_(source_ids[FoodLogSource.COMMUNITY])
                )
            )
        ).all()
        for community_food in community_foods:
            resolved[(FoodLogSource.COMMUNITY, community_food.slug)] = ResolvedFood(
                table=FoodSourceTable.COMMUNITY,
                internal_id=community_food.id,
                source_key=community_food.slug,
                name=community_food.name,
                nutrients_json=community_food.nutrients_json,
                portions_json=community_food.portions_json,
            )
    if source_ids[FoodLogSource.OPEN_FOOD_FACTS]:
        odbl_foods = (
            await database.scalars(
                select(FoodOdbl).where(
                    FoodOdbl.barcode.in_(
                        source_ids[FoodLogSource.OPEN_FOOD_FACTS]
                    )
                )
            )
        ).all()
        for odbl_food in odbl_foods:
            resolved[(FoodLogSource.OPEN_FOOD_FACTS, odbl_food.barcode)] = ResolvedFood(
                table=FoodSourceTable.ODBL,
                internal_id=odbl_food.id,
                source_key=odbl_food.barcode,
                name=odbl_food.product_name,
                nutrients_json=odbl_food.nutrients_json,
                portions_json=[],
            )
    if source_ids[FoodLogSource.CUSTOM]:
        custom_ids = [UUID(source_id) for source_id in source_ids[FoodLogSource.CUSTOM]]
        custom_foods = (
            await database.scalars(
                select(FoodCustom).where(
                    FoodCustom.id.in_(custom_ids),
                    FoodCustom.user_id == current.user_id,
                )
            )
        ).all()
        for custom_food in custom_foods:
            source_id = str(custom_food.id)
            resolved[(FoodLogSource.CUSTOM, source_id)] = ResolvedFood(
                table=FoodSourceTable.CUSTOM,
                internal_id=custom_food.id,
                source_key=source_id,
                name=custom_food.name,
                nutrients_json=custom_food.nutrients_json,
                portions_json=custom_food.portions_json,
            )
    return resolved


def canonical_food_source_id(reference: FoodLogReference) -> str:
    """Return the stable lookup key for a validated food reference."""
    if reference.source in {FoodLogSource.CUSTOM, FoodLogSource.RECIPE}:
        return str(UUID(reference.source_id))
    return reference.source_id


async def resolve_food(
    database: AsyncSession,
    reference: FoodLogReference,
    current: CurrentSession,
) -> ResolvedFood | None:
    if reference.source is not FoodLogSource.RECIPE:
        return (await resolve_foods(database, (reference,), current)).get(
            (reference.source, canonical_food_source_id(reference))
        )

    from opennosh_api.recipes.service import (
        RecipeInputError,
        composition_from_ingredients,
        resolve_owned_recipe,
    )

    try:
        owned_recipe = await resolve_owned_recipe(
            database, UUID(reference.source_id), current
        )
        if owned_recipe is None:
            return None
        recipe, ingredients = owned_recipe
        composition = composition_from_ingredients(
            ingredients, yield_grams=recipe.yield_grams
        )
    except RecipeInputError as error:
        raise FoodLogInputError(str(error)) from error
    return ResolvedFood(
        table=FoodSourceTable.RECIPE,
        internal_id=recipe.id,
        source_key=str(recipe.id),
        name=recipe.name,
        nutrients_json=composition.profile.model_dump(mode="json"),
        portions_json=[],
        authoritative=True,
        recipe_yield_grams=recipe.yield_grams,
    )


def profile_for_food(food: ResolvedFood) -> NutrientProfile:
    if food.authoritative:
        return NutrientProfile.model_validate(
            food.nutrients_json,
            context={"authoritative_source": True},
        )
    return NutrientProfile.model_validate(food.nutrients_json)


def _portions(food: ResolvedFood) -> list[HouseholdPortion]:
    return [HouseholdPortion.model_validate(portion) for portion in food.portions_json]


def _source_for_entry(entry: LogEntry) -> FoodLogSource:
    try:
        return _TABLE_TO_SOURCE[entry.food_source_table]
    except KeyError as error:  # pragma: no cover - database constraint is authoritative
        raise RuntimeError("Stored log entry has an unsupported food source") from error


def _response(entry: LogEntry) -> LogEntryResponse:
    # Stored snapshots were computed from a profile validated at write time. USDA
    # snapshots may legitimately retain food-specific energy-factor differences.
    snapshot = NutrientSnapshot.model_validate(
        entry.computed_nutrients_json,
        context={"authoritative_source": True},
    )
    return LogEntryResponse(
        id=entry.id,
        logged_at=entry.logged_at,
        meal_slot=entry.meal_slot,
        food=LoggedFood(
            source=_source_for_entry(entry),
            source_id=entry.food_source_key,
            name=entry.food_name,
        ),
        quantity=LogQuantity(
            amount=entry.quantity_amount,
            unit=QuantityUnit(entry.quantity_unit),
            portion_name=entry.portion_name,
        ),
        snapshot=snapshot.rounded_for_api(),
    )


async def create_log_entry(
    database: AsyncSession,
    payload: LogEntryCreate,
    current: CurrentSession,
) -> LogEntryResponse | None:
    food = await resolve_food(database, payload.food, current)
    if food is None:
        return None
    try:
        profile = profile_for_food(food)
        if (
            food.recipe_yield_grams is not None
            and payload.quantity.unit is QuantityUnit.NAMED_PORTION
        ):
            if payload.quantity.portion_name is None or (
                payload.quantity.portion_name.casefold() != "whole recipe"
            ):
                raise ValueError(
                    f"Unknown household portion: {payload.quantity.portion_name}"
                )
            grams = deterministic_multiply(
                payload.quantity.amount, food.recipe_yield_grams
            )
            snapshot = convert_quantity(
                profile,
                Quantity(amount=grams, unit=QuantityUnit.GRAM),
            )
        else:
            snapshot = convert_quantity(
                profile,
                payload.quantity.to_quantity(),
                portions=_portions(food),
            )
    except ValueError as error:
        raise FoodLogInputError(str(error)) from error

    entry = LogEntry(
        user_id=current.user_id,
        logged_at=payload.logged_at,
        meal_slot=payload.meal_slot,
        food_source_table=food.table.value,
        food_source_id=food.internal_id,
        food_source_key=food.source_key,
        food_name=food.name,
        quantity_amount=payload.quantity.amount,
        quantity_unit=payload.quantity.unit.value,
        portion_name=payload.quantity.portion_name,
        grams=snapshot.grams,
        computed_nutrients_json=snapshot.model_dump(mode="json"),
    )
    database.add(entry)
    await database.commit()
    return _response(entry)


def _owned_day_statement(
    current: CurrentSession, start: datetime, end: datetime
) -> Select[tuple[LogEntry]]:
    return select(LogEntry).where(
        LogEntry.user_id == current.user_id,
        LogEntry.logged_at >= start,
        LogEntry.logged_at < end,
    )


async def list_log_entries(
    database: AsyncSession,
    *,
    day: date,
    timezone: ZoneInfo,
    current: CurrentSession,
    limit: int,
    offset: int,
) -> LogEntryListResponse:
    start, end = utc_day_bounds(day, timezone)
    rows = list(
        (
            await database.scalars(
                _owned_day_statement(current, start, end)
                .order_by(LogEntry.logged_at, LogEntry.id)
                .offset(offset)
                .limit(limit + 1)
            )
        ).all()
    )
    return LogEntryListResponse(
        day=day,
        timezone=timezone.key,
        items=[_response(entry) for entry in rows[:limit]],
        limit=limit,
        offset=offset,
        has_more=len(rows) > limit,
    )


async def get_log_entry(
    database: AsyncSession, entry_id: UUID, current: CurrentSession
) -> LogEntryResponse | None:
    entry = await get_owned_resource(database, LogEntry, resource_id=entry_id, current=current)
    return _response(entry) if entry is not None else None


async def delete_log_entry(database: AsyncSession, entry_id: UUID, current: CurrentSession) -> bool:
    deleted = await delete_owned_resource(database, LogEntry, resource_id=entry_id, current=current)
    if deleted:
        await database.commit()
    return deleted


async def daily_totals(
    database: AsyncSession,
    *,
    day: date,
    timezone: ZoneInfo,
    current: CurrentSession,
) -> DailyTotalsResponse:
    start, end = utc_day_bounds(day, timezone)
    totals = (
        (
            await database.execute(
                text(
                    """
                WITH scoped_entries AS MATERIALIZED (
                    SELECT grams, computed_nutrients_json
                    FROM log_entries
                    WHERE user_id = :user_id
                      AND logged_at >= :start
                      AND logged_at < :end
                ),
                summary AS (
                    SELECT count(*) AS entry_count,
                           coalesce(sum(grams), 0) AS grams
                    FROM scoped_entries
                ),
                nutrient_totals AS (
                    SELECT nutrient.key AS code,
                           sum((nutrient.value #>> '{}')::numeric) AS amount
                    FROM scoped_entries AS entry
                    CROSS JOIN LATERAL jsonb_each(
                        entry.computed_nutrients_json -> 'nutrients'
                    ) AS nutrient(key, value)
                    GROUP BY nutrient.key
                )
                SELECT summary.entry_count,
                       summary.grams,
                       coalesce(
                           (
                               SELECT jsonb_object_agg(
                                   code, amount::text ORDER BY code
                               )
                               FROM nutrient_totals
                           ),
                           '{}'::jsonb
                       ) AS nutrients
                FROM summary
                """
                ),
                {"user_id": current.user_id, "start": start, "end": end},
            )
        )
        .mappings()
        .one()
    )
    return DailyTotalsResponse(
        day=day,
        timezone=timezone.key,
        entry_count=int(totals["entry_count"]),
        grams=Decimal(totals["grams"]),
        nutrients={code: Decimal(amount) for code, amount in totals["nutrients"].items()},
    )
