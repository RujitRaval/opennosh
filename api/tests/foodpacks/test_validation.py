from __future__ import annotations

import json
from copy import deepcopy
from decimal import Decimal, localcontext
from pathlib import Path

import opennosh_api.foodpacks.validation as validation
import pytest
from jsonschema import Draft202012Validator
from opennosh_api.foodpacks.validation import (
    DEFAULT_SCHEMA_PATH,
    MAX_FOOD_FILES,
    MAX_NUMERIC_BITS,
    MAX_PACK_BYTES,
    MAX_PACK_DIRECTORIES,
    MAX_PACK_ENTRIES,
    MAX_REPOSITORY_ENTRIES,
    MAX_RUNTIME_COLLECTION_ITEMS,
    MAX_RUNTIME_DEPTH,
    MAX_RUNTIME_NODES,
    MAX_RUNTIME_STRING_CHARS,
    MAX_YAML_FILE_BYTES,
    MAX_YAML_NESTING,
    SCHEMA_VERSION,
    FoodPackLoadError,
    LoadedFoodPack,
    _canonical_core,
    _load_yaml,
    _schema_validator,
    discover_pack_directories,
    load_pack_directory,
    main,
    validate_pack_directories,
    validate_pack_document,
    validate_pack_roots,
)

FIXTURES = Path(__file__).parent / "fixtures"
VALID_PACK = FIXTURES / "valid" / "balanced-pack"
HOSTILE_A = FIXTURES / "hostile" / "hostile-a"
HOSTILE_B = FIXTURES / "hostile" / "hostile-b"


def _valid_document() -> dict[str, object]:
    return deepcopy(load_pack_directory(VALID_PACK).document)


def _first_food(document: dict[str, object]) -> dict[str, object]:
    foods = document["foods"]
    assert isinstance(foods, list) and isinstance(foods[0], dict)
    return foods[0]


def _manifest(document: dict[str, object]) -> dict[str, object]:
    manifest = document["pack"]
    assert isinstance(manifest, dict)
    return manifest


def _make_loader_pack(tmp_path: Path, name: str = "test-pack") -> tuple[Path, Path]:
    pack = tmp_path / name
    foods = pack / "foods"
    foods.mkdir(parents=True)
    (pack / "pack.yaml").write_text("id: test-pack\n", encoding="utf-8")
    return pack, foods


def test_versioned_schema_is_valid_draft_2020_12() -> None:
    schema = json.loads(DEFAULT_SCHEMA_PATH.read_text(encoding="utf-8"))

    Draft202012Validator.check_schema(schema)

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["x-opennosh-schema-version"] == SCHEMA_VERSION
    assert schema["additionalProperties"] is False


def test_valid_directory_fixture_passes_without_warnings() -> None:
    report = validate_pack_directories([VALID_PACK])

    assert report.valid
    assert report.errors == ()
    assert report.warnings == ()


@pytest.mark.parametrize(
    ("target", "value", "path"),
    [
        ("version", "1.0.0+" + "a" * 64, ("pack", "version")),
        ("locale", "en-" + "-".join(["Latin"] * 8), ("pack", "locale")),
        (
            "source_uri",
            "https://example.gov/" + "a" * 2048,
            ("foods", 0, "source_uri"),
        ),
    ],
)
def test_storage_bounded_strings_are_rejected_before_database_write(
    target: str, value: str, path: tuple[str | int, ...]
) -> None:
    document = _valid_document()
    if target == "source_uri":
        entry = _first_food(document)
        entry["provenance"] = "government_database"
        entry["source_license"] = "public-domain"
        entry[target] = value
    else:
        _manifest(document)[target] = value

    report = validate_pack_document(document)

    assert any(issue.code == "schema.maxLength" and issue.path == path for issue in report.errors)


def test_hostile_fixtures_cover_every_documented_hard_failure() -> None:
    report = validate_pack_directories([HOSTILE_A, HOSTILE_B])
    error_codes = {issue.code for issue in report.errors}
    schema_validators = {
        code.removeprefix("schema.")
        for code in error_codes
        if code.startswith("schema.")
    }

    assert not report.valid
    assert {
        "required",  # missing name, density, and required structured source fields
        "enum",  # provenance and source-license allowlists
        "oneOf",  # invalid source URI
        "minimum",  # negative nutrient
        "exclusiveMinimum",  # zero-gram portion
        "const",  # pack license
    } <= schema_validators
    assert {
        "entry_count_mismatch",
        "slug_collision",
        "macro_energy_mismatch",
        "energy_too_high",
    } <= error_codes
    assert sum(issue.code == "slug_collision" for issue in report.errors) >= 2


def test_hostile_fixtures_cover_every_documented_warning() -> None:
    report = validate_pack_directories([HOSTILE_A])

    assert {
        "no_named_portions",
        "short_source_note",
        "near_duplicate_nutrients",
        "fiber_exceeds_carbohydrate",
    } <= {issue.code for issue in report.warnings}


@pytest.mark.parametrize(
    "field",
    [
        "slug",
        "name",
        "category",
        "contributed_by",
        "provenance",
        "source_uri",
        "source_license",
        "basis",
        "nutrients",
    ],
)
def test_each_required_food_field_has_independent_coverage(field: str) -> None:
    document = _valid_document()
    _first_food(document).pop(field)

    report = validate_pack_document(document)

    assert any(
        issue.code == "schema.required"
        and issue.path == ("foods", 0)
        and field in issue.message
        for issue in report.errors
    )


@pytest.mark.parametrize(
    "field",
    ["energy_kcal", "protein_g", "fat_g", "carbohydrate_g"],
)
def test_each_required_core_nutrient_has_independent_coverage(field: str) -> None:
    document = _valid_document()
    nutrients = _first_food(document)["nutrients"]
    assert isinstance(nutrients, dict)
    nutrients.pop(field)

    report = validate_pack_document(document)

    assert any(
        issue.code == "schema.required"
        and issue.path == ("foods", 0, "nutrients")
        and field in issue.message
        for issue in report.errors
    )


def test_basis_allowlist_has_independent_coverage() -> None:
    document = _valid_document()
    _first_food(document)["basis"] = "per_serving"

    report = validate_pack_document(document)

    assert any(
        issue.code == "schema.enum" and issue.path == ("foods", 0, "basis")
        for issue in report.errors
    )


@pytest.mark.parametrize(
    "field",
    ["id", "name", "description", "version", "locale", "license", "maintainers", "entry_count"],
)
def test_each_required_manifest_field_has_independent_coverage(field: str) -> None:
    document = _valid_document()
    _manifest(document).pop(field)

    report = validate_pack_document(document)

    assert any(
        issue.code == "schema.required"
        and issue.path == ("pack",)
        and field in issue.message
        for issue in report.errors
    )


@pytest.mark.parametrize(
    ("provenance", "source_license", "source_uri"),
    [
        ("lab_analysis", "contributor-original", None),
        ("government_database", "public-domain", "https://example.gov/food/1"),
        ("manufacturer_label", "CC0-1.0", "https://example.com/label/1"),
        ("published_recipe_calculation", "contributor-original", None),
        ("own_measurement", "contributor-original", None),
    ],
)
def test_every_allowed_provenance_and_license_relationship(
    provenance: str,
    source_license: str,
    source_uri: str | None,
) -> None:
    document = _valid_document()
    entry = _first_food(document)
    entry.update(
        provenance=provenance,
        source_license=source_license,
        source_uri=source_uri,
        source_note="A complete source note that explains the data collection method.",
    )

    assert validate_pack_document(document).valid


@pytest.mark.parametrize(
    ("case", "expected_path", "expected_code"),
    [
        ("bad_credit", ("foods", 0, "contributed_by"), "schema.pattern"),
        ("bad_provenance", ("foods", 0, "provenance"), "schema.enum"),
        ("bad_source_license", ("foods", 0, "source_license"), "schema.enum"),
        ("government_http", ("foods", 0, "source_uri"), "schema.oneOf"),
        ("government_hostless", ("foods", 0, "source_uri"), "schema.oneOf"),
        ("government_original", ("foods", 0, "source_license"), "schema.enum"),
        ("manufacturer_original", ("foods", 0, "provenance"), "schema.enum"),
        ("pack_license", ("pack", "license"), "schema.const"),
    ],
)
def test_each_allowlist_relationship_fails_at_its_own_path(
    case: str,
    expected_path: tuple[str | int, ...],
    expected_code: str,
) -> None:
    document = _valid_document()
    entry = _first_food(document)
    if case == "bad_credit":
        entry["contributed_by"] = "@not-a-github-user"
    elif case == "bad_provenance":
        entry["provenance"] = "internet"
    elif case == "bad_source_license":
        entry["source_license"] = "ODbL-1.0"
    elif case in {"government_http", "government_hostless", "government_original"}:
        entry.update(
            provenance="government_database",
            source_uri=(
                "http://example.gov/food"
                if case == "government_http"
                else "https:///missing-host"
            ),
            source_license=(
                "contributor-original"
                if case == "government_original"
                else "public-domain"
            ),
            source_note="Published by a government food composition database.",
        )
        if case == "government_original":
            entry["source_uri"] = "https://example.gov/food"
    elif case == "manufacturer_original":
        entry["provenance"] = "manufacturer_label"
    else:
        _manifest(document)["license"] = "ODbL-1.0"

    report = validate_pack_document(document)

    assert any(
        issue.path == expected_path and issue.code == expected_code
        for issue in report.errors
    )


@pytest.mark.parametrize(
    "provenance",
    ["government_database", "published_recipe_calculation", "own_measurement"],
)
def test_provenance_that_requires_a_note_is_covered(provenance: str) -> None:
    document = _valid_document()
    entry = _first_food(document)
    entry["provenance"] = provenance
    entry.pop("source_note")
    if provenance == "government_database":
        entry["source_uri"] = "https://example.gov/food"
        entry["source_license"] = "public-domain"

    report = validate_pack_document(document)

    assert any(
        issue.code == "schema.required"
        and issue.path == ("foods", 0)
        and "source_note" in issue.message
        for issue in report.errors
    )


def test_per_100ml_basis_independently_requires_density() -> None:
    document = _valid_document()
    entry = _first_food(document)
    entry["basis"] = "per_100ml"
    entry.pop("density_g_per_ml", None)

    report = validate_pack_document(document)

    assert any(
        issue.code == "schema.required"
        and issue.path == ("foods", 0)
        and "density_g_per_ml" in issue.message
        for issue in report.errors
    )


def test_warnings_are_machine_readable_and_do_not_block_runtime_validation() -> None:
    document = _valid_document()
    foods = document["foods"]
    assert isinstance(foods, list)
    entry = foods[0]
    assert isinstance(entry, dict)
    entry.pop("portions")
    entry["source_note"] = "brief"
    nutrients = entry["nutrients"]
    assert isinstance(nutrients, dict)
    nutrients["fiber_g"] = 43

    report = validate_pack_document(document)
    payload = report.to_dict()

    assert report.valid
    assert payload["valid"] is True
    assert payload["errors"] == []
    assert all(warning["severity"] == "warning" for warning in payload["warnings"])
    assert all(isinstance(warning["code"], str) for warning in payload["warnings"])
    assert all(str(warning["path"]).startswith("/") for warning in payload["warnings"])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("density_g_per_ml", float("nan")),
        ("density_g_per_ml", float("inf")),
    ],
)
def test_nonfinite_density_is_a_hard_failure(field: str, value: float) -> None:
    document = _valid_document()
    foods = document["foods"]
    assert isinstance(foods, list) and isinstance(foods[1], dict)
    foods[1][field] = value

    report = validate_pack_document(document)

    assert "non_finite_number" in {issue.code for issue in report.errors}


def test_nonfinite_nutrients_and_portions_are_hard_failures() -> None:
    document = _valid_document()
    foods = document["foods"]
    assert isinstance(foods, list) and isinstance(foods[0], dict)
    nutrients = foods[0]["nutrients"]
    portions = foods[0]["portions"]
    assert isinstance(nutrients, dict) and isinstance(portions, list)
    nutrients["sodium_mg"] = float("nan")
    assert isinstance(portions[0], dict)
    portions[0]["grams"] = float("inf")

    report = validate_pack_document(document)

    assert sum(issue.code == "non_finite_number" for issue in report.errors) == 2


def test_per_100ml_energy_limit_uses_canonical_per_100g_value() -> None:
    document = _valid_document()
    foods = document["foods"]
    assert isinstance(foods, list) and isinstance(foods[1], dict)
    nutrients = foods[1]["nutrients"]
    assert isinstance(nutrients, dict)
    foods[1]["density_g_per_ml"] = 1.25
    nutrients.update(
        energy_kcal=1125,
        protein_g=0,
        fat_g=125,
        carbohydrate_g=0,
    )

    assert validate_pack_document(document).valid

    foods[1]["density_g_per_ml"] = 1.24
    assert "energy_too_high" in {
        issue.code for issue in validate_pack_document(document).errors
    }


def test_macro_tolerance_accepts_exactly_15_percent_and_rejects_just_over() -> None:
    document = _valid_document()
    nutrients = _first_food(document)["nutrients"]
    assert isinstance(nutrients, dict)
    nutrients.update(energy_kcal=100, protein_g=21.25, fat_g=0, carbohydrate_g=0)

    assert "macro_energy_mismatch" not in {
        issue.code for issue in validate_pack_document(document).errors
    }

    nutrients["protein_g"] = 21.249
    assert "macro_energy_mismatch" in {
        issue.code for issue in validate_pack_document(document).errors
    }


def test_energy_boundary_accepts_900_and_rejects_any_value_above_it() -> None:
    document = _valid_document()
    nutrients = _first_food(document)["nutrients"]
    assert isinstance(nutrients, dict)
    nutrients.update(energy_kcal=900, protein_g=0, fat_g=100, carbohydrate_g=0)

    assert "energy_too_high" not in {
        issue.code for issue in validate_pack_document(document).errors
    }

    nutrients["energy_kcal"] = 900.01
    assert "energy_too_high" in {
        issue.code for issue in validate_pack_document(document).errors
    }


@pytest.mark.parametrize("density", [0.01, 5])
def test_density_inclusive_boundaries_are_accepted(density: float) -> None:
    document = _valid_document()
    entry = _first_food(document)
    entry["basis"] = "per_100ml"
    entry["density_g_per_ml"] = density
    entry["nutrients"] = {
        "energy_kcal": 0,
        "protein_g": 0,
        "fat_g": 0,
        "carbohydrate_g": 0,
    }

    assert validate_pack_document(document).valid


@pytest.mark.parametrize(
    ("density", "code"),
    [(0.009999, "schema.minimum"), (5.000001, "schema.maximum")],
)
def test_density_values_outside_inclusive_bounds_are_rejected(
    density: float, code: str
) -> None:
    document = _valid_document()
    entry = _first_food(document)
    entry["basis"] = "per_100ml"
    entry["density_g_per_ml"] = density

    assert any(
        issue.code == code and issue.path == ("foods", 0, "density_g_per_ml")
        for issue in validate_pack_document(document).errors
    )


def test_portion_grams_are_strictly_positive() -> None:
    document = _valid_document()
    portions = _first_food(document)["portions"]
    assert isinstance(portions, list) and isinstance(portions[0], dict)
    portions[0]["grams"] = 0.000001
    assert validate_pack_document(document).valid

    portions[0]["grams"] = 0
    assert any(
        issue.code == "schema.exclusiveMinimum"
        and issue.path == ("foods", 0, "portions", 0, "grams")
        for issue in validate_pack_document(document).errors
    )


def test_canonical_comparison_does_not_depend_on_process_decimal_precision() -> None:
    document = _valid_document()
    foods = document["foods"]
    assert isinstance(foods, list) and isinstance(foods[1], dict)

    with localcontext() as context:
        context.prec = 6
        low_precision = _canonical_core(foods[1])
    with localcontext() as context:
        context.prec = 60
        high_precision = _canonical_core(foods[1])

    assert low_precision == high_precision


def test_loader_rejects_duplicate_yaml_keys(tmp_path: Path) -> None:
    pack = tmp_path / "duplicate-keys"
    foods = pack / "foods"
    foods.mkdir(parents=True)
    (pack / "pack.yaml").write_text("id: first\nid: second\n", encoding="utf-8")
    (foods / "foods.yaml").write_text("[]\n", encoding="utf-8")

    with pytest.raises(FoodPackLoadError, match="duplicate key") as caught:
        load_pack_directory(pack)

    assert caught.value.code == "yaml_invalid"


@pytest.mark.parametrize(
    ("manifest", "food_text", "expected_code"),
    [
        (None, "[]\n", "manifest_missing"),
        ("[]\n", "[]\n", "manifest_not_mapping"),
        ("id: test-pack\n", None, "food_files_missing"),
        ("id: test-pack\n", "slug: not-a-list\n", "food_file_not_list"),
        ("id: [\n", "[]\n", "yaml_invalid"),
    ],
)
def test_each_loader_failure_returns_a_stable_code(
    tmp_path: Path,
    manifest: str | None,
    food_text: str | None,
    expected_code: str,
) -> None:
    pack = tmp_path / expected_code
    foods = pack / "foods"
    foods.mkdir(parents=True)
    if manifest is not None:
        (pack / "pack.yaml").write_text(manifest, encoding="utf-8")
    if food_text is not None:
        (foods / "foods.yaml").write_text(food_text, encoding="utf-8")

    with pytest.raises(FoodPackLoadError) as caught:
        load_pack_directory(pack)

    assert caught.value.code == expected_code


def test_unreadable_or_missing_yaml_has_a_stable_load_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"

    with pytest.raises(FoodPackLoadError) as caught:
        _load_yaml(missing)

    assert caught.value.code == "file_unreadable"
    assert caught.value.path == missing


def test_invalid_utf8_returns_stable_loader_and_cli_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pack, foods = _make_loader_pack(tmp_path, "invalid-encoding")
    (pack / "pack.yaml").write_bytes(b"id: invalid-\xff\n")
    (foods / "foods.yaml").write_text("[]\n", encoding="utf-8")

    with pytest.raises(FoodPackLoadError) as caught:
        load_pack_directory(pack)
    assert caught.value.code == "file_encoding_invalid"

    assert main([str(pack), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert [error["code"] for error in payload["errors"]] == [
        "file_encoding_invalid"
    ]


def test_hostile_numeric_strings_return_type_errors_without_decimal_overflow() -> None:
    document = _valid_document()
    nutrients = _first_food(document)["nutrients"]
    assert isinstance(nutrients, dict)
    for field in ("energy_kcal", "protein_g", "fat_g", "carbohydrate_g"):
        nutrients[field] = "1e1000000000"

    report = validate_pack_document(document)

    assert not report.valid
    assert sum(issue.code == "schema.type" for issue in report.errors) == 4


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("sNaN")])
def test_non_finite_decimal_returns_a_stable_error(value: Decimal) -> None:
    document = _valid_document()
    nutrients = _first_food(document)["nutrients"]
    assert isinstance(nutrients, dict)
    nutrients["protein_g"] = value

    report = validate_pack_document(document)

    assert [issue.code for issue in report.errors] == ["non_finite_number"]
    assert report.errors[0].path == ("foods", 0, "nutrients", "protein_g")


@pytest.mark.parametrize(
    "yaml_text",
    [
        "value: &shared 1\ncopy: *shared\n",
        "1: non-string-key\n",
        "[" * MAX_YAML_NESTING + "0" + "]" * MAX_YAML_NESTING,
    ],
)
def test_yaml_aliases_non_string_keys_and_excessive_nesting_are_rejected(
    tmp_path: Path, yaml_text: str
) -> None:
    path = tmp_path / "hostile.yaml"
    path.write_text(yaml_text, encoding="utf-8")

    with pytest.raises(FoodPackLoadError) as caught:
        _load_yaml(path)

    assert caught.value.code == "yaml_invalid"


def test_symbolic_linked_yaml_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source.yaml"
    link = tmp_path / "link.yaml"
    source.write_text("value: safe\n", encoding="utf-8")
    link.symlink_to(source)

    with pytest.raises(FoodPackLoadError) as caught:
        _load_yaml(link)

    assert caught.value.code == "symlink_not_allowed"


def test_exact_yaml_nesting_and_file_size_limits_are_accepted(tmp_path: Path) -> None:
    nested = tmp_path / "nested.yaml"
    nesting = MAX_YAML_NESTING - 1
    nested.write_text("[" * nesting + "0" + "]" * nesting, encoding="utf-8")
    assert _load_yaml(nested) is not None

    exact_size = tmp_path / "exact-size.yaml"
    exact_size.write_text("value" + " " * (MAX_YAML_FILE_BYTES - 5), encoding="utf-8")
    assert exact_size.stat().st_size == MAX_YAML_FILE_BYTES
    assert _load_yaml(exact_size) == "value"


def test_per_file_and_aggregate_byte_limits_block_oversized_packs(tmp_path: Path) -> None:
    oversized = tmp_path / "oversized.yaml"
    oversized.write_text(" " * (MAX_YAML_FILE_BYTES + 1), encoding="utf-8")
    with pytest.raises(FoodPackLoadError) as caught:
        _load_yaml(oversized)
    assert caught.value.code == "file_too_large"

    pack, foods = _make_loader_pack(tmp_path, "aggregate")
    file_count = MAX_PACK_BYTES // (MAX_YAML_FILE_BYTES - 1) + 1
    for index in range(file_count):
        (foods / f"{index}.yaml").write_text(
            " " * (MAX_YAML_FILE_BYTES - 1), encoding="utf-8"
        )
    with pytest.raises(FoodPackLoadError) as aggregate:
        load_pack_directory(pack)
    assert aggregate.value.code == "pack_too_large"


def test_exact_aggregate_byte_limit_is_accepted(tmp_path: Path) -> None:
    pack, foods = _make_loader_pack(tmp_path, "exact-aggregate")
    manifest_size = (pack / "pack.yaml").stat().st_size
    remaining = MAX_PACK_BYTES - manifest_size
    index = 0
    while remaining:
        size = min(remaining, MAX_YAML_FILE_BYTES)
        content = "[]\n" + " " * (size - 3)
        (foods / f"{index}.yaml").write_text(content, encoding="utf-8")
        remaining -= size
        index += 1

    assert sum(path.stat().st_size for path in pack.rglob("*.yaml")) == MAX_PACK_BYTES
    assert load_pack_directory(pack).document["foods"] == []


def test_file_and_entry_count_limits_stop_loading_early(tmp_path: Path) -> None:
    file_pack, file_directory = _make_loader_pack(tmp_path, "too-many-files")
    for index in range(MAX_FOOD_FILES + 1):
        (file_directory / f"{index}.yaml").write_text("[]\n", encoding="utf-8")
    with pytest.raises(FoodPackLoadError) as files_error:
        load_pack_directory(file_pack)
    assert files_error.value.code == "too_many_food_files"

    entry_pack, entry_directory = _make_loader_pack(tmp_path, "too-many-entries")
    (entry_directory / "foods.yaml").write_text(
        "- {}\n" * (MAX_PACK_ENTRIES + 1), encoding="utf-8"
    )
    with pytest.raises(FoodPackLoadError) as entries_error:
        load_pack_directory(entry_pack)
    assert entries_error.value.code == "too_many_entries"


def test_exact_file_and_entry_count_limits_are_accepted(tmp_path: Path) -> None:
    file_pack, file_directory = _make_loader_pack(tmp_path, "max-files")
    for index in range(MAX_FOOD_FILES):
        (file_directory / f"{index}.yaml").write_text("[]\n", encoding="utf-8")
    assert load_pack_directory(file_pack).document["foods"] == []

    entry_pack, entry_directory = _make_loader_pack(tmp_path, "max-entries")
    (entry_directory / "foods.yaml").write_text(
        "- {}\n" * MAX_PACK_ENTRIES, encoding="utf-8"
    )
    loaded = load_pack_directory(entry_pack)
    assert len(loaded.document["foods"]) == MAX_PACK_ENTRIES


def test_runtime_oversized_document_stops_before_quadratic_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _valid_document()
    entry = _first_food(document)
    document["foods"] = [deepcopy(entry) for _ in range(MAX_PACK_ENTRIES + 1)]

    def unexpected_cross_pack_scan(_documents: object) -> object:
        raise AssertionError("oversized documents must not reach cross-pack scanning")

    def unexpected_schema_scan(_document: object) -> object:
        raise AssertionError("oversized documents must not reach schema traversal")

    monkeypatch.setattr(validation, "_cross_pack_issues", unexpected_cross_pack_scan)
    monkeypatch.setattr(validation, "_schema_errors", unexpected_schema_scan)

    report = validate_pack_document(document)

    assert not report.valid
    assert {issue.code for issue in report.errors} == {"schema.maxItems"}
    assert report.errors[0].message == (
        f"foods cannot contain more than {MAX_PACK_ENTRIES} entries"
    )


def test_runtime_complexity_guard_stops_before_schema_traversal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _valid_document()
    nutrients = _first_food(document)["nutrients"]
    assert isinstance(nutrients, dict)
    nutrients.update(
        {f"nutrient_{index}_g": 0 for index in range(MAX_RUNTIME_COLLECTION_ITEMS + 1)}
    )

    def unexpected_schema_scan(_document: object) -> object:
        raise AssertionError("over-complex documents must not reach schema traversal")

    monkeypatch.setattr(validation, "_schema_errors", unexpected_schema_scan)

    report = validate_pack_document(document)

    assert [issue.code for issue in report.errors] == ["input_too_complex"]


@pytest.mark.parametrize(
    ("value", "expected_message"),
    [
        ("x" * (MAX_RUNTIME_STRING_CHARS + 1), "strings cannot exceed"),
        (1 << MAX_NUMERIC_BITS, "integers cannot exceed"),
        (Decimal(f"1e{MAX_NUMERIC_BITS + 1}"), "numbers cannot exceed"),
        ([0] * (MAX_RUNTIME_COLLECTION_ITEMS + 1), "lists cannot contain"),
    ],
)
def test_runtime_scalar_and_list_complexity_limits(
    value: object, expected_message: str
) -> None:
    document = _valid_document()
    _first_food(document)["tags"] = value

    report = validate_pack_document(document)

    assert [issue.code for issue in report.errors] == ["input_too_complex"]
    assert expected_message in report.errors[0].message


def test_runtime_depth_cycle_and_shared_container_limits() -> None:
    deep_document = _valid_document()
    nested: object = "leaf"
    for _ in range(MAX_RUNTIME_DEPTH + 1):
        nested = [nested]
    _first_food(deep_document)["tags"] = nested
    assert "nested levels" in validate_pack_document(deep_document).errors[0].message

    cyclic_document = _valid_document()
    cyclic: list[object] = []
    cyclic.append(cyclic)
    _first_food(cyclic_document)["tags"] = cyclic
    assert "recursive or shared" in validate_pack_document(cyclic_document).errors[0].message

    shared_document = _valid_document()
    foods = shared_document["foods"]
    assert isinstance(foods, list) and len(foods) >= 2
    assert isinstance(foods[0], dict) and isinstance(foods[1], dict)
    foods[1]["portions"] = foods[0]["portions"]
    assert "recursive or shared" in validate_pack_document(shared_document).errors[0].message


def test_runtime_total_node_limit() -> None:
    document = _valid_document()
    entry = _first_food(document)
    documents_entries: list[object] = []
    nutrients_per_entry = MAX_RUNTIME_NODES // MAX_PACK_ENTRIES + 5
    for entry_index in range(MAX_PACK_ENTRIES):
        copy = deepcopy(entry)
        nutrients = copy["nutrients"]
        assert isinstance(nutrients, dict)
        nutrients.update(
            {
                f"extra_{entry_index}_{nutrient_index}_g": 0
                for nutrient_index in range(nutrients_per_entry)
            }
        )
        documents_entries.append(copy)
    document["foods"] = documents_entries

    report = validate_pack_document(document)

    assert [issue.code for issue in report.errors] == ["input_too_complex"]
    assert "values" in report.errors[0].message


@pytest.mark.parametrize(
    ("content", "expected_message"),
    [
        (None, "Unable to load food-pack schema"),
        ("{", "Unable to load food-pack schema"),
        ('{"type": 7}', "Invalid food-pack schema"),
    ],
)
def test_schema_loader_failures_are_stable(
    tmp_path: Path, content: str | None, expected_message: str
) -> None:
    schema_path = tmp_path / f"schema-{len(list(tmp_path.iterdir()))}.json"
    if content is not None:
        schema_path.write_text(content, encoding="utf-8")

    with pytest.raises(RuntimeError, match=expected_message):
        _schema_validator(schema_path)


def test_discovery_and_direct_validation_cap_pack_count(tmp_path: Path) -> None:
    directories: list[Path] = []
    for index in range(MAX_PACK_DIRECTORIES + 1):
        pack = tmp_path / f"pack-{index}"
        pack.mkdir()
        (pack / "pack.yaml").write_text(f"id: pack-{index}\n", encoding="utf-8")
        directories.append(pack)

    with pytest.raises(FoodPackLoadError) as caught:
        discover_pack_directories(tmp_path)
    assert caught.value.code == "too_many_packs"

    report = validate_pack_directories(directories)
    assert [issue.code for issue in report.errors] == ["too_many_packs"]


def test_aggregate_entry_limit_stops_before_cross_pack_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    template = _valid_document()
    template_entry = _first_food(template)
    entries_per_pack = MAX_PACK_ENTRIES

    def fake_load(directory: str | Path) -> LoadedFoodPack:
        path = Path(directory)
        document = deepcopy(template)
        document["foods"] = [deepcopy(template_entry) for _ in range(entries_per_pack)]
        manifest = _manifest(document)
        manifest["id"] = path.name
        manifest["entry_count"] = entries_per_pack
        return LoadedFoodPack(directory=path, document=document)

    def unexpected_cross_pack_scan(_documents: object) -> object:
        raise AssertionError("aggregate overflow must not reach cross-pack scanning")

    monkeypatch.setattr(validation, "load_pack_directory", fake_load)
    monkeypatch.setattr(validation, "_cross_pack_issues", unexpected_cross_pack_scan)
    directory_count = MAX_REPOSITORY_ENTRIES // entries_per_pack + 1
    directories = [tmp_path / f"pack-{index}" for index in range(directory_count)]

    report = validate_pack_directories(directories)

    assert "too_many_repository_entries" in {issue.code for issue in report.errors}


def test_discovery_reports_missing_roots_and_candidate_packs_without_manifests(
    tmp_path: Path,
) -> None:
    missing_report = validate_pack_roots([tmp_path / "missing"])
    assert {issue.code for issue in missing_report.errors} == {"path_missing"}

    candidate_foods = tmp_path / "packs" / "candidate" / "foods"
    candidate_foods.mkdir(parents=True)
    (candidate_foods / "foods.yaml").write_text("[]\n", encoding="utf-8")
    candidate_report = validate_pack_roots([tmp_path / "packs"])
    assert {issue.code for issue in candidate_report.errors} == {"manifest_missing"}

    file_path = tmp_path / "not-a-directory.yaml"
    file_path.write_text("[]\n", encoding="utf-8")
    file_report = validate_pack_roots([file_path])
    assert {issue.code for issue in file_report.errors} == {"path_not_directory"}


def test_cli_uses_same_validator_and_warning_exit_semantics(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main([str(VALID_PACK), "--json"]) == 0
    valid_payload = json.loads(capsys.readouterr().out)
    assert valid_payload["schema_version"] == SCHEMA_VERSION
    assert valid_payload["valid"] is True

    assert main([str(HOSTILE_A), "--json"]) == 1
    invalid_payload = json.loads(capsys.readouterr().out)
    assert invalid_payload["valid"] is False
    assert invalid_payload["errors"]


def test_cli_fails_closed_for_a_missing_input_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([str(tmp_path / "mistyped-root"), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)

    assert payload["valid"] is False
    assert [error["code"] for error in payload["errors"]] == ["path_missing"]


def test_human_readable_cli_reports_success_and_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main([str(VALID_PACK)]) == 0
    success = capsys.readouterr().out
    assert f"food-pack schema {SCHEMA_VERSION}: 0 error(s), 0 warning(s)" in success

    assert main([str(HOSTILE_A)]) == 1
    failure = capsys.readouterr().out
    assert "ERROR " in failure
    assert "food-pack schema" in failure
