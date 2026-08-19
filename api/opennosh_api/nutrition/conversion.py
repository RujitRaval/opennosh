from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from opennosh_api.nutrition.models import (
    HouseholdPortion,
    NutrientProfile,
    NutrientSnapshot,
    Quantity,
    QuantityUnit,
    deterministic_divide,
    deterministic_multiply,
)


def _portion_by_name(portions: Iterable[HouseholdPortion], portion_name: str) -> HouseholdPortion:
    matches = [
        portion for portion in portions if portion.name.casefold() == portion_name.casefold()
    ]
    if not matches:
        raise ValueError(f"Unknown household portion: {portion_name}")
    if len(matches) > 1:
        raise ValueError(f"Household portion names must be unique: {portion_name}")
    return matches[0]


def convert_quantity(
    profile: NutrientProfile,
    quantity: Quantity,
    *,
    portions: Iterable[HouseholdPortion] = (),
) -> NutrientSnapshot:
    """Convert a mass, volume, or named portion into an exact nutrient snapshot."""
    if quantity.unit is QuantityUnit.GRAM:
        grams = quantity.amount
    elif quantity.unit is QuantityUnit.MILLILITRE:
        if profile.density_g_per_ml is None:
            raise ValueError("Millilitre conversion requires density_g_per_ml")
        grams = deterministic_multiply(quantity.amount, profile.density_g_per_ml)
    elif quantity.unit is QuantityUnit.NAMED_PORTION:
        if quantity.portion_name is None:  # pragma: no cover - enforced at validation
            raise ValueError("Named-portion quantities require portion_name")
        portion = _portion_by_name(portions, quantity.portion_name)
        grams = deterministic_multiply(quantity.amount, portion.grams)
    else:  # pragma: no cover - Quantity validation keeps this exhaustive
        raise ValueError(f"Unsupported quantity unit: {quantity.unit}")

    return NutrientSnapshot(
        grams=grams,
        nutrients=profile.nutrients.scaled(deterministic_divide(grams, Decimal(100))),
    )
