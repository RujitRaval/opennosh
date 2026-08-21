from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from opennosh_api.main import create_app
from opennosh_api.models import LoadUnit
from opennosh_api.workouts.schemas import WorkoutCreate, WorkoutSetWrite
from opennosh_api.workouts.service import (
    WorkoutInputError,
    set_volume,
    utc_date_bounds,
)
from pydantic import ValidationError

EXERCISE_ID = "00000000-0000-0000-0000-000000000016"


@pytest.mark.parametrize(
    ("load_unit", "load_value"),
    [
        ("kg", "100"),
        ("lb", "225.5"),
        ("machine_units", "12"),
        ("bodyweight", None),
        ("band", None),
        ("rpe_only", "8.5"),
    ],
)
def test_all_load_units_have_explicit_value_contracts(
    load_unit: str, load_value: str | None
) -> None:
    item = WorkoutSetWrite.model_validate(
        {
            "exercise_id": EXERCISE_ID,
            "reps": 8,
            "load_value": load_value,
            "load_unit": load_unit,
        }
    )

    assert item.exercise_id == UUID(EXERCISE_ID)
    assert item.load_unit is LoadUnit(load_unit)


@pytest.mark.parametrize(
    ("load_unit", "load_value", "message"),
    [
        ("kg", None, "required for kg"),
        ("lb", None, "required for lb"),
        ("machine_units", None, "required for machine_units"),
        ("bodyweight", "1", "omitted for bodyweight"),
        ("band", "1", "omitted for band"),
        ("rpe_only", None, "between 1 and 10"),
        ("rpe_only", "0.999", "between 1 and 10"),
        ("rpe_only", "10.001", "between 1 and 10"),
    ],
)
def test_incompatible_load_values_are_rejected(
    load_unit: str, load_value: str | None, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        WorkoutSetWrite.model_validate(
            {
                "exercise_id": EXERCISE_ID,
                "reps": 8,
                "load_value": load_value,
                "load_unit": load_unit,
            }
        )


def test_reps_reject_boolean_coercion() -> None:
    with pytest.raises(ValidationError):
        WorkoutSetWrite.model_validate(
            {
                "exercise_id": EXERCISE_ID,
                "reps": True,
                "load_value": "100",
                "load_unit": "kg",
            }
        )


@pytest.mark.parametrize("reps", [0, 100_001])
def test_reps_are_bounded(reps: int) -> None:
    with pytest.raises(ValidationError):
        WorkoutSetWrite.model_validate(
            {
                "exercise_id": EXERCISE_ID,
                "reps": reps,
                "load_value": "100",
                "load_unit": "kg",
            }
        )


def test_workout_create_rejects_more_than_500_sets() -> None:
    workout_set = {
        "exercise_id": EXERCISE_ID,
        "reps": 8,
        "load_value": "100",
        "load_unit": "kg",
    }

    with pytest.raises(ValidationError):
        WorkoutCreate.model_validate(
            {
                "performed_at": "2026-08-20T12:00:00Z",
                "sets": [workout_set] * 501,
            }
        )


@pytest.mark.parametrize("load_value", ["-0.001", "1000000.001", "NaN", "Infinity"])
def test_load_value_is_finite_nonnegative_and_bounded(load_value: str) -> None:
    with pytest.raises(ValidationError):
        WorkoutSetWrite.model_validate(
            {
                "exercise_id": EXERCISE_ID,
                "reps": 8,
                "load_value": load_value,
                "load_unit": "kg",
            }
        )


def test_load_value_has_at_most_three_nonzero_decimal_places() -> None:
    with pytest.raises(ValidationError, match="at most three decimal places"):
        WorkoutSetWrite.model_validate(
            {
                "exercise_id": EXERCISE_ID,
                "reps": 8,
                "load_value": "100.0001",
                "load_unit": "kg",
            }
        )

    accepted = WorkoutSetWrite.model_validate(
        {
            "exercise_id": EXERCISE_ID,
            "reps": 8,
            "load_value": "100.1000",
            "load_unit": "kg",
        }
    )
    assert accepted.load_value == Decimal("100.1000")


def test_workout_timestamp_notes_and_extra_fields_are_bounded() -> None:
    payload: dict[str, object] = {
        "performed_at": "2026-08-20T12:00:00",
        "notes": "  useful notes  ",
        "sets": [],
    }
    with pytest.raises(ValidationError, match="UTC offset"):
        WorkoutCreate.model_validate(payload)

    for numeric_epoch in (0, 1.5, "0"):
        payload["performed_at"] = numeric_epoch
        with pytest.raises(ValidationError, match="ISO 8601 date-time string"):
            WorkoutCreate.model_validate(payload)

    payload["performed_at"] = "0001-01-01T00:00:00Z"
    with pytest.raises(ValidationError, match="supported UTC range"):
        WorkoutCreate.model_validate(payload)

    payload["performed_at"] = "9999-12-31T23:59:59.999999Z"
    with pytest.raises(ValidationError, match="supported UTC range"):
        WorkoutCreate.model_validate(payload)

    payload["performed_at"] = "2026-08-20T12:00:00Z"
    workout = WorkoutCreate.model_validate(payload)
    assert workout.notes == "useful notes"

    payload["notes"] = " \n\t "
    workout = WorkoutCreate.model_validate(payload)
    assert workout.notes is None

    payload["user_id"] = EXERCISE_ID
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        WorkoutCreate.model_validate(payload)


@pytest.mark.parametrize(
    ("notes", "message"),
    [
        ("x" * 5001, "notes must be at most 5000 characters"),
        ("unsafe\x00notes", "notes must not contain control characters"),
        ("unsafe\x7fnotes", "notes must not contain control characters"),
    ],
)
def test_workout_notes_reject_overlong_and_control_characters(
    notes: str, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        WorkoutCreate.model_validate(
            {
                "performed_at": "2026-08-20T12:00:00Z",
                "notes": notes,
                "sets": [],
            }
        )


@pytest.mark.parametrize(
    ("load_unit", "load_value", "expected"),
    [
        (LoadUnit.KG, Decimal("100.125"), Decimal("801.000")),
        (LoadUnit.LB, Decimal("225"), Decimal("1800")),
        (LoadUnit.MACHINE_UNITS, Decimal("12"), Decimal("96")),
        (LoadUnit.BODYWEIGHT, None, None),
        (LoadUnit.BAND, None, None),
        (LoadUnit.RPE_ONLY, Decimal("8.5"), None),
    ],
)
def test_volume_is_only_computed_for_comparable_numeric_units(
    load_unit: LoadUnit, load_value: Decimal | None, expected: Decimal | None
) -> None:
    assert set_volume(reps=8, load_value=load_value, load_unit=load_unit) == expected


def test_workout_date_bounds_are_inclusive_and_validate_order() -> None:
    start, end = utc_date_bounds(date(2026, 8, 20), date(2026, 8, 21))
    assert start.isoformat() == "2026-08-20T00:00:00+00:00"
    assert end is not None
    assert end.isoformat() == "2026-08-22T00:00:00+00:00"

    with pytest.raises(WorkoutInputError, match="from must be on or before to"):
        utc_date_bounds(date(2026, 8, 21), date(2026, 8, 20))
    _, maximum_end = utc_date_bounds(date.max, date.max)
    assert maximum_end is None


def test_openapi_exposes_all_load_units() -> None:
    openapi = create_app().openapi()
    schemas = openapi["components"]["schemas"]

    assert schemas["LoadUnit"]["enum"] == [member.value for member in LoadUnit]
    assert openapi["paths"]["/api/v1/workouts/trends"]["get"]
