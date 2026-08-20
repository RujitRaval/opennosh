from datetime import UTC, datetime
from decimal import Decimal

import pytest
from opennosh_api.logs.schemas import (
    FoodLogReference,
    FoodLogSource,
    LogEntryCreate,
    LogQuantity,
)
from opennosh_api.main import create_app
from opennosh_api.nutrition import QuantityUnit


def test_log_payload_normalizes_meal_slot_and_preserves_exact_quantity() -> None:
    payload = LogEntryCreate(
        logged_at=datetime(2026, 8, 20, 12, tzinfo=UTC),
        meal_slot="  post workout  ",
        food=FoodLogReference(source=FoodLogSource.COMMUNITY, source_id="dal-rice"),
        quantity=LogQuantity(
            amount=Decimal("1.25"),
            unit=QuantityUnit.NAMED_PORTION,
            portion_name="1 bowl",
        ),
    )

    assert payload.meal_slot == "post workout"
    assert payload.quantity.to_quantity().amount == Decimal("1.25")
    assert payload.model_dump(mode="json")["quantity"]["amount"] == "1.25"


@pytest.mark.parametrize(
    ("source", "source_id"),
    [
        (FoodLogSource.USDA, "abc"),
        (FoodLogSource.COMMUNITY, "Not-A-Slug"),
        (FoodLogSource.OPEN_FOOD_FACTS, "123x"),
        (FoodLogSource.CUSTOM, "not-a-uuid"),
    ],
)
def test_food_reference_rejects_source_incompatible_identifiers(
    source: FoodLogSource, source_id: str
) -> None:
    with pytest.raises(ValueError):
        FoodLogReference(source=source, source_id=source_id)


def test_log_payload_rejects_naive_time_control_slots_and_bad_quantities() -> None:
    valid = {
        "logged_at": "2026-08-20T12:00:00Z",
        "meal_slot": "lunch",
        "food": {"source": "usda", "source_id": "100"},
        "quantity": {"amount": "10", "unit": "g"},
    }
    for changes in (
        {"logged_at": "2026-08-20T12:00:00"},
        {"meal_slot": "bad\nslot"},
        {"quantity": {"amount": "0", "unit": "g"}},
        {"quantity": {"amount": "1", "unit": "portion"}},
        {"quantity": {"amount": "1", "unit": "g", "portion_name": "cup"}},
    ):
        with pytest.raises(ValueError):
            LogEntryCreate.model_validate({**valid, **changes})


def test_openapi_registers_all_food_log_operations() -> None:
    paths = create_app().openapi()["paths"]

    assert {"get", "post"}.issubset(paths["/api/v1/logs"])
    assert paths["/api/v1/logs/daily-totals"]["get"]
    assert {"get", "delete"}.issubset(paths["/api/v1/logs/{entry_id}"])
