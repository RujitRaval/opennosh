from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

import scripts.build_starter_food_packs as generator
from scripts.build_starter_food_packs import (
    Component,
    Dataset,
    Recipe,
    SourceRef,
    assert_quality,
    build,
    category_for,
    choose_portion,
    direct_entry,
    load_records,
    nutrient_map,
    pack_readme,
    portion_name,
    recipe_entry,
    render_files,
    rounded_nutrients,
    slugify,
    write_or_check,
)


def record(
    fdc_id: int = 1,
    description: str = "Plain food",
    *,
    portions: list[dict[str, Any]] | None = None,
    energy: str = "90",
    protein: str = "10",
    fat: str = "2",
    carbohydrate: str = "8",
) -> dict[str, Any]:
    nutrients = (
        (1008, energy),
        (1003, protein),
        (1004, fat),
        (1005, carbohydrate),
        (1079, "3.456"),
        (1093, "12.345"),
        (1258, "0.444"),
        (2000, "1.555"),
        (9999, "99"),
    )
    return {
        "fdcId": fdc_id,
        "description": description,
        "foodNutrients": [
            {"nutrient": {"id": nutrient_id}, "amount": amount} for nutrient_id, amount in nutrients
        ]
        + [{"nutrient": {"id": 1003}, "amount": None}],
        "foodPortions": portions or [],
    }


def quality_entry(
    slug: str,
    *,
    energy: str = "90",
    protein: str = "10",
    fat: str = "2",
    carbohydrate: str = "8",
) -> dict[str, Any]:
    return {
        "slug": slug,
        "nutrients": {
            "energy_kcal": energy,
            "protein_g": protein,
            "fat_g": fat,
            "carbohydrate_g": carbohydrate,
        },
    }


def test_load_records_success_and_missing_ids(tmp_path: Path) -> None:
    dataset = Dataset("test", "Test foods", "2026-01-01", "SurveyFoods")
    path = tmp_path / "foods.json"
    path.write_text(
        json.dumps({"SurveyFoods": [{"fdcId": 1}, {"fdcId": 2}, {"fdcId": 99}]}),
        encoding="utf-8",
    )

    assert set(load_records(path, dataset, {1, 2})) == {1, 2}
    with pytest.raises(ValueError, match=r"missing curated FDC IDs: \[3\]"):
        load_records(path, dataset, {1, 3})


def test_nutrient_extraction_and_rounding() -> None:
    source = record()

    assert set(nutrient_map(source)) == {
        "energy_kcal",
        "protein_g",
        "fat_g",
        "carbohydrate_g",
        "fiber_g",
        "sodium_mg",
        "saturated_fat_g",
        "sugars_g",
    }
    assert set(nutrient_map(source, include_extra=False)) == {
        "energy_kcal",
        "protein_g",
        "fat_g",
        "carbohydrate_g",
    }
    assert rounded_nutrients(
        {
            "energy_kcal": Decimal("90.04"),
            "protein_g": Decimal("2.345"),
            "fat_g": Decimal("2.000"),
        }
    ) == {"energy_kcal": 90, "protein_g": 2.34, "fat_g": 2}

    source["foodNutrients"] = [
        item for item in source["foodNutrients"] if item.get("nutrient", {}).get("id") != 1003
    ]
    with pytest.raises(ValueError, match="protein_g"):
        nutrient_map(source)


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("black bean", "legume"),
        ("sweet barfi", "sweet"),
        ("paneer cheese", "dairy"),
        ("plain tofu", "legume"),
        ("wheat naan", "grain"),
        ("whey powder", "powder"),
        ("almond seed", "nuts_seeds"),
        ("vegetable curry", "prepared_dish"),
        ("spinach", "vegetable"),
    ],
)
def test_slug_and_category_classification(description: str, expected: str) -> None:
    assert category_for(description) == expected
    assert slugify("Paneer & Rice!") == "paneer-rice"


def test_portion_name_prefers_description_and_handles_invalid_amount() -> None:
    assert (
        portion_name(
            {
                "portionDescription": "1 household bowl",
                "amount": 10,
                "measureUnit": {"name": "cup"},
            }
        )
        == "1 household bowl"
    )
    assert (
        portion_name(
            {"amount": "not-a-number", "measureUnit": {"name": "piece"}, "modifier": "small"}
        )
        == "not-a-number piece small"
    )


def test_choose_portion_rejects_bad_candidates_and_ranks_household_units() -> None:
    portions = [
        {"portionDescription": "quantity not specified", "gramWeight": 10},
        {"portionDescription": "missing"},
        {"portionDescription": "zero", "gramWeight": 0},
        {"portionDescription": "huge", "gramWeight": 1001},
        {"portionDescription": "serving", "gramWeight": 20},
        {"portionDescription": "1 piece", "gramWeight": 30},
        {"portionDescription": "1 cup", "gramWeight": "113.5"},
    ]

    selected, from_source = choose_portion(record(portions=portions), "gujarati-home-cooking")

    assert selected == {"name": "1 cup", "grams": 113.5}
    assert from_source is True


def test_choose_portion_uses_weight_then_name_as_tiebreakers() -> None:
    selected, from_source = choose_portion(
        record(
            portions=[
                {"portionDescription": "z serving", "gramWeight": 30},
                {"portionDescription": "a serving", "gramWeight": 20},
            ]
        ),
        "gujarati-home-cooking",
    )

    assert selected == {"name": "a serving", "grams": 20}
    assert from_source is True


@pytest.mark.parametrize(
    ("description", "pack_id", "expected"),
    [
        ("Protein mix", "supplements-and-powders", {"name": "30 g weighed scoop", "grams": 30}),
        ("Whole egg", "common-vegetarian-proteins", {"name": "1 large egg", "grams": 50}),
        ("Paneer", "common-vegetarian-proteins", {"name": "1/2 cup", "grams": 113}),
        ("Tofu", "common-vegetarian-proteins", {"name": "3 oz portion", "grams": 85}),
        ("Black bean", "common-vegetarian-proteins", {"name": "1/2 cup", "grams": 90}),
        ("Almond nut", "common-vegetarian-proteins", {"name": "1 oz portion", "grams": 28}),
        ("Gluten flour", "common-vegetarian-proteins", {"name": "1/4 cup", "grams": 30}),
        ("Spinach", "gujarati-home-cooking", {"name": "100 g weighed portion", "grams": 100}),
    ],
)
def test_choose_portion_fallbacks(
    description: str, pack_id: str, expected: dict[str, int | str]
) -> None:
    selected, from_source = choose_portion(record(description=description), pack_id)

    assert selected == expected
    assert from_source is False


@pytest.mark.parametrize(
    ("pack_id", "description", "slug_prefix", "category"),
    [
        ("gujarati-home-cooking", "Paneer rice", "gujarati-", "dairy"),
        ("indian-staples-north", "Chicken curry", "north-indian-", "prepared_dish"),
        (
            "common-vegetarian-proteins",
            "Plain tofu",
            "vegetarian-protein-",
            "protein",
        ),
        ("supplements-and-powders", "Whey powder", "supplement-", "supplement"),
    ],
)
def test_direct_entry_pack_branches(
    pack_id: str,
    description: str,
    slug_prefix: str,
    category: str,
) -> None:
    source = SourceRef(generator.FNDDS, 123)
    entry = direct_entry(
        pack_id,
        source,
        record(
            123,
            description,
            portions=[{"portionDescription": "1 cup", "gramWeight": 100}],
        ),
    )

    assert entry["slug"].startswith(slug_prefix)
    assert entry["category"] == category
    assert entry["source_uri"].endswith("/123/nutrients")
    assert entry["source_license"] == "CC0-1.0"
    assert entry["contributed_by"] == generator.CONTRIBUTOR
    assert entry["provenance"] == "government_database"
    assert "household portion weight is also from that record" in entry["source_note"]
    assert ("vegetarian" in entry["tags"]) is not (
        pack_id == "indian-staples-north" and "chicken" in description.lower()
    )


def test_direct_entry_discloses_estimated_portion() -> None:
    entry = direct_entry(
        "supplements-and-powders",
        SourceRef(generator.SR_LEGACY, 321),
        record(321, "Protein powder"),
    )

    assert entry["portions"] == [{"name": "30 g weighed scoop", "grams": 30}]
    assert "logging estimate, not a USDA measure" in entry["source_note"]


def test_recipe_entry_calculates_batch_and_yield(monkeypatch: pytest.MonkeyPatch) -> None:
    ingredient_ids = tuple(generator.INGREDIENTS)[:2]
    monkeypatch.setitem(generator.INGREDIENTS, ingredient_ids[0], "first ingredient")
    monkeypatch.setitem(generator.INGREDIENTS, ingredient_ids[1], "second ingredient")
    recipe = Recipe(
        slug="test-recipe",
        name="Test recipe",
        category="prepared_dish",
        components=(
            Component(ingredient_ids[0], Decimal(100)),
            Component(ingredient_ids[1], Decimal(50)),
        ),
        yield_grams=Decimal(300),
        portion_name="1 bowl",
        portion_grams=Decimal(150),
    )
    profiles = {
        ingredient_id: record(
            ingredient_id,
            energy="10",
            protein="10",
            fat="10",
            carbohydrate="10",
        )
        for ingredient_id in ingredient_ids
    }

    entry = recipe_entry(recipe, profiles)

    assert entry["nutrients"] == {
        "energy_kcal": 5,
        "protein_g": 5,
        "fat_g": 5,
        "carbohydrate_g": 5,
    }
    assert entry["portions"] == [{"name": "1 bowl", "grams": 150}]
    for ingredient_id in ingredient_ids:
        assert f"FDC {ingredient_id}" in entry["source_note"]
    assert "Cooked yield: 300 g" in entry["source_note"]
    assert "actual home recipes vary" in entry["source_note"]


def test_assert_quality_accepts_valid_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(generator, "PACKS", {"tiny": {"count": 1}})

    assert_quality({"tiny": [quality_entry("valid")]})


@pytest.mark.parametrize(
    ("packs", "definitions", "message"),
    [
        ({"tiny": []}, {"tiny": {"count": 1}}, "has 0 entries; expected 1"),
        (
            {"one": [quality_entry("same")], "two": [quality_entry("same")]},
            {"one": {"count": 1}, "two": {"count": 1}},
            "duplicate slug: same",
        ),
        (
            {"tiny": [quality_entry("mismatch", energy="20")]},
            {"tiny": {"count": 1}},
            "macro energy",
        ),
        (
            {"tiny": [quality_entry("zero", energy="0", protein="1", fat="0", carbohydrate="0")]},
            {"tiny": {"count": 1}},
            "macro energy",
        ),
        (
            {"tiny": [quality_entry("first"), quality_entry("second")]},
            {"tiny": {"count": 2}},
            "nutritionally near-duplicate",
        ),
    ],
)
def test_assert_quality_rejects_bad_packs(
    monkeypatch: pytest.MonkeyPatch,
    packs: dict[str, list[dict[str, Any]]],
    definitions: dict[str, dict[str, int]],
    message: str,
) -> None:
    monkeypatch.setattr(generator, "PACKS", definitions)

    with pytest.raises(ValueError, match=message):
        assert_quality(packs)


def test_pack_readme_and_render_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        generator,
        "PACKS",
        {
            "tiny": {
                "count": 1,
                "name": "Tiny pack",
                "description": "A tiny pack.",
                "locale": "en-US",
            }
        },
    )
    entries = [quality_entry("valid")]
    rendered = render_files(tmp_path, {"tiny": entries}, {"tiny": (1, 0)})

    assert set(rendered) == {
        tmp_path / "tiny" / "pack.yaml",
        tmp_path / "tiny" / "foods" / "foods.yaml",
        tmp_path / "tiny" / "README.md",
    }
    assert "entry_count: 1" in rendered[tmp_path / "tiny" / "pack.yaml"]
    assert "locale: en-US" in rendered[tmp_path / "tiny" / "pack.yaml"]
    assert "1 entries reproduce USDA" in rendered[tmp_path / "tiny" / "README.md"]
    assert "No entries directly reproduce" in pack_readme("tiny", 0, 1)
    assert "1 entries are contributor-authored" in pack_readme("tiny", 1, 1)


def test_write_or_check_round_trip_and_drift(tmp_path: Path) -> None:
    first = tmp_path / "one.txt"
    second = tmp_path / "nested" / "two.txt"
    rendered = {first: "one\n", second: "two\n"}

    write_or_check(rendered, check=False)
    assert first.read_text(encoding="utf-8") == "one\n"
    assert second.read_text(encoding="utf-8") == "two\n"
    write_or_check(rendered, check=True)

    first.write_text("changed\n", encoding="utf-8")
    second.unlink()
    with pytest.raises(SystemExit, match="Generated starter packs are stale") as error:
        write_or_check(rendered, check=True)
    assert str(first) in str(error.value)
    assert str(second) in str(error.value)


def test_build_assembles_exact_pack_composition(monkeypatch: pytest.MonkeyPatch) -> None:
    fndds_ids = set(generator.GUJARATI_DIRECT_IDS) | set(generator.NORTH_DIRECT_IDS) | {2705740}
    sr_ids = set(generator.PROTEIN_SR_IDS) | set(generator.SUPPLEMENT_SR_IDS)
    fndds_records = {fdc_id: {"fdcId": fdc_id} for fdc_id in fndds_ids}
    sr_records = {fdc_id: {"fdcId": fdc_id} for fdc_id in sr_ids}
    quality_calls: list[dict[str, list[dict[str, Any]]]] = []

    def fake_direct(
        pack_id: str, source: SourceRef, source_record: dict[str, Any]
    ) -> dict[str, Any]:
        return {"slug": f"{pack_id}-{source.fdc_id}", "source_record": source_record}

    def fake_recipe(recipe: Recipe, source_records: dict[int, dict[str, Any]]) -> dict[str, Any]:
        return {"slug": f"recipe-{recipe.slug}", "source_records": source_records}

    monkeypatch.setattr(generator, "direct_entry", fake_direct)
    monkeypatch.setattr(generator, "recipe_entry", fake_recipe)
    monkeypatch.setattr(generator, "assert_quality", quality_calls.append)

    packs, counts = build(fndds_records, sr_records)

    assert {pack_id: len(entries) for pack_id, entries in packs.items()} == {
        "gujarati-home-cooking": 50,
        "indian-staples-north": 60,
        "common-vegetarian-proteins": 30,
        "supplements-and-powders": 25,
    }
    assert counts == {
        "gujarati-home-cooking": (30, 20),
        "indian-staples-north": (60, 0),
        "common-vegetarian-proteins": (29, 1),
        "supplements-and-powders": (25, 0),
    }
    assert packs["common-vegetarian-proteins"][-1]["slug"] == (
        f"recipe-{generator.SEITAN_RECIPE.slug}"
    )
    assert quality_calls == [packs]
