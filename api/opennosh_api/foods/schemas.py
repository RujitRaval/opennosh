from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


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
    items: list[FoodSearchItem]
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    has_more: bool


class FoodDetail(FoodSearchItem):
    nutrients: dict[str, Any]
    portions: list[dict[str, Any]]


class OpenFoodFactsAttribution(BaseModel):
    source: Literal["openfoodfacts"] = "openfoodfacts"
    source_url: str
    database_license: Literal["ODbL-1.0"] = "ODbL-1.0"
    contents_license: Literal["DbCL-1.0"] = "DbCL-1.0"
    attribution_text: str


class OpenFoodFactsFood(BaseModel):
    id: str
    source: Literal["openfoodfacts"] = "openfoodfacts"
    source_id: str
    barcode: str
    name: str
    brand: str | None = None
    nutrients: dict[str, Any]
    portions: list[dict[str, Any]] = Field(default_factory=list)
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
