from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from opennosh_api.nutrition import (
    DeclaredNutrients,
    HouseholdPortion,
    NutrientBasis,
    NutrientProfile,
    NutrientValues,
    Quantity,
    QuantityUnit,
    convert_quantity,
)


def profile(*, density: Decimal | None = Decimal("1.03")) -> NutrientProfile:
    return NutrientProfile(
        nutrients=NutrientValues.model_validate(
            {
                "energy_kcal": "170",
                "protein_g": "10",
                "fat_g": "10",
                "carbohydrate_g": "10",
                "sodium_mg": "125.75",
            }
        ),
        density_g_per_ml=density,
    )


positive_amounts = st.decimals(
    min_value=Decimal("0.000001"),
    max_value=Decimal("10000"),
    places=6,
    allow_nan=False,
    allow_infinity=False,
)


@given(grams=positive_amounts)
def test_gram_conversion_scales_every_nutrient_without_rounding(grams: Decimal) -> None:
    result = convert_quantity(profile(), Quantity(amount=grams, unit=QuantityUnit.GRAM))

    assert result.grams == grams
    assert result.nutrients["protein_g"] == Decimal("10") * grams / Decimal(100)
    assert result.nutrients["sodium_mg"] == Decimal("125.75") * grams / Decimal(100)


@given(millilitres=positive_amounts)
def test_millilitre_conversion_is_identical_to_its_density_derived_mass(
    millilitres: Decimal,
) -> None:
    nutrients = profile()
    volume_result = convert_quantity(
        nutrients,
        Quantity(amount=millilitres, unit=QuantityUnit.MILLILITRE),
    )
    mass_result = convert_quantity(
        nutrients,
        Quantity(
            amount=millilitres * Decimal("1.03"),
            unit=QuantityUnit.GRAM,
        ),
    )

    assert volume_result == mass_result


def test_one_hundred_millilitres_reproduce_the_declared_source_values() -> None:
    declared = DeclaredNutrients(
        basis=NutrientBasis.PER_100ML,
        nutrients=profile().nutrients,
        density_g_per_ml=Decimal("1.25"),
    )

    result = convert_quantity(
        declared.to_canonical(),
        Quantity(amount=Decimal(100), unit=QuantityUnit.MILLILITRE),
    )

    assert result.nutrients == declared.nutrients


@given(count=positive_amounts)
def test_named_portion_conversion_is_identical_to_its_declared_mass(count: Decimal) -> None:
    portion = HouseholdPortion(name="1 roti", grams=Decimal("42"))
    portion_result = convert_quantity(
        profile(),
        Quantity(amount=count, unit=QuantityUnit.NAMED_PORTION, portion_name="1 ROTI"),
        portions=[portion],
    )
    mass_result = convert_quantity(
        profile(),
        Quantity(amount=count * portion.grams, unit=QuantityUnit.GRAM),
    )

    assert portion_result == mass_result


@given(first=positive_amounts, second=positive_amounts)
def test_conversion_is_additive(first: Decimal, second: Decimal) -> None:
    nutrients = profile()
    combined = convert_quantity(nutrients, Quantity(amount=first + second, unit=QuantityUnit.GRAM))
    first_snapshot = convert_quantity(nutrients, Quantity(amount=first, unit=QuantityUnit.GRAM))
    second_snapshot = convert_quantity(nutrients, Quantity(amount=second, unit=QuantityUnit.GRAM))

    for code in combined.nutrients.codes():
        assert combined.nutrients[code] == (
            first_snapshot.nutrients[code] + second_snapshot.nutrients[code]
        )


def test_conversion_rejects_missing_density_and_unknown_or_duplicate_portions() -> None:
    with pytest.raises(ValueError, match="requires density"):
        convert_quantity(
            profile(density=None),
            Quantity(amount=Decimal(100), unit=QuantityUnit.MILLILITRE),
        )
    with pytest.raises(ValueError, match="Unknown household portion"):
        convert_quantity(
            profile(),
            Quantity(
                amount=Decimal(1),
                unit=QuantityUnit.NAMED_PORTION,
                portion_name="1 ladle",
            ),
        )
    duplicate = HouseholdPortion(name="1 ladle", grams=Decimal(50))
    with pytest.raises(ValueError, match="must be unique"):
        convert_quantity(
            profile(),
            Quantity(
                amount=Decimal(1),
                unit=QuantityUnit.NAMED_PORTION,
                portion_name="1 ladle",
            ),
            portions=[duplicate, duplicate],
        )


def test_conversion_rejects_a_resulting_mass_above_the_snapshot_limit() -> None:
    with pytest.raises(ValueError, match="Snapshot grams"):
        convert_quantity(
            profile(density=Decimal(5)),
            Quantity(amount=Decimal("1000000"), unit=QuantityUnit.MILLILITRE),
        )


def test_conversion_preserves_valid_authoritative_energy_factors() -> None:
    authoritative = NutrientProfile.from_authoritative_source(
        {
            "energy_kcal": Decimal("100"),
            "protein_g": Decimal("0"),
            "fat_g": Decimal("0"),
            "carbohydrate_g": Decimal("0"),
        }
    )

    result = convert_quantity(
        authoritative,
        Quantity(amount=Decimal("50"), unit=QuantityUnit.GRAM),
    )

    assert result.nutrients["energy_kcal"] == Decimal("50")
    assert result.nutrients["protein_g"] == Decimal("0")
