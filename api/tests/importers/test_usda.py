from __future__ import annotations

import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from shutil import copytree
from zipfile import ZipFile

import opennosh_api.importers.usda as usda_importer
import pytest
from opennosh_api.importers.usda import USDADataType, USDAFormatError, iter_usda

FIXTURES = Path(__file__).parents[1] / "fixtures" / "usda"
REPOSITORY_ROOT = Path(__file__).parents[3]


def test_json_parser_keeps_provenance_and_reports_bad_records() -> None:
    outcomes = list(iter_usda(FIXTURES / "foundation.json"))
    records = [outcome.record for outcome in outcomes if outcome.record is not None]
    issues = [outcome.issue for outcome in outcomes if outcome.issue is not None]

    assert len(records) == 1
    assert len(issues) == 2
    record = records[0]
    assert record.fdc_id == "321358"
    assert record.source == "usda"
    assert record.license == "CC0"
    assert record.updated_at.isoformat() == "2019-04-01T00:00:00+00:00"
    assert record.nutrients_json["basis"] == "per_100g"
    assert record.nutrients_json["nutrients"]["energy_kcal"] == "229"
    assert record.nutrients_json["nutrients"]["sodium_mg"] == "438"
    assert record.nutrients_json["nutrients"]["vitamin_d_iu"] == "40"
    assert record.nutrients_json["nutrients"]["vitamin_d_mcg"] == "1"
    assert "usda_1024_sp gr" not in record.nutrients_json["nutrients"]
    assert record.portions_json == [{"name": "2 tbsp", "grams": "33.9"}]
    assert issues[0].fdc_id is None
    assert "FDC ID" in issues[0].message
    assert issues[1].fdc_id == "999999"
    assert "Missing required nutrients" in issues[1].message


def test_csv_parser_filters_to_foundation_and_sr_legacy() -> None:
    outcomes = list(iter_usda(FIXTURES / "csv"))
    records = [outcome.record for outcome in outcomes if outcome.record is not None]
    issues = [outcome.issue for outcome in outcomes if outcome.issue is not None]

    assert [record.fdc_id for record in records] == ["170001", "170002"]
    assert all(record.source == "usda" and record.license == "CC0" for record in records)
    assert records[0].food_category == "Cereal Grains and Pasta"
    assert records[0].portions_json == [{"name": "1 cup", "grams": "81"}]
    assert records[1].portions_json == [{"name": "1 serving", "grams": "195"}]
    assert len(issues) == 1
    assert issues[0].fdc_id == "170003"


def test_csv_zip_uses_the_same_offline_parser(tmp_path: Path) -> None:
    archive_path = tmp_path / "foundation-and-sr.zip"
    with ZipFile(archive_path, "w") as archive:
        for fixture in (FIXTURES / "csv").iterdir():
            archive.write(fixture, f"nested/{fixture.name}")

    outcomes = list(iter_usda(archive_path))

    assert sum(outcome.record is not None for outcome in outcomes) == 2
    assert sum(outcome.issue is not None for outcome in outcomes) == 1


def test_json_zip_uses_the_streaming_json_parser(tmp_path: Path) -> None:
    archive_path = tmp_path / "foundation.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.write(FIXTURES / "foundation.json", "nested/foundation.json")

    outcomes = list(iter_usda(archive_path))

    assert sum(outcome.record is not None for outcome in outcomes) == 1
    assert sum(outcome.issue is not None for outcome in outcomes) == 2


def test_empty_json_dataset_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "empty.json"
    source.write_text(json.dumps({"FoundationFoods": []}))

    with pytest.raises(USDAFormatError, match="contains no records"):
        list(iter_usda(source))


def test_ambiguous_json_zip_is_rejected(tmp_path: Path) -> None:
    archive_path = tmp_path / "ambiguous.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("foundation.json", '{"FoundationFoods": []}')
        archive.writestr("sr.json", '{"SRLegacyFoods": []}')

    with pytest.raises(USDAFormatError, match="more than one JSON file"):
        list(iter_usda(archive_path))


def test_duplicate_zip_basename_is_rejected(tmp_path: Path) -> None:
    archive_path = tmp_path / "duplicate-name.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("one/foundation.json", '{"FoundationFoods": []}')
        archive.writestr("two/foundation.json", '{"FoundationFoods": []}')

    with pytest.raises(USDAFormatError, match="duplicate archive filename"):
        list(iter_usda(archive_path))


def test_zip_member_over_decompressed_limit_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_path = tmp_path / "oversized.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("foundation.json", b"x" * 33)
    monkeypatch.setattr(usda_importer, "_MAX_INPUT_BYTES", 32)

    with pytest.raises(USDAFormatError, match="input limit"):
        list(iter_usda(archive_path))


def test_non_seed_dataset_is_rejected(tmp_path: Path) -> None:
    branded = tmp_path / "branded.json"
    branded.write_text(json.dumps({"BrandedFoods": []}))

    with pytest.raises(USDAFormatError, match="allowed USDA datasets"):
        list(iter_usda(branded))


def test_missing_csv_tables_are_reported(tmp_path: Path) -> None:
    (tmp_path / "food.csv").write_text(
        '"fdc_id","data_type","description","food_category_id","publication_date"\n'
    )

    with pytest.raises(USDAFormatError, match="missing required CSV files"):
        list(iter_usda(tmp_path))


def test_wrong_common_nutrient_unit_rejects_the_record(tmp_path: Path) -> None:
    payload = json.loads((FIXTURES / "foundation.json").read_text())
    payload["FoundationFoods"] = [deepcopy(payload["FoundationFoods"][0])]
    payload["FoundationFoods"][0]["foodNutrients"][0]["nutrient"]["unitName"] = "mg"
    source = tmp_path / "wrong-unit.json"
    source.write_text(json.dumps(payload))

    outcome = next(iter_usda(source))

    assert outcome.issue is not None
    assert "expected unit 'g', got 'mg'" in outcome.issue.message


def test_direct_energy_takes_priority_over_atwater_values(tmp_path: Path) -> None:
    payload = json.loads((FIXTURES / "foundation.json").read_text())
    valid = deepcopy(payload["FoundationFoods"][0])
    valid["foodNutrients"].append(
        {
            "nutrient": {"id": 1008, "name": "Energy", "unitName": "kcal"},
            "amount": 228,
        }
    )
    payload["FoundationFoods"] = [valid]
    source = tmp_path / "direct-energy.json"
    source.write_text(json.dumps(payload))

    outcome = next(iter_usda(source))

    assert outcome.record is not None
    assert outcome.record.nutrients_json["nutrients"]["energy_kcal"] == "228"


def test_conflicting_duplicate_energy_is_reported(tmp_path: Path) -> None:
    payload = json.loads((FIXTURES / "foundation.json").read_text())
    valid = deepcopy(payload["FoundationFoods"][0])
    valid["foodNutrients"].extend(
        [
            {
                "nutrient": {"id": 1008, "name": "Energy", "unitName": "kcal"},
                "amount": 228,
            },
            {
                "nutrient": {"id": 1008, "name": "Energy", "unitName": "kcal"},
                "amount": 229,
            },
        ]
    )
    payload["FoundationFoods"] = [valid]
    source = tmp_path / "conflicting-energy.json"
    source.write_text(json.dumps(payload))

    outcome = next(iter_usda(source))

    assert outcome.issue is not None
    assert "duplicate energy nutrient 1008" in outcome.issue.message


def test_unknown_supported_nutrient_is_retained_and_null_amount_is_skipped(
    tmp_path: Path,
) -> None:
    payload = json.loads((FIXTURES / "foundation.json").read_text())
    valid = deepcopy(payload["FoundationFoods"][0])
    valid["foodNutrients"].extend(
        [
            {
                "nutrient": {"id": 9991, "name": "Future nutrient", "unitName": "mg"},
                "amount": 0.5,
            },
            {
                "nutrient": {"id": 9992, "name": "Unavailable", "unitName": "mystery"},
                "amount": None,
            },
        ]
    )
    payload["FoundationFoods"] = [valid]
    source = tmp_path / "unknown-nutrient.json"
    source.write_text(json.dumps(payload))

    outcome = next(iter_usda(source))

    assert outcome.record is not None
    assert outcome.record.nutrients_json["nutrients"]["usda_9991_mg"] == "0.5"


def test_duplicate_json_fdc_id_is_reported(tmp_path: Path) -> None:
    payload = json.loads((FIXTURES / "foundation.json").read_text())
    valid = deepcopy(payload["FoundationFoods"][0])
    payload["FoundationFoods"] = [valid, deepcopy(valid)]
    source = tmp_path / "duplicate.json"
    source.write_text(json.dumps(payload))

    outcomes = list(iter_usda(source, allowed_data_types=[USDADataType.FOUNDATION]))

    assert outcomes[0].record is not None
    assert outcomes[1].issue is not None
    assert outcomes[1].issue.message == "duplicate FDC ID 321358"


def test_conflicting_duplicate_portion_is_reported(tmp_path: Path) -> None:
    payload = json.loads((FIXTURES / "foundation.json").read_text())
    valid = deepcopy(payload["FoundationFoods"][0])
    conflicting = deepcopy(valid["foodPortions"][0])
    conflicting["gramWeight"] = 99
    valid["foodPortions"].append(conflicting)
    payload["FoundationFoods"] = [valid]
    source = tmp_path / "duplicate-portion.json"
    source.write_text(json.dumps(payload))

    outcome = next(iter_usda(source))

    assert outcome.issue is not None
    assert "conflicting gram weights" in outcome.issue.message


def test_identical_duplicate_portion_is_deduplicated(tmp_path: Path) -> None:
    payload = json.loads((FIXTURES / "foundation.json").read_text())
    valid = deepcopy(payload["FoundationFoods"][0])
    valid["foodPortions"].append(deepcopy(valid["foodPortions"][0]))
    payload["FoundationFoods"] = [valid]
    source = tmp_path / "identical-portion.json"
    source.write_text(json.dumps(payload))

    outcome = next(iter_usda(source))

    assert outcome.record is not None
    assert outcome.record.portions_json == [{"name": "2 tbsp", "grams": "33.9"}]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("publicationDate", "not-a-date", "publication date"),
        ("description", "", "description must not be empty"),
        ("dataType", "SR Legacy", "does not match"),
    ],
)
def test_invalid_food_metadata_is_reported(
    tmp_path: Path, field: str, value: str, message: str
) -> None:
    payload = json.loads((FIXTURES / "foundation.json").read_text())
    valid = deepcopy(payload["FoundationFoods"][0])
    valid[field] = value
    payload["FoundationFoods"] = [valid]
    source = tmp_path / f"invalid-{field}.json"
    source.write_text(json.dumps(payload))

    outcome = next(iter_usda(source))

    assert outcome.issue is not None
    assert message in outcome.issue.message


def test_duplicate_csv_food_is_reported(tmp_path: Path) -> None:
    csv_dir = copytree(FIXTURES / "csv", tmp_path / "csv")
    food_path = csv_dir / "food.csv"
    duplicate = '"170001","foundation_food","Duplicate oats","20","2026-04-30"\n'
    food_path.write_text(food_path.read_text() + duplicate)

    outcomes = list(iter_usda(csv_dir))
    issues = [outcome.issue for outcome in outcomes if outcome.issue is not None]

    assert any(issue.message == "duplicate FDC ID 170001" for issue in issues)


def test_malformed_json_is_reported_as_a_format_error(tmp_path: Path) -> None:
    source = tmp_path / "truncated.json"
    source.write_text('{"FoundationFoods": [{"fdcId": 1}')

    with pytest.raises(USDAFormatError, match="malformed JSON"):
        list(iter_usda(source))


def test_module_cli_help_loads_without_runpy_warning() -> None:
    environment = {**os.environ, "PYTHONPATH": str(REPOSITORY_ROOT / "api")}

    result = subprocess.run(
        [sys.executable, "-W", "error", "-m", "opennosh_api.importers.usda", "--help"],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "RuntimeWarning" not in result.stderr
