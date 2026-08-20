from decimal import Decimal, getcontext, localcontext

import pytest
from opennosh_api.nutrition import NutrientSnapshot
from opennosh_api.recipes.service import RecipeInputError, compose_recipe


def _snapshot(grams: str, nutrients: dict[str, str]) -> NutrientSnapshot:
    return NutrientSnapshot.model_validate(
        {"grams": grams, "nutrients": nutrients},
        context={"authoritative_source": True},
    )


def test_composition_sums_ingredient_snapshots_and_uses_yield() -> None:
    ingredients = (
        _snapshot(
            "100",
            {
                "energy_kcal": "100",
                "protein_g": "10",
                "carbohydrate_g": "15",
                "fat_g": "0",
            },
        ),
        _snapshot(
            "50",
            {
                "energy_kcal": "95",
                "protein_g": "5",
                "carbohydrate_g": "7.5",
                "fat_g": "5",
            },
        ),
    )

    composition = compose_recipe(ingredients, yield_grams=Decimal("300"))

    assert composition.total.grams == Decimal("300")
    assert composition.total.nutrients["energy_kcal"] == Decimal("195")
    assert composition.total.nutrients["protein_g"] == Decimal("15")
    assert composition.profile.nutrients["energy_kcal"] == Decimal("65")
    assert composition.profile.nutrients["carbohydrate_g"] == Decimal("7.5")


def test_composition_is_independent_of_ambient_decimal_context() -> None:
    ingredient = _snapshot(
        "1",
        {
            "energy_kcal": "1.234567890123456789",
            "protein_g": "0.1",
            "carbohydrate_g": "0.2",
            "fat_g": "0.001",
        },
    )
    original_precision = getcontext().prec
    expected = compose_recipe((ingredient,), yield_grams=Decimal("3"))
    with localcontext() as context:
        context.prec = 6
        composition = compose_recipe((ingredient,), yield_grams=Decimal("3"))

    assert getcontext().prec == original_precision
    assert composition.total.nutrients["energy_kcal"] == Decimal(
        "1.234567890123456789"
    )
    assert composition.profile == expected.profile


def test_composition_rejects_empty_or_physically_invalid_yields() -> None:
    with pytest.raises(RecipeInputError, match="at least one ingredient"):
        compose_recipe((), yield_grams=Decimal("100"))

    protein = _snapshot(
        "200",
        {
            "energy_kcal": "800",
            "protein_g": "200",
            "carbohydrate_g": "0",
            "fat_g": "0",
        },
    )
    with pytest.raises(RecipeInputError, match="invalid composition"):
        compose_recipe((protein,), yield_grams=Decimal("100"))
