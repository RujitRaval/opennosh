from decimal import Decimal

import pytest
from opennosh_api.main import create_app
from opennosh_api.recipes.schemas import RecipeFoodReference, RecipeFoodSource, RecipeWrite


def _payload() -> dict[str, object]:
    return {
        "name": "  Sunday dal  ",
        "yield_grams": "1400",
        "ingredients": [
            {
                "food": {"source": "community", "source_id": "dal-rice"},
                "grams": "300.125",
            }
        ],
    }


def test_recipe_payload_normalizes_name_and_preserves_exact_mass() -> None:
    payload = RecipeWrite.model_validate(_payload())

    assert payload.name == "Sunday dal"
    assert payload.yield_grams == Decimal("1400")
    assert payload.ingredients[0].grams == Decimal("300.125")


def test_recipe_response_preserves_sub_milligram_yield_and_ingredient_mass() -> None:
    payload = RecipeWrite.model_validate(
        {
            **_payload(),
            "yield_grams": "0.0001",
            "ingredients": [
                {
                    "food": {"source": "community", "source_id": "dal-rice"},
                    "grams": "0.0001",
                }
            ],
        }
    )

    assert format(payload.yield_grams, "f") == "0.0001"
    assert format(payload.ingredients[0].grams, "f") == "0.0001"


def test_recipe_payload_rejects_publication_recursion_and_invalid_values() -> None:
    invalid_payloads = (
        {**_payload(), "is_public": True},
        {**_payload(), "yield_grams": "0"},
        {**_payload(), "ingredients": []},
        {**_payload(), "name": "bad\nname"},
        {
            **_payload(),
            "ingredients": [
                {
                    "food": {"source": "recipe", "source_id": "not-allowed"},
                    "grams": "1",
                }
            ],
        },
    )
    for payload in invalid_payloads:
        with pytest.raises(ValueError):
            RecipeWrite.model_validate(payload)


@pytest.mark.parametrize(
    ("source", "source_id"),
    [
        (RecipeFoodSource.USDA, "abc"),
        (RecipeFoodSource.COMMUNITY, "Not-A-Slug"),
        (RecipeFoodSource.OPEN_FOOD_FACTS, "123x"),
        (RecipeFoodSource.CUSTOM, "not-a-uuid"),
    ],
)
def test_recipe_food_reference_rejects_source_incompatible_identifiers(
    source: RecipeFoodSource, source_id: str
) -> None:
    with pytest.raises(ValueError):
        RecipeFoodReference(source=source, source_id=source_id)


def test_openapi_registers_private_recipe_crud() -> None:
    paths = create_app().openapi()["paths"]

    assert {"get", "post"}.issubset(paths["/api/v1/recipes"])
    assert {"get", "put", "delete"}.issubset(paths["/api/v1/recipes/{recipe_id}"])
