from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from opennosh_api.models import LoadUnit
from opennosh_api.workouts.constants import (
    LOAD_DECIMAL_PLACES,
    LOAD_QUANTUM,
    MAX_LOAD_VALUE,
    MAX_REPS,
    MAX_WORKOUT_NOTES_LENGTH,
    MAX_WORKOUT_PERFORMED_AT,
    MAX_WORKOUT_SETS,
    MIN_WORKOUT_PERFORMED_AT,
)


def _bounded_load(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    if not value.is_finite():
        raise ValueError("load_value must be finite")
    if value < 0 or value > MAX_LOAD_VALUE:
        raise ValueError(f"load_value must be between 0 and {MAX_LOAD_VALUE}")
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int):  # pragma: no cover - finite check is authoritative
        raise ValueError("load_value must be finite")
    fractional_excess = max(-exponent - LOAD_DECIMAL_PLACES, 0)
    if fractional_excess and any(value.as_tuple().digits[-fractional_excess:]):
        raise ValueError("load_value must have at most three decimal places")
    return value


def _clean_notes(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > MAX_WORKOUT_NOTES_LENGTH:
        raise ValueError(f"notes must be at most {MAX_WORKOUT_NOTES_LENGTH} characters")
    if any(
        (ord(character) < 32 and character not in "\n\t") or ord(character) == 127
        for character in normalized
    ):
        raise ValueError("notes must not contain control characters")
    return normalized


def _validate_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("performed_at must include a UTC offset")
    try:
        normalized = value.astimezone(UTC)
    except (OverflowError, ValueError) as error:
        raise ValueError("performed_at is outside the supported UTC range") from error
    if not MIN_WORKOUT_PERFORMED_AT <= normalized <= MAX_WORKOUT_PERFORMED_AT:
        raise ValueError("performed_at is outside the supported UTC range")
    return value


def _require_datetime_input(value: object) -> object:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise ValueError("performed_at must be an ISO 8601 date-time string")
    try:
        Decimal(value.strip())
    except InvalidOperation:
        return value
    raise ValueError("performed_at must be an ISO 8601 date-time string")


class WorkoutSetWrite(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    exercise_id: UUID
    reps: int = Field(strict=True, ge=1, le=MAX_REPS)
    load_value: Decimal | None = None
    load_unit: LoadUnit

    @field_validator("load_value")
    @classmethod
    def validate_load(cls, value: Decimal | None) -> Decimal | None:
        return _bounded_load(value)

    @model_validator(mode="after")
    def validate_load_contract(self) -> WorkoutSetWrite:
        numeric_units = {LoadUnit.KG, LoadUnit.LB, LoadUnit.MACHINE_UNITS}
        if self.load_unit in numeric_units and self.load_value is None:
            raise ValueError(f"load_value is required for {self.load_unit.value}")
        if self.load_unit in {LoadUnit.BODYWEIGHT, LoadUnit.BAND}:
            if self.load_value is not None:
                raise ValueError(f"load_value must be omitted for {self.load_unit.value}")
        if self.load_unit is LoadUnit.RPE_ONLY:
            if self.load_value is None or not Decimal(1) <= self.load_value <= Decimal(10):
                raise ValueError("rpe_only load_value must be between 1 and 10")
        return self


class WorkoutCreate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    performed_at: datetime
    notes: str | None = None
    sets: list[WorkoutSetWrite] = Field(default_factory=list, max_length=MAX_WORKOUT_SETS)

    _require_performed_at_input = field_validator("performed_at", mode="before")(
        _require_datetime_input
    )
    _validate_performed_at = field_validator("performed_at")(_validate_timestamp)
    _validate_notes = field_validator("notes")(_clean_notes)


class WorkoutUpdate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    performed_at: datetime
    notes: str | None = None

    _require_performed_at_input = field_validator("performed_at", mode="before")(
        _require_datetime_input
    )
    _validate_performed_at = field_validator("performed_at")(_validate_timestamp)
    _validate_notes = field_validator("notes")(_clean_notes)


class WorkoutExerciseResponse(BaseModel):
    id: UUID
    slug: str
    name: str
    source: str
    source_id: str
    source_url: str
    derivative_source_url: str | None
    license_spdx: str
    license_url: str
    author: str | None
    author_url: str | None
    attribution_text: str
    translation_attribution: list[dict[str, Any]]


class WorkoutSetResponse(BaseModel):
    id: UUID
    position: int = Field(ge=0)
    exercise: WorkoutExerciseResponse
    reps: int = Field(ge=1)
    load_value: Decimal | None
    load_unit: LoadUnit
    volume: Decimal | None

    @field_serializer("load_value", "volume", when_used="json")
    def serialize_decimal(self, value: Decimal | None) -> str | None:
        return None if value is None else format(value.quantize(LOAD_QUANTUM), "f")


class WorkoutVolumeGroup(BaseModel):
    exercise_id: UUID
    load_unit: LoadUnit
    volume: Decimal

    @field_serializer("volume", when_used="json")
    def serialize_volume(self, value: Decimal) -> str:
        return format(value.quantize(LOAD_QUANTUM), "f")


class WorkoutResponse(BaseModel):
    id: UUID
    performed_at: datetime
    notes: str | None
    sets: list[WorkoutSetResponse]
    volume_groups: list[WorkoutVolumeGroup]


class WorkoutListResponse(BaseModel):
    from_date: date
    to_date: date
    items: list[WorkoutResponse]
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    has_more: bool


class WorkoutVolumeResponse(BaseModel):
    from_date: date
    to_date: date
    exercise_id: UUID
    load_unit: LoadUnit | None
    volume: Decimal | None
    qualifying_sets: int = Field(ge=0)

    @field_serializer("volume", when_used="json")
    def serialize_volume(self, value: Decimal | None) -> str | None:
        return None if value is None else format(value.quantize(LOAD_QUANTUM), "f")
