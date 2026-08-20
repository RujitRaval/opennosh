from __future__ import annotations

from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Annotated, Literal
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


class RecipeFoodSource(StrEnum):
    USDA = "usda"
    COMMUNITY = "community"
    OPEN_FOOD_FACTS = "openfoodfacts"
    CUSTOM = "custom"


def _clean_name(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("name must not be empty")
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise ValueError("name must not contain control characters")
    return normalized


RecipeName = Annotated[str, Field(max_length=255), AfterValidator(_clean_name)]


class RecipeFoodReference(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True, extra="forbid")

    source: RecipeFoodSource
    source_id: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def validate_source_id(self) -> RecipeFoodReference:
        if "\x00" in self.source_id:
            raise ValueError("source_id must not contain NUL characters")
        if self.source is RecipeFoodSource.USDA:
            if not self.source_id.isascii() or not self.source_id.isdigit():
                raise ValueError("USDA source_id must contain only digits")
        elif self.source is RecipeFoodSource.COMMUNITY:
            parts = self.source_id.split("-")
            valid_slug = all(
                part and part.isascii() and part.isalnum() and part == part.lower()
                for part in parts
            )
            if not valid_slug:
                raise ValueError("Community source_id must be a lowercase slug")
        elif self.source is RecipeFoodSource.OPEN_FOOD_FACTS:
            if not self.source_id.isascii() or not self.source_id.isdigit():
                raise ValueError("Open Food Facts source_id must contain only digits")
        else:
            try:
                UUID(self.source_id)
            except ValueError as error:
                raise ValueError("Custom source_id must be a UUID") from error
        return self


class RecipeIngredientWrite(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    food: RecipeFoodReference
    grams: Decimal

    @field_validator("grams")
    @classmethod
    def validate_grams(cls, value: Decimal) -> Decimal:
        return Quantity(amount=value, unit=QuantityUnit.GRAM).amount


class RecipeWrite(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: RecipeName
    yield_grams: Decimal
    ingredients: list[RecipeIngredientWrite] = Field(min_length=1, max_length=100)

    @field_validator("yield_grams")
    @classmethod
    def validate_yield_grams(cls, value: Decimal) -> Decimal:
        return Quantity(amount=value, unit=QuantityUnit.GRAM).amount


class RecipeIngredientFood(RecipeFoodReference):
    name: str


class RecipeIngredientResponse(BaseModel):
    id: UUID
    position: int = Field(ge=0)
    food: RecipeIngredientFood
    grams: Decimal
    snapshot: NutrientSnapshotPayload

    @field_serializer("grams", when_used="json")
    def serialize_grams(self, value: Decimal) -> str:
        return format(value, "f")


class RecipeResponse(BaseModel):
    id: UUID
    name: str
    yield_grams: Decimal
    is_public: Literal[False] = False
    ingredients: list[RecipeIngredientResponse]
    total: NutrientSnapshotPayload
    nutrients_per_100g: Mapping[str, Decimal]

    @field_serializer("yield_grams", when_used="json")
    def serialize_yield_grams(self, value: Decimal) -> str:
        return format(value, "f")

    @field_serializer("nutrients_per_100g", when_used="json")
    def serialize_nutrients_per_100g(
        self, value: Mapping[str, Decimal]
    ) -> dict[str, str]:
        quantum = Decimal("0.01")
        return {
            code: format(amount.quantize(quantum, rounding=ROUND_HALF_UP), "f")
            for code, amount in value.items()
        }


class RecipeListResponse(BaseModel):
    items: list[RecipeResponse]
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    has_more: bool
