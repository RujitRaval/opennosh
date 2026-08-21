from __future__ import annotations

import pytest
from opennosh_api.foods.schemas import CustomFoodCreate
from pydantic import ValidationError


def _payload() -> dict[str, object]:
    return {
        "name": "  My tofu  ",
        "nutrients": {
            "basis": "per_100g",
            "nutrients": {
                "energy_kcal": "165",
                "protein_g": "10",
                "carbohydrate_g": "20",
                "fat_g": "5",
            },
        },
        "portions": [{"name": "slice", "grams": "25"}],
    }


def test_custom_food_normalizes_name_and_validates_portions() -> None:
    food = CustomFoodCreate.model_validate(_payload())

    assert food.name == "My tofu"
    assert food.portions[0].name == "slice"


@pytest.mark.parametrize(
    "mutation",
    [
        {"name": "bad\x00name"},
        {"portions": [{"name": "Scoop", "grams": "30"}, {"name": "scoop", "grams": "20"}]},
        {"portions": [{"name": "bad\nname", "grams": "20"}]},
    ],
)
def test_custom_food_rejects_unsafe_or_ambiguous_names(
    mutation: dict[str, object],
) -> None:
    payload = _payload() | mutation

    with pytest.raises(ValidationError):
        CustomFoodCreate.model_validate(payload)


def test_custom_food_rejects_macro_energy_mismatch() -> None:
    payload = _payload()
    payload["nutrients"] = {
        "basis": "per_100g",
        "nutrients": {
            "energy_kcal": "500",
            "protein_g": "10",
            "carbohydrate_g": "20",
            "fat_g": "5",
        },
    }

    with pytest.raises(ValidationError):
        CustomFoodCreate.model_validate(payload)
