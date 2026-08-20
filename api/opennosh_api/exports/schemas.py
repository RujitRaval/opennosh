from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_serializer


class _ExportModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AccountExport(_ExportModel):
    id: UUID
    email: str
    created_at: datetime
    settings: dict[str, Any]


class CustomFoodExport(_ExportModel):
    id: UUID
    created_at: datetime
    name: str
    nutrients: dict[str, Any]
    portions: list[dict[str, Any]]


class RecipeExport(_ExportModel):
    id: UUID
    name: str
    yield_grams: Decimal
    is_public: Literal[False] = False

    @field_serializer("yield_grams", when_used="json")
    def serialize_yield_grams(self, value: Decimal) -> str:
        return format(value, "f")


class RecipeIngredientExport(_ExportModel):
    id: UUID
    recipe_id: UUID
    position: int
    food_source_table: str
    food_source_id: UUID
    food_source_key: str
    food_name: str
    grams: Decimal
    computed_nutrients: dict[str, Any]

    @field_serializer("grams", when_used="json")
    def serialize_grams(self, value: Decimal) -> str:
        return format(value, "f")


class LogEntryExport(_ExportModel):
    id: UUID
    logged_at: datetime
    meal_slot: str
    food_source_table: str
    food_source_id: UUID
    food_source_key: str
    food_name: str
    quantity_amount: Decimal
    quantity_unit: str
    portion_name: str | None
    grams: Decimal
    computed_nutrients: dict[str, Any]

    @field_serializer("quantity_amount", "grams", when_used="json")
    def serialize_decimal(self, value: Decimal) -> str:
        return format(value, "f")


class TargetExport(_ExportModel):
    id: UUID
    day_type: str
    kcal: Decimal
    protein_g: Decimal
    carb_g: Decimal
    fat_g: Decimal
    active_from: date
    active_until: date | None
    below_floor_confirmed: bool
    safety_review_required: bool
    safety_floor_kcal: Decimal

    @field_serializer(
        "kcal",
        "protein_g",
        "carb_g",
        "fat_g",
        "safety_floor_kcal",
        when_used="json",
    )
    def serialize_decimal(self, value: Decimal) -> str:
        return format(value, "f")


class BodyMetricExport(_ExportModel):
    id: UUID
    recorded_at: datetime
    metric_type: str
    value: Decimal
    unit: str

    @field_serializer("value", when_used="json")
    def serialize_value(self, value: Decimal) -> str:
        return format(value, "f")


class WorkoutExport(_ExportModel):
    id: UUID
    performed_at: datetime
    notes: str | None


class WorkoutSetExport(_ExportModel):
    id: UUID
    workout_id: UUID
    exercise_id: UUID
    position: int
    reps: int
    load_value: Decimal | None
    load_unit: str

    @field_serializer("load_value", when_used="json")
    def serialize_load(self, value: Decimal | None) -> str | None:
        return None if value is None else format(value, "f")


class PrivateDataExport(_ExportModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    dataset: Literal["opennosh-private-user-data"] = "opennosh-private-user-data"
    access: Literal["private"] = "private"
    notice: str = (
        "Private account data for the authenticated owner. This export is not a public "
        "dataset and contains no authentication secrets."
    )
    account: AccountExport
    custom_foods: list[CustomFoodExport]
    recipes: list[RecipeExport]
    recipe_ingredients: list[RecipeIngredientExport]
    log_entries: list[LogEntryExport]
    targets: list[TargetExport]
    body_metrics: list[BodyMetricExport]
    workouts: list[WorkoutExport]
    workout_sets: list[WorkoutSetExport]


class CommunityFoodExportEntry(_ExportModel):
    source: Literal["community"] = "community"
    pack_id: str
    pack_version: str
    pack_license: Literal["CC0-1.0"] = "CC0-1.0"
    slug: str
    name: str
    name_local: str | None
    locale: str | None
    category: str
    contributed_by: str
    provenance: str
    source_uri: str | None
    source_license: str
    source_note: str | None
    basis: str
    nutrients: dict[str, Any]
    portions: list[dict[str, Any]]
    density_g_per_ml: Any | None = None


class CommunityFoodExport(_ExportModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    dataset: Literal["opennosh-community-foods"] = "opennosh-community-foods"
    license: Literal["CC0-1.0"] = "CC0-1.0"
    license_url: str = "https://creativecommons.org/publicdomain/zero/1.0/"
    notice: str = (
        "The community-food pack is dedicated to the public domain under CC0. "
        "Contributor credit remains visible as a community norm."
    )
    entries: list[CommunityFoodExportEntry]
