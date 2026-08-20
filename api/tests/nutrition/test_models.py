from decimal import Decimal, localcontext

import pytest
from opennosh_api.nutrition import (
    DeclaredNutrients,
    HouseholdPortion,
    NutrientBasis,
    NutrientProfile,
    NutrientSnapshot,
    NutrientValues,
    Quantity,
    QuantityUnit,
    convert_quantity,
)
from opennosh_api.nutrition.models import deterministic_divide, deterministic_multiply
from pydantic import ValidationError


def nutrient_values(**overrides: object) -> NutrientValues:
    values: dict[str, object] = {
        "energy_kcal": "170",
        "protein_g": "10",
        "fat_g": "10",
        "carbohydrate_g": "10",
        "sodium_mg": "100.5",
    }
    values.update(overrides)
    return NutrientValues.model_validate(values)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"protein_g": "-1"}, "finite and non-negative"),
        ({"protein_g": "NaN"}, "finite number"),
        ({"protein_g": "Infinity"}, "finite number"),
        ({"protein_g": "101", "energy_kcal": "534"}, "cannot exceed 100"),
        (
            {"energy_kcal": "901", "fat_g": "89", "carbohydrate_g": "15"},
            "cannot exceed 900",
        ),
        ({"sodium_mg": "100000.01"}, "cannot exceed 100000"),
        ({"energy_kcal": "400"}, "differs from energy_kcal"),
        ({"protein": "10"}, "Invalid nutrient code"),
    ],
)
def test_nutrient_values_reject_invalid_and_impossible_values(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        NutrientProfile(nutrients=nutrient_values(**overrides))


def test_nutrient_values_require_core_macros_and_reject_booleans() -> None:
    with pytest.raises(ValidationError, match="Missing required nutrients"):
        NutrientValues.model_validate({"energy_kcal": 0})
    with pytest.raises(ValidationError, match="not booleans"):
        nutrient_values(sodium_mg=True)


def test_authoritative_source_values_may_use_food_specific_energy_factors() -> None:
    published = {
        "energy_kcal": Decimal("472"),
        "protein_g": Decimal("12.1"),
        "fat_g": Decimal("47.7"),
        "carbohydrate_g": Decimal("36.2"),
    }

    with pytest.raises(ValidationError, match="Macro-derived energy"):
        NutrientValues.model_validate(published)

    exact = NutrientValues.from_authoritative_source(published)

    assert exact["energy_kcal"] == Decimal("472")


def test_authoritative_source_accepts_valid_high_energy_oil() -> None:
    published = {
        "energy_kcal": Decimal("902"),
        "energy_kj": Decimal("3774"),
        "protein_g": Decimal("0"),
        "fat_g": Decimal("100"),
        "carbohydrate_g": Decimal("0"),
    }

    with pytest.raises(ValidationError, match="cannot exceed 900"):
        NutrientProfile(nutrients=NutrientValues.from_authoritative_source(published))

    profile = NutrientProfile.from_authoritative_source(published)

    assert profile.nutrients["energy_kcal"] == Decimal("902")


def test_zero_energy_requires_zero_energy_macros() -> None:
    zero = nutrient_values(energy_kcal="0", protein_g="0", fat_g="0", carbohydrate_g="0")
    assert zero["energy_kcal"] == 0

    with pytest.raises(ValidationError, match="differs from energy_kcal"):
        nutrient_values(energy_kcal="0", protein_g="1", fat_g="0", carbohydrate_g="0")


def test_per_100ml_declarations_require_density_and_canonicalise_to_per_100g() -> None:
    with pytest.raises(ValidationError, match="require density"):
        DeclaredNutrients(basis=NutrientBasis.PER_100ML, nutrients=nutrient_values())

    declared = DeclaredNutrients(
        basis=NutrientBasis.PER_100ML,
        nutrients=nutrient_values(),
        density_g_per_ml=Decimal("1.25"),
    )

    canonical = declared.to_canonical()

    assert canonical.basis is NutrientBasis.PER_100G
    assert canonical.nutrients["energy_kcal"] == Decimal("136")
    assert canonical.density_g_per_ml == Decimal("1.25")


def test_per_100ml_limits_are_evaluated_after_canonicalisation() -> None:
    dense_source = DeclaredNutrients(
        basis=NutrientBasis.PER_100ML,
        density_g_per_ml=Decimal("1.25"),
        nutrients=nutrient_values(
            energy_kcal="1125",
            protein_g="0",
            fat_g="125",
            carbohydrate_g="0",
        ),
    )

    canonical = dense_source.to_canonical()

    assert canonical.nutrients["energy_kcal"] == Decimal("900")
    assert canonical.nutrients["fat_g"] == Decimal("100")

    with pytest.raises(ValidationError, match="cannot exceed"):
        DeclaredNutrients(
            basis=NutrientBasis.PER_100ML,
            density_g_per_ml=Decimal("0.5"),
            nutrients=nutrient_values(
                energy_kcal="900",
                protein_g="0",
                fat_g="100",
                carbohydrate_g="0",
            ),
        )


@pytest.mark.parametrize("basis", [NutrientBasis.PER_100G, NutrientBasis.PER_100ML])
def test_canonical_passthrough_when_no_scaling_is_needed(basis: NutrientBasis) -> None:
    values = nutrient_values()
    declared = DeclaredNutrients(
        basis=basis,
        nutrients=values,
        density_g_per_ml=Decimal(1),
    )

    assert declared.to_canonical().nutrients == values


@pytest.mark.parametrize(
    "density",
    ["0", "-1", "NaN", "Infinity", "0.009999", "1e-1000000", "5.01"],
)
def test_density_rejects_non_food_values(density: str) -> None:
    with pytest.raises(ValidationError, match="Density|finite number"):
        NutrientProfile(nutrients=nutrient_values(), density_g_per_ml=Decimal(density))


def test_canonical_values_do_not_depend_on_process_decimal_precision() -> None:
    def calculated_protein(precision: int) -> tuple[Decimal, Decimal]:
        with localcontext() as context:
            context.prec = precision
            declared = DeclaredNutrients(
                basis=NutrientBasis.PER_100ML,
                nutrients=nutrient_values(),
                density_g_per_ml=Decimal("1.03"),
            )
            canonical = declared.to_canonical()
            snapshot = convert_quantity(
                canonical,
                Quantity(amount=Decimal(100), unit=QuantityUnit.MILLILITRE),
            )
            return canonical.nutrients["protein_g"], snapshot.nutrients["protein_g"]

    assert calculated_protein(6) == calculated_protein(60)


def test_quantity_and_portion_boundaries_are_validated() -> None:
    assert HouseholdPortion(name=" 1 katori ", grams=Decimal("150")).name == "1 katori"
    assert HouseholdPortion(name="maximum", grams=Decimal("10000")).grams == Decimal("10000")
    assert Quantity(amount=Decimal("1000000"), unit=QuantityUnit.GRAM).amount == Decimal(
        "1000000"
    )
    with pytest.raises(ValidationError, match="Portion grams"):
        HouseholdPortion(name="1 katori", grams=Decimal(0))
    with pytest.raises(ValidationError, match="greater than zero"):
        Quantity(amount=Decimal(-1), unit=QuantityUnit.GRAM)
    with pytest.raises(ValidationError, match="require portion_name"):
        Quantity(amount=Decimal(1), unit=QuantityUnit.NAMED_PORTION)
    with pytest.raises(ValidationError, match="only valid"):
        Quantity(amount=Decimal(1), unit=QuantityUnit.GRAM, portion_name="1 katori")


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "1000000.01"])
def test_quantity_rejects_nonfinite_and_over_maximum_values(amount: str) -> None:
    with pytest.raises(ValidationError, match="finite number|Quantity"):
        Quantity(amount=Decimal(amount), unit=QuantityUnit.GRAM)


@pytest.mark.parametrize("grams", ["NaN", "Infinity", "10000.01"])
def test_portion_rejects_nonfinite_and_over_maximum_values(grams: str) -> None:
    with pytest.raises(ValidationError, match="finite number|Portion grams"):
        HouseholdPortion(name="serving", grams=Decimal(grams))


def test_extreme_finite_nutrient_values_are_rejected_before_rounding() -> None:
    with pytest.raises(ValidationError, match="supported numeric range"):
        nutrient_values(sodium_mg=Decimal("1e999999"))


@pytest.mark.parametrize("factor", ["0", "-1", "NaN", "Infinity"])
def test_nutrient_scaling_rejects_invalid_factors(factor: str) -> None:
    with pytest.raises(ValueError, match="scale factor"):
        nutrient_values().scaled(Decimal(factor))


def test_decimal_arithmetic_failures_become_domain_errors() -> None:
    with pytest.raises(ValueError, match="multiplication exceeds"):
        deterministic_multiply(Decimal("1e999999"), Decimal("1e999999"))
    with pytest.raises(ValueError, match="division exceeds"):
        deterministic_divide(Decimal("1e999999"), Decimal("1e-999999"))


@pytest.mark.parametrize("grams", ["0", "1000000.01"])
def test_snapshot_rejects_invalid_computed_mass(grams: str) -> None:
    with pytest.raises(ValidationError, match="Snapshot grams"):
        NutrientSnapshot(grams=Decimal(grams), nutrients=nutrient_values())


def test_computed_snapshot_is_deeply_immutable_and_rounds_only_at_api_boundary() -> None:
    snapshot = NutrientSnapshot(
        grams=Decimal("1"),
        nutrients=nutrient_values().scaled(Decimal("0.01")),
    )

    with pytest.raises(ValidationError):
        snapshot.grams = Decimal("2")
    with pytest.raises(TypeError):
        snapshot.nutrients.root["sodium_mg"] = Decimal("2")  # type: ignore[index]

    assert snapshot.nutrients["sodium_mg"] == Decimal("1.005")
    rounded = snapshot.rounded_for_api(2)
    assert rounded.nutrients["sodium_mg"] == Decimal("1.01")
    assert snapshot.nutrients["sodium_mg"] == Decimal("1.005")
    with pytest.raises(ValueError, match="between 0 and 6"):
        snapshot.rounded_for_api(7)
