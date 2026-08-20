from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    ValidationInfo,
    field_serializer,
    field_validator,
    model_validator,
)

from opennosh_api.models import TargetDayType
from opennosh_api.targets.constants import (
    MAX_KCAL,
    MAX_MACRO_GRAMS,
    MAX_TARGET_SCHEDULE_ITEMS,
    TARGET_DECIMAL_PLACES,
    TARGET_QUANTUM,
)


def _bounded_decimal(value: Decimal, *, maximum: Decimal, field_name: str) -> Decimal:
    if not value.is_finite():
        raise ValueError(f"{field_name} must be finite")
    if value < 0 or value > maximum:
        raise ValueError(f"{field_name} must be between 0 and {maximum}")
    parts = value.as_tuple()
    if not isinstance(parts.exponent, int):  # pragma: no cover - finite check is authoritative
        raise ValueError(f"{field_name} must be finite")
    fractional_excess = max(-parts.exponent - TARGET_DECIMAL_PLACES, 0)
    if fractional_excess and any(parts.digits[-fractional_excess:]):
        raise ValueError(f"{field_name} must have at most two decimal places")
    return value


class TargetWrite(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    day_type: TargetDayType
    kcal: Decimal
    protein_g: Decimal
    carb_g: Decimal
    fat_g: Decimal
    active_from: date
    active_until: date | None = None
    confirm_below_floor: StrictBool = False

    @field_validator("kcal")
    @classmethod
    def validate_kcal(cls, value: Decimal) -> Decimal:
        return _bounded_decimal(value, maximum=MAX_KCAL, field_name="kcal")

    @field_validator("protein_g", "carb_g", "fat_g")
    @classmethod
    def validate_macro(cls, value: Decimal, info: ValidationInfo) -> Decimal:
        field_name = info.field_name or "macro"
        return _bounded_decimal(value, maximum=MAX_MACRO_GRAMS, field_name=field_name)

    @model_validator(mode="after")
    def validate_active_range(self) -> TargetWrite:
        if self.active_until is not None and self.active_until < self.active_from:
            raise ValueError("active_until must be on or after active_from")
        return self


class TargetScheduleWrite(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: Annotated[list[TargetWrite], Field(max_length=MAX_TARGET_SCHEDULE_ITEMS)]

    @model_validator(mode="after")
    def validate_non_overlapping_ranges(self) -> TargetScheduleWrite:
        by_type: dict[TargetDayType, list[TargetWrite]] = {}
        for item in self.items:
            by_type.setdefault(item.day_type, []).append(item)
        for day_type, items in by_type.items():
            ordered = sorted(items, key=lambda item: item.active_from)
            for previous, current in zip(ordered, ordered[1:], strict=False):
                if previous.active_until is None or previous.active_until >= current.active_from:
                    raise ValueError(
                        f"{day_type.value} target active ranges must not overlap"
                    )
        return self


class TargetResponse(BaseModel):
    id: UUID
    day_type: TargetDayType
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
        return format(value.quantize(TARGET_QUANTUM), "f")


class TargetScheduleResponse(BaseModel):
    items: list[TargetResponse]
    target_kcal_floor: Decimal
    safety_copy: str

    @field_serializer("target_kcal_floor", when_used="json")
    def serialize_floor(self, value: Decimal) -> str:
        return format(value.quantize(TARGET_QUANTUM), "f")
