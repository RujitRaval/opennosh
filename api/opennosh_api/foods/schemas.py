from __future__ import annotations

from enum import StrEnum
from typing import Any

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
