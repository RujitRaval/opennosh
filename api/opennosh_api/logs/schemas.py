from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from opennosh_api.nutrition import NutrientSnapshotPayload, Quantity, QuantityUnit


class FoodLogSource(StrEnum):
    USDA = "usda"
    COMMUNITY = "community"
    OPEN_FOOD_FACTS = "openfoodfacts"
    CUSTOM = "custom"
    RECIPE = "recipe"


def _validate_meal_slot(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("meal_slot must not be empty")
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise ValueError("meal_slot must not contain control characters")
    return normalized


MealSlot = Annotated[str, Field(max_length=64), AfterValidator(_validate_meal_slot)]


class FoodLogReference(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    source: FoodLogSource
    source_id: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def validate_source_id(self) -> FoodLogReference:
        if "\x00" in self.source_id:
            raise ValueError("source_id must not contain NUL characters")
        if self.source is FoodLogSource.USDA:
            if not self.source_id.isascii() or not self.source_id.isdigit():
                raise ValueError("USDA source_id must contain only digits")
        elif self.source is FoodLogSource.COMMUNITY:
            parts = self.source_id.split("-")
            valid_slug = all(
                part and part.isascii() and part.isalnum() and part == part.lower()
                for part in parts
            )
            if not valid_slug:
                raise ValueError("Community source_id must be a lowercase slug")
        elif self.source is FoodLogSource.OPEN_FOOD_FACTS:
            if not self.source_id.isascii() or not self.source_id.isdigit():
                raise ValueError("Open Food Facts source_id must contain only digits")
        elif self.source in {FoodLogSource.CUSTOM, FoodLogSource.RECIPE}:
            try:
                UUID(self.source_id)
            except ValueError as error:
                label = "Custom" if self.source is FoodLogSource.CUSTOM else "Recipe"
                raise ValueError(f"{label} source_id must be a UUID") from error
        return self


class LogQuantity(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    amount: Decimal
    unit: QuantityUnit
    portion_name: str | None = Field(default=None, min_length=1, max_length=80)

    @model_validator(mode="after")
    def validate_quantity(self) -> LogQuantity:
        Quantity.model_validate(self.model_dump())
        return self

    def to_quantity(self) -> Quantity:
        return Quantity.model_validate(self.model_dump())

    @field_serializer("amount", when_used="json")
    def serialize_amount(self, value: Decimal) -> str:
        return format(value, "f")


class LogEntryCreate(BaseModel):
    model_config = ConfigDict(frozen=True)

    logged_at: datetime
    meal_slot: MealSlot
    food: FoodLogReference
    quantity: LogQuantity

    @field_validator("logged_at")
    @classmethod
    def require_aware_logged_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("logged_at must include a UTC offset")
        try:
            value.astimezone(UTC)
        except OverflowError as error:
            raise ValueError("logged_at is outside the supported UTC range") from error
        return value


class LoggedFood(FoodLogReference):
    name: str


class LogEntryResponse(BaseModel):
    id: UUID
    logged_at: datetime
    meal_slot: str
    food: LoggedFood
    quantity: LogQuantity
    snapshot: NutrientSnapshotPayload


class LogEntryListResponse(BaseModel):
    day: date
    timezone: str
    items: list[LogEntryResponse]
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    has_more: bool


class DailyTotalsResponse(BaseModel):
    day: date
    timezone: str
    entry_count: int = Field(ge=0)
    grams: Decimal = Field(ge=0)
    nutrients: dict[str, Decimal]

    @field_serializer("grams", when_used="json")
    def serialize_grams(self, value: Decimal) -> str:
        return format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")

    @field_serializer("nutrients", when_used="json")
    def serialize_nutrients(self, value: dict[str, Decimal]) -> dict[str, str]:
        return {
            code: format(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")
            for code, amount in value.items()
        }
