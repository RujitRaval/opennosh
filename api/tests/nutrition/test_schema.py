from decimal import Decimal

from fastapi.encoders import jsonable_encoder
from opennosh_api.nutrition import (
    DeclaredNutrients,
    NutrientBasis,
    NutrientProfile,
    NutrientSnapshot,
    NutrientValues,
    Quantity,
    QuantityUnit,
)


def test_api_models_accept_numeric_json_and_emit_canonical_basis() -> None:
    declared = DeclaredNutrients.model_validate(
        {
            "basis": "per_100ml",
            "density_g_per_ml": 1.25,
            "nutrients": {
                "energy_kcal": 170,
                "protein_g": 10,
                "fat_g": 10,
                "carbohydrate_g": 10,
            },
        }
    )

    payload = declared.to_canonical().model_dump(mode="json")

    assert payload["basis"] == "per_100g"
    assert payload["nutrients"]["energy_kcal"] == "136.0"


def test_json_schemas_publish_closed_unit_and_basis_enums() -> None:
    declared_schema = DeclaredNutrients.model_json_schema()
    quantity_schema = Quantity.model_json_schema()
    profile_schema = NutrientProfile.model_json_schema()
    nutrient_schema = NutrientValues.model_json_schema()

    assert set(declared_schema["$defs"]["NutrientBasis"]["enum"]) == {
        basis.value for basis in NutrientBasis
    }
    assert set(quantity_schema["$defs"]["QuantityUnit"]["enum"]) == {
        unit.value for unit in QuantityUnit
    }
    assert profile_schema["properties"]["basis"]["const"] == "per_100g"
    assert nutrient_schema["type"] == "object"
    assert set(nutrient_schema["required"]) == {
        "energy_kcal",
        "protein_g",
        "fat_g",
        "carbohydrate_g",
    }
    assert nutrient_schema["propertyNames"]["pattern"].startswith("^")
    assert "15%" in nutrient_schema["description"]


def test_rounded_payload_has_one_exact_json_wire_type() -> None:
    snapshot = NutrientSnapshot(
        grams=Decimal("1.005"),
        nutrients=NutrientValues.model_validate(
            {
                "energy_kcal": "1.705",
                "protein_g": "0.105",
                "fat_g": "0.105",
                "carbohydrate_g": "0.105",
            }
        ),
    )

    encoded = jsonable_encoder(snapshot.rounded_for_api(2))

    assert encoded["grams"] == "1.01"
    assert encoded["nutrients"]["energy_kcal"] == "1.71"
