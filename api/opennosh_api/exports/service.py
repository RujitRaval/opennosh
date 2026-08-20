from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any, Literal, cast
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.auth.dependencies import CurrentSession
from opennosh_api.exports.schemas import (
    AccountExport,
    BodyMetricExport,
    CommunityFoodExport,
    CommunityFoodExportEntry,
    CustomFoodExport,
    LogEntryExport,
    PrivateDataExport,
    RecipeExport,
    RecipeIngredientExport,
    TargetExport,
    WorkoutExport,
    WorkoutSetExport,
)
from opennosh_api.exports.streaming import (
    ExportByteLimitError,
    JsonSection,
    spool_json_stream,
    stream_json_sections,
)
from opennosh_api.models import (
    BodyMetric,
    FoodCommunity,
    FoodCustom,
    LogEntry,
    Recipe,
    RecipeIngredient,
    Target,
    User,
    Workout,
    WorkoutSet,
)

EXPORT_ROW_LIMIT = 10_000
EXPORT_MAX_SERIALIZED_BYTES = 64 * 1024 * 1024


class CommunityExportLimitError(RuntimeError):
    """The CC0 community catalogue exceeded the bounded public export size."""


class ExportTimeoutError(RuntimeError):
    """PostgreSQL stopped an export at its configured deadline."""


async def _set_timeout(database: AsyncSession, milliseconds: int) -> None:
    await database.execute(
        text("SELECT set_config('statement_timeout', :timeout, true)"),
        {"timeout": f"{milliseconds}ms"},
    )


async def _begin_export_snapshot(database: AsyncSession) -> None:
    await database.execute(
        text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
    )


def _is_timeout(error: DBAPIError) -> bool:
    return getattr(error.orig, "sqlstate", None) == "57014"


def _community_entry(row: FoodCommunity) -> CommunityFoodExportEntry:
    return CommunityFoodExportEntry(
        pack_id=row.pack_id,
        pack_version=row.pack_version,
        pack_license=cast(Literal["CC0-1.0"], row.pack_license),
        slug=row.slug,
        name=row.name,
        name_local=row.name_local,
        locale=row.locale,
        category=row.category,
        contributed_by=row.contributed_by,
        provenance=row.provenance,
        source_uri=row.source_uri,
        source_license=row.source_license,
        source_note=row.source_note,
        basis=str(row.nutrients_json["basis"]),
        nutrients=dict(row.nutrients_json["nutrients"]),
        portions=row.portions_json,
        density_g_per_ml=row.nutrients_json.get("density_g_per_ml"),
    )


async def prepare_community_export(
    database: AsyncSession, *, statement_timeout_ms: int
) -> AsyncIterator[bytes]:
    await _begin_export_snapshot(database)
    await _set_timeout(database, statement_timeout_ms)

    async def rows() -> AsyncIterator[BaseModel]:
        result = await database.stream_scalars(
            select(FoodCommunity)
            .order_by(FoodCommunity.pack_id, FoodCommunity.slug)
            .execution_options(yield_per=100)
        )
        count = 0
        try:
            async for row in result:
                count += 1
                if count > EXPORT_ROW_LIMIT:
                    raise CommunityExportLimitError
                yield _community_entry(row)
        finally:
            await result.close()

    try:
        body = await spool_json_stream(
            stream_json_sections(
                CommunityFoodExport(entries=[]), [JsonSection("entries", rows)]
            ),
            max_bytes=EXPORT_MAX_SERIALIZED_BYTES,
        )
    except (CommunityExportLimitError, ExportByteLimitError) as error:
        await database.rollback()
        raise CommunityExportLimitError from error
    except DBAPIError as error:
        if not _is_timeout(error):
            await database.rollback()
            raise
        await database.rollback()
        raise ExportTimeoutError from error
    except BaseException:
        await database.rollback()
        raise
    await database.rollback()
    return body


def _private_sections(
    database: AsyncSession, user_id: UUID
) -> list[JsonSection]:
    def section(
        name: str,
        model: type[BaseModel],
        statement: Any,
        values: Callable[[Any], dict[str, Any]],
    ) -> JsonSection:
        async def rows() -> AsyncIterator[BaseModel]:
            result = await database.stream_scalars(
                statement.execution_options(yield_per=100)
            )
            try:
                async for row in result:
                    yield model.model_validate(values(row))
            finally:
                await result.close()

        return JsonSection(name, rows)

    return [
        section(
            "custom_foods",
            CustomFoodExport,
            select(FoodCustom)
            .where(FoodCustom.user_id == user_id)
            .order_by(FoodCustom.created_at, FoodCustom.id),
            lambda item: {
                "id": item.id,
                "created_at": item.created_at,
                "name": item.name,
                "nutrients": item.nutrients_json,
                "portions": item.portions_json,
            },
        ),
        section(
            "recipes",
            RecipeExport,
            select(Recipe).where(Recipe.user_id == user_id).order_by(Recipe.name, Recipe.id),
            lambda item: {
                "id": item.id,
                "name": item.name,
                "yield_grams": item.yield_grams,
                "is_public": item.is_public,
            },
        ),
        section(
            "recipe_ingredients",
            RecipeIngredientExport,
            select(RecipeIngredient)
            .where(RecipeIngredient.user_id == user_id)
            .order_by(
                RecipeIngredient.recipe_id,
                RecipeIngredient.position,
                RecipeIngredient.id,
            ),
            lambda item: {
                "id": item.id,
                "recipe_id": item.recipe_id,
                "position": item.position,
                "food_source_table": item.food_source_table,
                "food_source_id": item.food_source_id,
                "food_source_key": item.food_source_key,
                "food_name": item.food_name,
                "grams": item.grams,
                "computed_nutrients": item.computed_nutrients_json,
            },
        ),
        section(
            "log_entries",
            LogEntryExport,
            select(LogEntry)
            .where(LogEntry.user_id == user_id)
            .order_by(LogEntry.logged_at, LogEntry.id),
            lambda item: {
                "id": item.id,
                "logged_at": item.logged_at,
                "meal_slot": item.meal_slot,
                "food_source_table": item.food_source_table,
                "food_source_id": item.food_source_id,
                "food_source_key": item.food_source_key,
                "food_name": item.food_name,
                "quantity_amount": item.quantity_amount,
                "quantity_unit": item.quantity_unit,
                "portion_name": item.portion_name,
                "grams": item.grams,
                "computed_nutrients": item.computed_nutrients_json,
            },
        ),
        section(
            "targets",
            TargetExport,
            select(Target)
            .where(Target.user_id == user_id)
            .order_by(Target.day_type, Target.active_from, Target.id),
            lambda item: {
                "id": item.id,
                "day_type": item.day_type,
                "kcal": item.kcal,
                "protein_g": item.protein_g,
                "carb_g": item.carb_g,
                "fat_g": item.fat_g,
                "active_from": item.active_from,
                "active_until": item.active_until,
                "below_floor_confirmed": item.below_floor_confirmed,
                "safety_review_required": item.safety_review_required,
                "safety_floor_kcal": item.safety_floor_kcal,
            },
        ),
        section(
            "body_metrics",
            BodyMetricExport,
            select(BodyMetric)
            .where(BodyMetric.user_id == user_id)
            .order_by(BodyMetric.recorded_at, BodyMetric.id),
            lambda item: {
                "id": item.id,
                "recorded_at": item.recorded_at,
                "metric_type": item.metric_type,
                "value": item.value,
                "unit": item.unit,
            },
        ),
        section(
            "workouts",
            WorkoutExport,
            select(Workout)
            .where(Workout.user_id == user_id)
            .order_by(Workout.performed_at, Workout.id),
            lambda item: {
                "id": item.id,
                "performed_at": item.performed_at,
                "notes": item.notes,
            },
        ),
        section(
            "workout_sets",
            WorkoutSetExport,
            select(WorkoutSet)
            .where(WorkoutSet.user_id == user_id)
            .order_by(WorkoutSet.workout_id, WorkoutSet.set_index, WorkoutSet.id),
            lambda item: {
                "id": item.id,
                "workout_id": item.workout_id,
                "exercise_id": item.exercise_id,
                "position": item.set_index,
                "reps": item.reps,
                "load_value": item.load_value,
                "load_unit": item.load_unit,
            },
        ),
    ]


async def prepare_private_export(
    database: AsyncSession,
    *,
    current: CurrentSession,
    statement_timeout_ms: int,
) -> AsyncIterator[bytes]:
    await _begin_export_snapshot(database)
    await _set_timeout(database, statement_timeout_ms)
    try:
        owner = await database.scalar(
            select(User)
            .where(User.id == current.user_id)
            .execution_options(populate_existing=True)
        )
        if owner is None:  # pragma: no cover - the authenticated user cannot disappear
            raise RuntimeError("Authenticated export owner no longer exists")
        envelope = PrivateDataExport(
            account=AccountExport(
                id=owner.id,
                email=owner.email,
                created_at=owner.created_at,
                settings=owner.settings_json,
            ),
            custom_foods=[],
            recipes=[],
            recipe_ingredients=[],
            log_entries=[],
            targets=[],
            body_metrics=[],
            workouts=[],
            workout_sets=[],
        )
        body = await spool_json_stream(
            stream_json_sections(
                envelope, _private_sections(database, current.user_id)
            )
        )
    except DBAPIError as error:
        if not _is_timeout(error):
            await database.rollback()
            raise
        await database.rollback()
        raise ExportTimeoutError from error
    except BaseException:
        await database.rollback()
        raise
    await database.rollback()
    return body
