from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import yaml
from opennosh_api.foodpacks.validation import validate_pack_directories

from scripts.build_starter_food_packs import portion_name

ROOT = Path(__file__).resolve().parents[3]
PACK_ROOT = ROOT / "packs"
FOUNDATIONAL_COUNTS = {
    "gujarati-home-cooking": 50,
    "indian-staples-north": 60,
    "common-vegetarian-proteins": 30,
    "supplements-and-powders": 25,
}
PROHIBITED_BRAND_MARKERS = (
    "abbott",
    "chobani",
    "dannon",
    "eas whey",
    "house foods",
    "mori-nu",
    "nasoya",
    "vitasoy",
)


def load_pack(pack_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    directory = PACK_ROOT / pack_id
    manifest = yaml.safe_load((directory / "pack.yaml").read_text(encoding="utf-8"))
    foods: list[dict[str, Any]] = []
    for path in sorted((directory / "foods").glob("*.yaml")):
        foods.extend(yaml.safe_load(path.read_text(encoding="utf-8")))
    return manifest, foods


def all_entries() -> list[dict[str, Any]]:
    return [entry for pack_id in FOUNDATIONAL_COUNTS for entry in load_pack(pack_id)[1]]


def pack_directories() -> tuple[Path, ...]:
    return tuple(
        path
        for path in sorted(PACK_ROOT.iterdir())
        if path.is_dir() and (path / "pack.yaml").is_file()
    )


def test_usda_portion_amount_formatting_preserves_integer_zeroes() -> None:
    assert (
        portion_name({"amount": 10, "measureUnit": {"name": "tablespoon"}, "modifier": ""})
        == "10 tablespoon"
    )
    assert (
        portion_name({"amount": "1.0", "measureUnit": {"name": "cup"}, "modifier": ""}) == "1 cup"
    )


def test_foundational_starter_packs_have_the_promised_165_entries() -> None:
    actual_directories = {path.name for path in pack_directories()}

    assert set(FOUNDATIONAL_COUNTS) <= actual_directories
    for pack_id, expected in FOUNDATIONAL_COUNTS.items():
        manifest, foods = load_pack(pack_id)
        assert manifest["id"] == pack_id
        assert manifest["entry_count"] == expected
        assert len(foods) == expected
    assert sum(FOUNDATIONAL_COUNTS.values()) == 165


def test_starter_packs_pass_runtime_validation_without_warnings() -> None:
    report = validate_pack_directories(pack_directories())

    assert report.valid
    assert report.errors == ()
    assert report.warnings == ()


def test_every_entry_has_auditable_provenance_and_visible_credit() -> None:
    entries = all_entries()
    provenance = Counter(entry["provenance"] for entry in entries)

    assert provenance == {
        "government_database": 144,
        "published_recipe_calculation": 21,
    }
    for entry in entries:
        assert entry["contributed_by"] == "RujitRaval"
        note = entry["source_note"]
        if entry["provenance"] == "government_database":
            assert entry["source_license"] == "CC0-1.0"
            assert entry["source_uri"].startswith("https://fdc.nal.usda.gov/")
            assert "bulk release" in note
            assert "FDC ID " in note
            assert "public-domain record" in note
        else:
            assert entry["source_license"] == "contributor-original"
            assert entry["source_uri"] is None
            assert "Batch formula:" in note
            assert "FDC " in note
            assert "Cooked yield:" in note
            assert "actual home recipes vary" in note


def test_portions_are_named_and_estimates_are_explicit() -> None:
    entries = all_entries()

    for entry in entries:
        assert entry["portions"]
        for portion in entry["portions"]:
            assert portion["name"].strip().lower() != "serving"
            assert portion["grams"] > 0
        if "logging estimate" in entry["source_note"]:
            assert any(
                unit in entry["portions"][0]["name"].lower()
                for unit in ("cup", "oz", "egg", "g weighed")
            )

    gujarati = load_pack("gujarati-home-cooking")[1]
    assert all("weighed portion" not in entry["portions"][0]["name"].lower() for entry in gujarati)
    assert {"1 katori", "1 thepla", "1 chilla"} <= {
        entry["portions"][0]["name"] for entry in gujarati
    }


def test_packs_are_generic_unbranded_and_cover_the_promised_protein_examples() -> None:
    entries = all_entries()
    normalized_names = " ".join(entry["name"].lower() for entry in entries)

    assert not any(marker in normalized_names for marker in PROHIBITED_BRAND_MARKERS)
    proteins = " ".join(
        entry["name"].lower() for entry in load_pack("common-vegetarian-proteins")[1]
    )
    assert all(food in proteins for food in ("paneer", "tofu", "tempeh", "seitan"))
    north = load_pack("indian-staples-north")[1]
    assert any(entry["name"] == "Bread, naan" for entry in north)
    assert any(entry["name"] == "Palak Paneer" for entry in north)
