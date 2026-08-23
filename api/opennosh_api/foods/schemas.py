from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

from opennosh_api.nutrition import HouseholdPortion, NutrientProfile


class FoodSource(StrEnum):
    USDA = "usda"
    COMMUNITY = "community"


class FoodAttribution(BaseModel):
    source: FoodSource
    license: str
    source_uri: str | None = None
    source_license: str | None = None
    contributed_by: str | None = None
    pack_id: str | None = None
    pack_version: str | None = None
    provenance: str | None = None


class FoodSearchItem(BaseModel):
    id: str
    source: FoodSource
    source_id: str
    name: str
    name_local: str | None = None
    category: str | None = None
    attribution: FoodAttribution


class FoodSearchResponse(BaseModel):
    schema_version: Literal["2.0"] = "2.0"
    items: list[FoodSearchItem]
    limit: int = Field(ge=1)
    has_more: bool
    next_cursor: str | None = Field(default=None, max_length=2048)
    snapshot_id: UUID
    snapshot_expires_at: datetime


class FoodDetail(FoodSearchItem):
    schema_version: Literal["1.0"] = "1.0"
    nutrients: dict[str, Any]
    portions: list[HouseholdPortion]


class FoodCapabilities(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    barcode_lookup_enabled: bool


def _clean_custom_food_name(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("name must not be empty")
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise ValueError("name must not contain control characters")
    return normalized


CustomFoodName = Annotated[
    str,
    Field(max_length=255),
    AfterValidator(_clean_custom_food_name),
]


class CustomFoodCreate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: CustomFoodName
    nutrients: NutrientProfile
    portions: list[HouseholdPortion] = Field(default_factory=list, max_length=20)

    @field_validator("portions")
    @classmethod
    def validate_portions(cls, portions: list[HouseholdPortion]) -> list[HouseholdPortion]:
        normalized_names: set[str] = set()
        for portion in portions:
            if any(ord(character) < 32 or ord(character) == 127 for character in portion.name):
                raise ValueError("portion names must not contain control characters")
            normalized_name = portion.name.casefold()
            if normalized_name in normalized_names:
                raise ValueError("portion names must be unique")
            normalized_names.add(normalized_name)
        return portions


class CustomFoodResponse(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    id: UUID
    source: Literal["custom"] = "custom"
    source_id: str
    name: str
    nutrients: dict[str, Any]
    portions: list[HouseholdPortion]
    private: Literal[True] = True


class OpenFoodFactsAttribution(BaseModel):
    source: Literal["openfoodfacts"] = "openfoodfacts"
    source_url: str
    database_license: Literal["ODbL-1.0"] = "ODbL-1.0"
    contents_license: Literal["DbCL-1.0"] = "DbCL-1.0"
    attribution_text: str


class OpenFoodFactsFood(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    id: str
    source: Literal["openfoodfacts"] = "openfoodfacts"
    source_id: str
    barcode: str
    name: str
    brand: str | None = None
    nutrients: dict[str, Any]
    portions: list[HouseholdPortion] = Field(default_factory=list)
    attribution: OpenFoodFactsAttribution
    cached: bool


class OpenFoodFactsExportEntry(BaseModel):
    source: Literal["openfoodfacts"] = "openfoodfacts"
    barcode: str
    product_name: str
    brand: str | None = None
    nutrients: dict[str, Any]
    source_url: str
    database_license: Literal["ODbL-1.0"] = "ODbL-1.0"
    contents_license: Literal["DbCL-1.0"] = "DbCL-1.0"
    attribution_text: str


class OpenFoodFactsExport(BaseModel):
    schema_version: str = "1.0.0"
    dataset: str = "opennosh-open-food-facts-cache"
    source: str = "Open Food Facts"
    source_url: str = "https://world.openfoodfacts.org/"
    database_license: Literal["ODbL-1.0"] = "ODbL-1.0"
    database_license_url: str = "https://opendatacommons.org/licenses/odbl/1-0/"
    contents_license: Literal["DbCL-1.0"] = "DbCL-1.0"
    contents_license_url: str = "https://opendatacommons.org/licenses/dbcl/1-0/"
    notice: str = (
        "Contains information from Open Food Facts, made available under the Open "
        "Database License. Individual contents are available under the Database "
        "Contents License."
    )
    entries: list[OpenFoodFactsExportEntry]
