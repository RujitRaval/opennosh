"""Validate contributor food packs with one CI/runtime implementation."""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation, localcontext
from functools import lru_cache
from itertools import chain, islice
from pathlib import Path
from typing import Any, TextIO

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

SCHEMA_VERSION = "1.0.0"
MAX_PACK_ENTRIES = 100
MAX_FOOD_FILES = 100
MAX_YAML_FILE_BYTES = 1_000_000
MAX_PACK_BYTES = 5_000_000
MAX_YAML_NESTING = 40
MAX_PACK_DIRECTORIES = 100
MAX_REPOSITORY_ENTRIES = 1_000
MAX_RUNTIME_COLLECTION_ITEMS = 1_000
MAX_RUNTIME_NODES = 20_000
MAX_RUNTIME_STRING_CHARS = 10_000
MAX_RUNTIME_DEPTH = 40
MAX_NUMERIC_BITS = 4_096
_REPOSITORY_SCHEMA_PATH = (
    Path(__file__).resolve().parents[3] / "schemas" / "food-pack.schema.json"
)
_PACKAGED_SCHEMA_PATH = Path(__file__).with_name("food-pack.schema.json")
DEFAULT_SCHEMA_PATH = (
    _REPOSITORY_SCHEMA_PATH if _REPOSITORY_SCHEMA_PATH.is_file() else _PACKAGED_SCHEMA_PATH
)
_CORE_NUTRIENTS = ("energy_kcal", "protein_g", "fat_g", "carbohydrate_g")
_ENERGY_TOLERANCE = Decimal("0.15")
_NEAR_DUPLICATE_TOLERANCE = Decimal("0.01")
_ARITHMETIC_CONTEXT = Context(prec=50, rounding=ROUND_HALF_EVEN)

JsonPathPart = str | int
JsonObject = dict[str, Any]


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects ambiguous duplicate mapping keys."""

    def __init__(self, stream: TextIO) -> None:
        super().__init__(stream)
        self._opennosh_depth = 0

    def compose_node(
        self,
        parent: yaml.nodes.Node | None,
        index: int,
    ) -> yaml.nodes.Node:
        if self.check_event(yaml.events.AliasEvent):
            raise yaml.constructor.ConstructorError(
                "while composing YAML",
                None,
                "YAML aliases are not allowed in food packs",
                None,
            )
        self._opennosh_depth += 1
        try:
            if self._opennosh_depth > MAX_YAML_NESTING:
                raise yaml.constructor.ConstructorError(
                    "while composing YAML",
                    None,
                    f"YAML nesting cannot exceed {MAX_YAML_NESTING} levels",
                    None,
                )
            composed = super().compose_node(parent, index)
            if composed is None:  # pragma: no cover - PyYAML's parser contract
                raise yaml.YAMLError("YAML parser returned no node")
            return composed
        finally:
            self._opennosh_depth -= 1


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: yaml.nodes.MappingNode, deep: bool = False
) -> dict[object, object]:
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as error:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from error
        if not isinstance(key, str):
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "food-pack mapping keys must be strings",
                key_node.start_mark,
            )
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """A stable, machine-readable error or warning."""

    severity: str
    code: str
    message: str
    path: tuple[JsonPathPart, ...] = ()
    pack_id: str | None = None
    slug: str | None = None

    @property
    def json_pointer(self) -> str:
        if not self.path:
            return ""
        escaped = (str(part).replace("~", "~0").replace("/", "~1") for part in self.path)
        return "/" + "/".join(escaped)

    def to_dict(self) -> JsonObject:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "path": self.json_pointer,
            "pack_id": self.pack_id,
            "slug": self.slug,
        }


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """All blocking errors and non-blocking warnings for one validation run."""

    errors: tuple[ValidationIssue, ...] = ()
    warnings: tuple[ValidationIssue, ...] = ()

    @property
    def valid(self) -> bool:
        return not self.errors

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": SCHEMA_VERSION,
            "valid": self.valid,
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
        }


@dataclass(frozen=True, slots=True)
class LoadedFoodPack:
    """A normalized pack document and the directory it came from."""

    directory: Path
    document: JsonObject


class FoodPackLoadError(ValueError):
    def __init__(self, *, code: str, message: str, path: Path) -> None:
        super().__init__(message)
        self.code = code
        self.path = path


@lru_cache(maxsize=4)
def _schema_validator(schema_path: Path = DEFAULT_SCHEMA_PATH) -> Draft202012Validator:
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Unable to load food-pack schema at {schema_path}: {error}") from error
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        raise RuntimeError(f"Invalid food-pack schema at {schema_path}: {error.message}") from error
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _checked_yaml_size(path: Path) -> int:
    if path.is_symlink():
        raise FoodPackLoadError(
            code="symlink_not_allowed",
            message="Food-pack YAML files cannot be symbolic links",
            path=path,
        )
    try:
        size = path.stat().st_size
        if size > MAX_YAML_FILE_BYTES:
            raise FoodPackLoadError(
                code="file_too_large",
                message=(
                    f"Food-pack YAML files cannot exceed {MAX_YAML_FILE_BYTES} bytes"
                ),
                path=path,
            )
        return size
    except OSError as error:
        raise FoodPackLoadError(code="file_unreadable", message=str(error), path=path) from error


def _load_yaml(path: Path) -> object:
    _checked_yaml_size(path)
    try:
        with path.open(encoding="utf-8") as handle:
            return yaml.load(handle, Loader=_UniqueKeyLoader)
    except OSError as error:
        raise FoodPackLoadError(code="file_unreadable", message=str(error), path=path) from error
    except UnicodeError as error:
        raise FoodPackLoadError(
            code="file_encoding_invalid",
            message="Food-pack YAML must be valid UTF-8",
            path=path,
        ) from error
    except yaml.YAMLError as error:
        raise FoodPackLoadError(
            code="yaml_invalid", message=f"Invalid YAML: {error}", path=path
        ) from error


def parse_pack_manifest(content: str) -> dict[str, object]:
    """Parse one bounded pack manifest with the canonical strict YAML loader."""

    if len(content.encode("utf-8")) > MAX_YAML_FILE_BYTES:
        raise ValueError("pack manifest exceeds the bounded YAML size")
    try:
        value = yaml.load(content, Loader=_UniqueKeyLoader)
    except yaml.YAMLError as error:
        raise ValueError("pack manifest is invalid YAML") from error
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError("pack manifest must contain one string-keyed mapping")
    return value


def parse_pack_foods(content: str) -> list[object]:
    """Parse one bounded food-entry file with the canonical strict YAML loader."""

    if len(content.encode("utf-8")) > MAX_YAML_FILE_BYTES:
        raise ValueError("food-entry file exceeds the bounded YAML size")
    try:
        value = yaml.load(content, Loader=_UniqueKeyLoader)
    except yaml.YAMLError as error:
        raise ValueError("food-entry file is invalid YAML") from error
    if not isinstance(value, list):
        raise ValueError("food-entry file must contain one list")
    return value


def load_pack_directory(directory: str | Path) -> LoadedFoodPack:
    """Load pack.yaml and every foods/*.yaml file into the schema's normalized shape."""
    pack_directory = Path(directory).resolve()
    manifest_path = pack_directory / "pack.yaml"
    if not manifest_path.is_file():
        raise FoodPackLoadError(
            code="manifest_missing",
            message="Food-pack directory must contain pack.yaml",
            path=manifest_path,
        )
    pack_bytes = _checked_yaml_size(manifest_path)
    manifest = _load_yaml(manifest_path)
    if not isinstance(manifest, dict):
        raise FoodPackLoadError(
            code="manifest_not_mapping",
            message="pack.yaml must contain one mapping",
            path=manifest_path,
        )

    foods_directory = pack_directory / "foods"
    food_paths = sorted(
        islice(
            chain(foods_directory.glob("*.yaml"), foods_directory.glob("*.yml")),
            MAX_FOOD_FILES + 1,
        )
    )
    foods: list[object] = []
    if not food_paths:
        raise FoodPackLoadError(
            code="food_files_missing",
            message="Food-pack directory must contain at least one foods/*.yaml file",
            path=foods_directory,
        )
    if len(food_paths) > MAX_FOOD_FILES:
        raise FoodPackLoadError(
            code="too_many_food_files",
            message=f"A food pack cannot contain more than {MAX_FOOD_FILES} food files",
            path=foods_directory,
        )
    pack_bytes += sum(_checked_yaml_size(path) for path in food_paths)
    if pack_bytes > MAX_PACK_BYTES:
        raise FoodPackLoadError(
            code="pack_too_large",
            message=f"A food pack cannot exceed {MAX_PACK_BYTES} total YAML bytes",
            path=pack_directory,
        )
    for food_path in food_paths:
        entries = _load_yaml(food_path)
        if not isinstance(entries, list):
            raise FoodPackLoadError(
                code="food_file_not_list",
                message="Each food file must contain a YAML list of food entries",
                path=food_path,
            )
        if len(foods) + len(entries) > MAX_PACK_ENTRIES:
            raise FoodPackLoadError(
                code="too_many_entries",
                message=f"A food pack cannot contain more than {MAX_PACK_ENTRIES} entries",
                path=food_path,
            )
        foods.extend(entries)

    return LoadedFoodPack(
        directory=pack_directory,
        document={"pack": manifest, "foods": foods},
    )


def discover_pack_directories(root: str | Path) -> tuple[Path, ...]:
    """Return deterministic pack directories below a repository or pack root."""
    path = Path(root).resolve()
    if not path.exists():
        raise FoodPackLoadError(
            code="path_missing",
            message="Food-pack path does not exist",
            path=path,
        )
    if not path.is_dir():
        raise FoodPackLoadError(
            code="path_not_directory",
            message="Food-pack path must be a directory",
            path=path,
        )
    if (path / "pack.yaml").is_file():
        return (path,)
    candidates: set[Path] = set()

    def add_candidate(candidate: Path) -> None:
        if any(part.startswith(".") for part in candidate.relative_to(path).parts):
            return
        candidates.add(candidate)
        if len(candidates) > MAX_PACK_DIRECTORIES:
            raise FoodPackLoadError(
                code="too_many_packs",
                message=(
                    "A validation run cannot discover more than "
                    f"{MAX_PACK_DIRECTORIES} food packs"
                ),
                path=path,
            )

    for manifest in path.rglob("pack.yaml"):
        add_candidate(manifest.parent)
    for foods_directory in path.rglob("foods"):
        candidate = foods_directory.parent
        if any(part.startswith(".") for part in candidate.relative_to(path).parts):
            continue
        if foods_directory.is_dir() and (
            any(foods_directory.glob("*.yaml"))
            or any(foods_directory.glob("*.yml"))
        ):
            add_candidate(candidate)
    return tuple(sorted(candidates))


def _pack_id(document: Mapping[str, object]) -> str | None:
    pack = document.get("pack")
    if isinstance(pack, Mapping):
        value = pack.get("id")
        return value if isinstance(value, str) else None
    return None


def _runtime_preflight_issue(
    document: Mapping[str, object],
) -> ValidationIssue | None:
    """Bound runtime-owned structures before schema traversal or error rendering."""
    stack: list[tuple[object, tuple[JsonPathPart, ...], int]] = [(document, (), 0)]
    seen_containers: set[int] = set()
    visited = 0
    while stack:
        value, path, depth = stack.pop()
        visited += 1
        if visited > MAX_RUNTIME_NODES:
            reason = f"input cannot contain more than {MAX_RUNTIME_NODES} values"
            return ValidationIssue(
                severity="error",
                code="input_too_complex",
                message=reason,
                path=path,
                pack_id=_pack_id(document),
            )
        if depth > MAX_RUNTIME_DEPTH:
            reason = f"input cannot exceed {MAX_RUNTIME_DEPTH} nested levels"
        elif isinstance(value, str) and len(value) > MAX_RUNTIME_STRING_CHARS:
            reason = (
                f"strings cannot exceed {MAX_RUNTIME_STRING_CHARS} characters"
            )
        elif isinstance(value, Decimal) and not value.is_finite():
            return ValidationIssue(
                severity="error",
                code="non_finite_number",
                message="numbers must be finite",
                path=path,
                pack_id=_pack_id(document),
            )
        elif isinstance(value, int) and not isinstance(value, bool):
            reason = (
                f"integers cannot exceed {MAX_NUMERIC_BITS} bits"
                if value.bit_length() > MAX_NUMERIC_BITS
                else ""
            )
        elif isinstance(value, Decimal):
            reason = (
                f"numbers cannot exceed {MAX_NUMERIC_BITS} bits of magnitude"
                if value.is_finite() and abs(value.adjusted()) > MAX_NUMERIC_BITS
                else ""
            )
        else:
            reason = ""
        if reason:
            return ValidationIssue(
                severity="error",
                code="input_too_complex",
                message=reason,
                path=path,
                pack_id=_pack_id(document),
            )

        if isinstance(value, Mapping):
            container_id = id(value)
            if container_id in seen_containers:
                reason = "input cannot contain recursive or shared containers"
            elif len(value) > MAX_RUNTIME_COLLECTION_ITEMS:
                reason = (
                    "mappings cannot contain more than "
                    f"{MAX_RUNTIME_COLLECTION_ITEMS} entries"
                )
            else:
                seen_containers.add(container_id)
                stack.extend(
                    (child, path + (str(key),), depth + 1)
                    for key, child in value.items()
                )
                continue
        elif isinstance(value, list):
            container_id = id(value)
            if container_id in seen_containers:
                reason = "input cannot contain recursive or shared containers"
            elif len(value) > MAX_RUNTIME_COLLECTION_ITEMS:
                reason = (
                    "lists cannot contain more than "
                    f"{MAX_RUNTIME_COLLECTION_ITEMS} entries"
                )
            else:
                seen_containers.add(container_id)
                stack.extend(
                    (child, path + (index,), depth + 1)
                    for index, child in enumerate(value)
                )
                continue
        else:
            continue
        return ValidationIssue(
            severity="error",
            code="input_too_complex",
            message=reason,
            path=path,
            pack_id=_pack_id(document),
        )
    return None


def _entry_slug(entry: object) -> str | None:
    if isinstance(entry, Mapping):
        value = entry.get("slug")
        return value if isinstance(value, str) else None
    return None


def _schema_errors(document: Mapping[str, object]) -> list[ValidationIssue]:
    pack_id = _pack_id(document)
    issues: list[ValidationIssue] = []
    for error in sorted(
        _schema_validator().iter_errors(document),
        key=lambda item: (tuple(str(part) for part in item.absolute_path), item.message),
    ):
        path = tuple(error.absolute_path)
        slug: str | None = None
        if len(path) >= 2 and path[0] == "foods" and isinstance(path[1], int):
            foods = document.get("foods")
            if isinstance(foods, list) and path[1] < len(foods):
                slug = _entry_slug(foods[path[1]])
        issues.append(
            ValidationIssue(
                severity="error",
                code=f"schema.{error.validator}",
                message=error.message,
                path=path,
                pack_id=pack_id,
                slug=slug,
            )
        )
    return issues


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    try:
        result = value if isinstance(value, Decimal) else Decimal(value)
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _is_nonfinite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return False
    try:
        return not Decimal(str(value)).is_finite()
    except InvalidOperation:
        return True


def _food_entries(document: Mapping[str, object]) -> list[Mapping[str, object]]:
    foods = document.get("foods")
    if not isinstance(foods, list):
        return []
    return [entry for entry in foods if isinstance(entry, Mapping)]


def _semantic_issues(
    document: Mapping[str, object],
) -> tuple[list[ValidationIssue], list[ValidationIssue]]:
    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []
    pack_id = _pack_id(document)
    entries = _food_entries(document)
    pack = document.get("pack")
    if isinstance(pack, Mapping):
        entry_count = pack.get("entry_count")
        if isinstance(entry_count, int) and not isinstance(entry_count, bool):
            if entry_count != len(entries):
                errors.append(
                    ValidationIssue(
                        severity="error",
                        code="entry_count_mismatch",
                        message=(
                            f"pack.entry_count is {entry_count}, but {len(entries)} "
                            "entries were loaded"
                        ),
                        path=("pack", "entry_count"),
                        pack_id=pack_id,
                    )
                )

    for index, entry in enumerate(entries):
        slug = _entry_slug(entry)
        base_path: tuple[JsonPathPart, ...] = ("foods", index)
        nutrients = entry.get("nutrients")
        if isinstance(nutrients, Mapping):
            for code, amount in nutrients.items():
                if _is_nonfinite_number(amount):
                    errors.append(
                        ValidationIssue(
                            severity="error",
                            code="non_finite_number",
                            message=f"{code} must be finite",
                            path=base_path + ("nutrients", str(code)),
                            pack_id=pack_id,
                            slug=slug,
                        )
                    )
            numeric = {code: _decimal(nutrients.get(code)) for code in _CORE_NUTRIENTS}
            if all(value is not None for value in numeric.values()):
                energy = numeric["energy_kcal"]
                protein = numeric["protein_g"]
                fat = numeric["fat_g"]
                carbohydrate = numeric["carbohydrate_g"]
                assert energy is not None
                assert protein is not None
                assert fat is not None
                assert carbohydrate is not None
                with localcontext(_ARITHMETIC_CONTEXT):
                    calculated = protein * 4 + carbohydrate * 4 + fat * 9
                    mismatch = (
                        calculated != 0
                        if energy == 0
                        else abs(calculated - energy) / energy > _ENERGY_TOLERANCE
                    )
                if mismatch:
                    errors.append(
                        ValidationIssue(
                            severity="error",
                            code="macro_energy_mismatch",
                            message=(
                                f"Macro-derived energy {calculated} kcal differs from "
                                f"energy_kcal {energy} by more than 15%"
                            ),
                            path=base_path + ("nutrients", "energy_kcal"),
                            pack_id=pack_id,
                            slug=slug,
                        )
                    )

                density = Decimal(1)
                if entry.get("basis") == "per_100ml":
                    density_value = _decimal(entry.get("density_g_per_ml"))
                    if density_value is not None and density_value > 0:
                        density = density_value
                with localcontext(_ARITHMETIC_CONTEXT):
                    canonical_energy = energy / density
                if canonical_energy > 900:
                    errors.append(
                        ValidationIssue(
                            severity="error",
                            code="energy_too_high",
                            message=(
                                f"Canonical energy {canonical_energy} kcal exceeds "
                                "900 kcal per 100g"
                            ),
                            path=base_path + ("nutrients", "energy_kcal"),
                            pack_id=pack_id,
                            slug=slug,
                        )
                    )

            fiber = _decimal(nutrients.get("fiber_g"))
            carbohydrate = _decimal(nutrients.get("carbohydrate_g"))
            if fiber is not None and carbohydrate is not None and fiber > carbohydrate:
                warnings.append(
                    ValidationIssue(
                        severity="warning",
                        code="fiber_exceeds_carbohydrate",
                        message="fiber_g exceeds carbohydrate_g",
                        path=base_path + ("nutrients", "fiber_g"),
                        pack_id=pack_id,
                        slug=slug,
                    )
                )

        portions = entry.get("portions")
        raw_density = entry.get("density_g_per_ml")
        if _is_nonfinite_number(raw_density):
            errors.append(
                ValidationIssue(
                    severity="error",
                    code="non_finite_number",
                    message="density_g_per_ml must be finite",
                    path=base_path + ("density_g_per_ml",),
                    pack_id=pack_id,
                    slug=slug,
                )
            )
        if isinstance(portions, list):
            for portion_index, portion in enumerate(portions):
                if isinstance(portion, Mapping) and _is_nonfinite_number(portion.get("grams")):
                    errors.append(
                        ValidationIssue(
                            severity="error",
                            code="non_finite_number",
                            message="Portion grams must be finite",
                            path=base_path + ("portions", portion_index, "grams"),
                            pack_id=pack_id,
                            slug=slug,
                        )
                    )
        if not isinstance(portions, list) or not portions:
            warnings.append(
                ValidationIssue(
                    severity="warning",
                    code="no_named_portions",
                    message="Entry has no named portions",
                    path=base_path + ("portions",),
                    pack_id=pack_id,
                    slug=slug,
                )
            )
        source_note = entry.get("source_note")
        if not isinstance(source_note, str) or len(source_note.strip()) < 20:
            warnings.append(
                ValidationIssue(
                    severity="warning",
                    code="short_source_note",
                    message="source_note is shorter than 20 characters",
                    path=base_path + ("source_note",),
                    pack_id=pack_id,
                    slug=slug,
                )
            )
    return errors, warnings


def validate_pack_document(document: Mapping[str, object]) -> ValidationReport:
    """Validate one normalized in-memory document for runtime callers."""
    foods = document.get("foods")
    if isinstance(foods, list) and len(foods) > MAX_PACK_ENTRIES:
        return ValidationReport(
            errors=(
                ValidationIssue(
                    severity="error",
                    code="schema.maxItems",
                    message=f"foods cannot contain more than {MAX_PACK_ENTRIES} entries",
                    path=("foods",),
                    pack_id=_pack_id(document),
                ),
            )
        )
    preflight_issue = _runtime_preflight_issue(document)
    if preflight_issue is not None:
        return ValidationReport(errors=(preflight_issue,))
    schema_errors = _schema_errors(document)
    semantic_errors, warnings = _semantic_issues(document)
    collision_errors, duplicate_warnings = _cross_pack_issues((document,))
    return ValidationReport(
        errors=tuple(schema_errors + semantic_errors + collision_errors),
        warnings=tuple(warnings + duplicate_warnings),
    )


def _canonical_core(entry: Mapping[str, object]) -> tuple[Decimal, ...] | None:
    nutrients = entry.get("nutrients")
    if not isinstance(nutrients, Mapping):
        return None
    values = tuple(_decimal(nutrients.get(code)) for code in _CORE_NUTRIENTS)
    if any(value is None for value in values):
        return None
    core = tuple(value for value in values if value is not None)
    if entry.get("basis") == "per_100ml":
        density = _decimal(entry.get("density_g_per_ml"))
        if density is None or density <= 0:
            return None
        with localcontext(_ARITHMETIC_CONTEXT):
            core = tuple(value / density for value in core)
    return core


def _profiles_near(left: tuple[Decimal, ...], right: tuple[Decimal, ...]) -> bool:
    with localcontext(_ARITHMETIC_CONTEXT):
        return all(
            abs(left_value - right_value)
            / max(abs(left_value), abs(right_value), Decimal(1))
            <= _NEAR_DUPLICATE_TOLERANCE
            for left_value, right_value in zip(left, right, strict=True)
        )


def _cross_pack_issues(
    documents: Sequence[Mapping[str, object]],
) -> tuple[list[ValidationIssue], list[ValidationIssue]]:
    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []
    seen_slugs: dict[str, tuple[str | None, int]] = {}
    profiles: list[tuple[tuple[Decimal, ...], str | None, str | None, int]] = []

    for document in documents:
        pack_id = _pack_id(document)
        for index, entry in enumerate(_food_entries(document)):
            slug = _entry_slug(entry)
            if slug is not None:
                previous = seen_slugs.get(slug)
                if previous is not None:
                    previous_pack, _ = previous
                    errors.append(
                        ValidationIssue(
                            severity="error",
                            code="slug_collision",
                            message=(
                                f"Slug {slug!r} already appears in pack "
                                f"{previous_pack or '<unknown>'!r}"
                            ),
                            path=("foods", index, "slug"),
                            pack_id=pack_id,
                            slug=slug,
                        )
                    )
                else:
                    seen_slugs[slug] = (pack_id, index)

            profile = _canonical_core(entry)
            if profile is None:
                continue
            for previous_profile, previous_pack, previous_slug, _ in profiles:
                if slug != previous_slug and _profiles_near(profile, previous_profile):
                    warnings.append(
                        ValidationIssue(
                            severity="warning",
                            code="near_duplicate_nutrients",
                            message=(
                                "Core nutrient profile is within 1% of "
                                f"{previous_pack or '<unknown>'}/{previous_slug or '<unknown>'}"
                            ),
                            path=("foods", index, "nutrients"),
                            pack_id=pack_id,
                            slug=slug,
                        )
                    )
                    break
            profiles.append((profile, pack_id, slug, index))
    return errors, warnings


def validate_pack_directories(directories: Iterable[str | Path]) -> ValidationReport:
    """Load and validate multiple packs, including cross-pack collision checks."""
    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []
    documents: list[Mapping[str, object]] = []
    resolved_directories: set[Path] = set()
    for item in directories:
        resolved_directories.add(Path(item).resolve())
        if len(resolved_directories) > MAX_PACK_DIRECTORIES:
            errors.append(
                ValidationIssue(
                    severity="error",
                    code="too_many_packs",
                    message=(
                        "A validation run cannot contain more than "
                        f"{MAX_PACK_DIRECTORIES} food packs"
                    ),
                )
            )
            return ValidationReport(errors=tuple(errors))

    total_entries = 0
    for directory in sorted(resolved_directories, key=str):
        try:
            loaded = load_pack_directory(directory)
        except FoodPackLoadError as error:
            errors.append(
                ValidationIssue(
                    severity="error",
                    code=error.code,
                    message=str(error),
                    path=(str(error.path),),
                )
            )
            continue
        preflight_issue = _runtime_preflight_issue(loaded.document)
        if preflight_issue is not None:
            errors.append(preflight_issue)
            continue
        loaded_entries = _food_entries(loaded.document)
        total_entries += len(loaded_entries)
        if total_entries > MAX_REPOSITORY_ENTRIES:
            errors.append(
                ValidationIssue(
                    severity="error",
                    code="too_many_repository_entries",
                    message=(
                        "A validation run cannot contain more than "
                        f"{MAX_REPOSITORY_ENTRIES} food entries"
                    ),
                    path=(str(directory),),
                )
            )
            return ValidationReport(errors=tuple(errors), warnings=tuple(warnings))
        documents.append(loaded.document)
        errors.extend(_schema_errors(loaded.document))
        semantic_errors, semantic_warnings = _semantic_issues(loaded.document)
        errors.extend(semantic_errors)
        warnings.extend(semantic_warnings)

    collision_errors, duplicate_warnings = _cross_pack_issues(documents)
    errors.extend(collision_errors)
    warnings.extend(duplicate_warnings)
    return ValidationReport(errors=tuple(errors), warnings=tuple(warnings))


def validate_pack_repository(root: str | Path = "packs") -> ValidationReport:
    """Discover and validate every food pack below root."""
    return validate_pack_roots((root,))


def _load_error_issue(error: FoodPackLoadError) -> ValidationIssue:
    return ValidationIssue(
        severity="error",
        code=error.code,
        message=str(error),
        path=(str(error.path),),
    )


def validate_pack_roots(roots: Iterable[str | Path]) -> ValidationReport:
    """Discover roots and report missing or invalid paths as blocking errors."""
    discovery_errors: list[ValidationIssue] = []
    directories: set[Path] = set()
    for root in roots:
        try:
            directories.update(discover_pack_directories(root))
        except FoodPackLoadError as error:
            discovery_errors.append(_load_error_issue(error))
    report = validate_pack_directories(directories)
    return ValidationReport(
        errors=tuple(discovery_errors) + report.errors,
        warnings=report.warnings,
    )


def _human_lines(report: ValidationReport) -> list[str]:
    lines = [
        f"food-pack schema {SCHEMA_VERSION}: "
        f"{len(report.errors)} error(s), {len(report.warnings)} warning(s)"
    ]
    for issue in (*report.errors, *report.warnings):
        location = issue.json_pointer or "/"
        context = "/".join(value for value in (issue.pack_id, issue.slug) if value)
        prefix = f" ({context})" if context else ""
        lines.append(f"{issue.severity.upper()} {issue.code} {location}{prefix}: {issue.message}")
    return lines


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        default=[Path("packs")],
        help="Pack directories or roots to discover (default: packs)",
    )
    parser.add_argument("--json", action="store_true", help="Emit a JSON validation report")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = validate_pack_roots(args.paths)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print("\n".join(_human_lines(report)))
    return 0 if report.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
