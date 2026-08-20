from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import Select, func, select, text
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
    QuantityUnit,
    convert_quantity,
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
    start_local = datetime.combine(day, time.min, tzinfo=timezone)
    end_local = datetime.combine(day + timedelta(days=1), time.min, tzinfo=timezone)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


async def _resolve_food(
    database: AsyncSession,
    reference: FoodLogReference,
    current: CurrentSession,
) -> ResolvedFood | None:
    if reference.source is FoodLogSource.USDA:
        food = await database.scalar(
            select(FoodReference).where(FoodReference.fdc_id == reference.source_id)
        )
        if food is None:
            return None
        return ResolvedFood(
            table=FoodSourceTable.REFERENCE,
            internal_id=food.id,
            source_key=food.fdc_id,
            name=food.description,
            nutrients_json=food.nutrients_json,
            portions_json=food.portions_json,
            authoritative=True,
        )
    if reference.source is FoodLogSource.COMMUNITY:
        food = await database.scalar(
            select(FoodCommunity).where(FoodCommunity.slug == reference.source_id)
        )
        if food is None:
            return None
        return ResolvedFood(
            table=FoodSourceTable.COMMUNITY,
            internal_id=food.id,
            source_key=food.slug,
            name=food.name,
            nutrients_json=food.nutrients_json,
            portions_json=food.portions_json,
        )
    if reference.source is FoodLogSource.OPEN_FOOD_FACTS:
        food = await database.scalar(
            select(FoodOdbl).where(FoodOdbl.barcode == reference.source_id)
        )
        if food is None:
            return None
        return ResolvedFood(
            table=FoodSourceTable.ODBL,
            internal_id=food.id,
            source_key=food.barcode,
            name=food.product_name,
            nutrients_json=food.nutrients_json,
            portions_json=[],
        )

    food = await database.scalar(
        select(FoodCustom).where(
            FoodCustom.id == UUID(reference.source_id),
            FoodCustom.user_id == current.user_id,
        )
    )
    if food is None:
        return None
    return ResolvedFood(
        table=FoodSourceTable.CUSTOM,
        internal_id=food.id,
        source_key=str(food.id),
        name=food.name,
        nutrients_json=food.nutrients_json,
        portions_json=food.portions_json,
    )


def _profile(food: ResolvedFood) -> NutrientProfile:
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
    snapshot = NutrientSnapshot.model_validate(entry.computed_nutrients_json)
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
    food = await _resolve_food(database, payload.food, current)
    if food is None:
        return None
    profile = _profile(food)
    portions = _portions(food)
    try:
        snapshot = convert_quantity(
            profile,
            payload.quantity.to_quantity(),
            portions=portions,
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
    entry_count, grams = (
        await database.execute(
            select(func.count(LogEntry.id), func.coalesce(func.sum(LogEntry.grams), 0)).where(
                LogEntry.user_id == current.user_id,
                LogEntry.logged_at >= start,
                LogEntry.logged_at < end,
            )
        )
    ).one()
    nutrient_rows = (
        await database.execute(
            text(
                """
                SELECT nutrient.key AS code,
                       sum((nutrient.value #>> '{}')::numeric) AS amount
                FROM log_entries AS entry
                CROSS JOIN LATERAL jsonb_each(
                    entry.computed_nutrients_json -> 'nutrients'
                ) AS nutrient(key, value)
                WHERE entry.user_id = :user_id
                  AND entry.logged_at >= :start
                  AND entry.logged_at < :end
                GROUP BY nutrient.key
                ORDER BY nutrient.key
                """
            ),
            {"user_id": current.user_id, "start": start, "end": end},
        )
    ).mappings()
    return DailyTotalsResponse(
        day=day,
        timezone=timezone.key,
        entry_count=int(entry_count),
        grams=Decimal(grams),
        nutrients={row["code"]: Decimal(row["amount"]) for row in nutrient_rows},
    )
