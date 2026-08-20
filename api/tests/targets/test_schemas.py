from decimal import Decimal

import pytest
from opennosh_api.main import create_app
from opennosh_api.targets.schemas import TargetScheduleWrite, TargetWrite


def _target(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "day_type": "training",
        "kcal": "2400",
        "protein_g": "180",
        "carb_g": "260",
        "fat_g": "70",
        "active_from": "2026-08-01",
        "active_until": "2026-08-31",
    }
    payload.update(overrides)
    return payload


def test_target_payload_preserves_exact_bounded_values() -> None:
    target = TargetWrite.model_validate(
        _target(kcal="20000.00", protein_g="2000.00", carb_g="0", fat_g="0.10")
    )

    assert target.kcal == Decimal("20000.00")
    assert target.protein_g == Decimal("2000.00")
    assert target.fat_g == Decimal("0.10")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("kcal", "-0.01"),
        ("kcal", "20000.01"),
        ("kcal", "NaN"),
        ("protein_g", "2000.01"),
        ("carb_g", "Infinity"),
        ("fat_g", "1.001"),
        ("fat_g", "1e-999999999"),
    ],
)
def test_target_payload_rejects_unbounded_or_overprecise_values(
    field: str, value: str
) -> None:
    with pytest.raises(ValueError):
        TargetWrite.model_validate(_target(**{field: value}))


def test_target_schedule_rejects_reversed_and_overlapping_inclusive_ranges() -> None:
    with pytest.raises(ValueError, match="active_until"):
        TargetWrite.model_validate(
            _target(active_from="2026-08-02", active_until="2026-08-01")
        )

    with pytest.raises(ValueError, match="must not overlap"):
        TargetScheduleWrite.model_validate(
            {
                "items": [
                    _target(active_until="2026-08-10"),
                    _target(active_from="2026-08-10", active_until=None),
                ]
            }
        )


def test_target_schedule_allows_adjacent_ranges_and_independent_day_types() -> None:
    schedule = TargetScheduleWrite.model_validate(
        {
            "items": [
                _target(active_until="2026-08-10"),
                _target(active_from="2026-08-11", active_until=None),
                _target(
                    day_type="rest",
                    active_from="2026-08-01",
                    active_until=None,
                ),
            ]
        }
    )

    assert len(schedule.items) == 3


def test_target_payload_rejects_owner_selection_and_unknown_day_types() -> None:
    with pytest.raises(ValueError):
        TargetWrite.model_validate(_target(user_id="8eb1a7ea-c696-4d70-a922-02b1604f1d70"))
    with pytest.raises(ValueError):
        TargetWrite.model_validate(_target(day_type="recovery"))


@pytest.mark.parametrize("confirmation", [1, 0, "yes", "true", "on"])
def test_below_floor_confirmation_requires_a_json_boolean(
    confirmation: object,
) -> None:
    with pytest.raises(ValueError):
        TargetWrite.model_validate(
            _target(kcal="1100", confirm_below_floor=confirmation)
        )


def test_target_schedule_rejects_more_than_one_thousand_items() -> None:
    with pytest.raises(ValueError):
        TargetScheduleWrite.model_validate({"items": [_target()] * 1001})


def test_openapi_registers_target_schedule_and_resolution() -> None:
    paths = create_app().openapi()["paths"]

    assert {"get", "put"}.issubset(paths["/api/v1/targets"])
    assert {"get"}.issubset(paths["/api/v1/targets/resolve"])
