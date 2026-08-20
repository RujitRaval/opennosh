from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_serializer, field_validator, model_validator

from opennosh_api.body_metrics.constants import (
    BODY_METRIC_DECIMAL_PLACES,
    BODY_METRIC_QUANTUM,
    MAX_BODY_METRIC_RECORDED_AT,
    MAX_BODY_METRIC_VALUE,
    MIN_BODY_METRIC_RECORDED_AT,
)
from opennosh_api.models import BodyMetricType, BodyMetricUnit

_CIRCUMFERENCE_TYPES = {
    BodyMetricType.HEIGHT,
    BodyMetricType.WAIST_CIRCUMFERENCE,
    BodyMetricType.HIP_CIRCUMFERENCE,
    BodyMetricType.CHEST_CIRCUMFERENCE,
    BodyMetricType.NECK_CIRCUMFERENCE,
    BodyMetricType.UPPER_ARM_CIRCUMFERENCE,
    BodyMetricType.THIGH_CIRCUMFERENCE,
}
_ALLOWED_UNITS = {
    BodyMetricType.BODY_WEIGHT: {BodyMetricUnit.KILOGRAM, BodyMetricUnit.POUND},
    BodyMetricType.BODY_FAT_PERCENTAGE: {BodyMetricUnit.PERCENT},
    **{
        metric_type: {BodyMetricUnit.CENTIMETER, BodyMetricUnit.INCH}
        for metric_type in _CIRCUMFERENCE_TYPES
    },
}


def _validate_value(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise ValueError("value must be finite")
    if value <= 0 or value > MAX_BODY_METRIC_VALUE:
        raise ValueError(f"value must be greater than 0 and at most {MAX_BODY_METRIC_VALUE}")
    parts = value.as_tuple()
    if not isinstance(parts.exponent, int):  # pragma: no cover - finite check is authoritative
        raise ValueError("value must be finite")
    fractional_excess = max(-parts.exponent - BODY_METRIC_DECIMAL_PLACES, 0)
    if fractional_excess and any(parts.digits[-fractional_excess:]):
        raise ValueError("value must have at most four decimal places")
    return value


class BodyMetricWrite(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    recorded_at: datetime
    metric_type: BodyMetricType
    value: Decimal
    unit: BodyMetricUnit

    @field_validator("recorded_at")
    @classmethod
    def require_aware_recorded_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("recorded_at must include a UTC offset")
        try:
            normalized = value.astimezone(UTC)
        except OverflowError as error:
            raise ValueError("recorded_at is outside the supported UTC range") from error
        if not MIN_BODY_METRIC_RECORDED_AT <= normalized <= MAX_BODY_METRIC_RECORDED_AT:
            raise ValueError("recorded_at is outside the supported UTC range")
        return value

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: Decimal) -> Decimal:
        return _validate_value(value)

    @model_validator(mode="after")
    def validate_unit_for_metric_type(self) -> BodyMetricWrite:
        if self.unit not in _ALLOWED_UNITS[self.metric_type]:
            allowed = ", ".join(sorted(unit.value for unit in _ALLOWED_UNITS[self.metric_type]))
            raise ValueError(f"unit for {self.metric_type.value} must be one of: {allowed}")
        return self


class BodyMetricResponse(BaseModel):
    id: UUID
    recorded_at: datetime
    metric_type: BodyMetricType
    value: Decimal
    unit: BodyMetricUnit

    @field_serializer("value", when_used="json")
    def serialize_value(self, value: Decimal) -> str:
        return format(value.quantize(BODY_METRIC_QUANTUM), "f")


class BodyMetricListResponse(BaseModel):
    from_date: date
    to_date: date
    items: list[BodyMetricResponse]
    limit: int
    offset: int
    has_more: bool
