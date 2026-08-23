from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from io import BufferedIOBase, TextIOWrapper
from pathlib import Path, PurePosixPath
from typing import IO, Any, cast
from zipfile import BadZipFile, ZipFile, ZipInfo

import ijson  # type: ignore[import-untyped]
from pydantic import ValidationError
from sqlalchemy import literal_column
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from opennosh_api.capacity import JobRole
from opennosh_api.database import build_administration_engine
from opennosh_api.models import FoodReference
from opennosh_api.nutrition import HouseholdPortion, NutrientProfile
from opennosh_api.settings import get_settings

USDA_SOURCE = "usda"
USDA_LICENSE = "CC0"


class USDADataType(StrEnum):
    FOUNDATION = "Foundation"
    SR_LEGACY = "SR Legacy"

    @property
    def json_root(self) -> str:
        return {
            USDADataType.FOUNDATION: "FoundationFoods",
            USDADataType.SR_LEGACY: "SRLegacyFoods",
        }[self]

    @property
    def csv_value(self) -> str:
        return {
            USDADataType.FOUNDATION: "foundation_food",
            USDADataType.SR_LEGACY: "sr_legacy_food",
        }[self]


DEFAULT_DATA_TYPES = frozenset(USDADataType)
_JSON_ROOTS = {data_type.json_root: data_type for data_type in USDADataType}
_CSV_DATA_TYPES = {data_type.csv_value: data_type for data_type in USDADataType}
_UNIT_SUFFIXES = {
    "g": "g",
    "mg": "mg",
    "ug": "mcg",
    "mcg": "mcg",
    "µg": "mcg",
    "μg": "mcg",
    "kcal": "kcal",
    "kj": "kj",
    "iu": "iu",
}
_COMMON_NUTRIENTS = {
    1003: ("protein_g", "g"),
    1004: ("fat_g", "g"),
    1005: ("carbohydrate_g", "g"),
    1008: ("energy_kcal", "kcal"),
    1062: ("energy_kj", "kj"),
    1079: ("fiber_g", "g"),
    1087: ("calcium_mg", "mg"),
    1089: ("iron_mg", "mg"),
    1090: ("magnesium_mg", "mg"),
    1091: ("phosphorus_mg", "mg"),
    1092: ("potassium_mg", "mg"),
    1093: ("sodium_mg", "mg"),
    1098: ("copper_mg", "mg"),
    1103: ("selenium_mcg", "mcg"),
    1104: ("vitamin_a_iu", "iu"),
    1106: ("vitamin_a_rae_mcg", "mcg"),
    1110: ("vitamin_d_iu", "iu"),
    1114: ("vitamin_d_mcg", "mcg"),
    1162: ("vitamin_c_mg", "mg"),
    1165: ("thiamin_mg", "mg"),
    1166: ("riboflavin_mg", "mg"),
    1167: ("niacin_mg", "mg"),
    1175: ("vitamin_b6_mg", "mg"),
    1177: ("folate_mcg", "mcg"),
    1178: ("vitamin_b12_mcg", "mcg"),
    1253: ("cholesterol_mg", "mg"),
    1258: ("saturated_fat_g", "g"),
    2000: ("sugars_g", "g"),
}
_NON_NUTRIENT_COMPONENT_IDS = frozenset({1024})
_MAX_INPUT_BYTES = 4 * 1024 * 1024 * 1024
_MAX_ZIP_TOTAL_BYTES = 8 * 1024 * 1024 * 1024
_MAX_ZIP_MEMBERS = 100
_MAX_ZIP_COMPRESSION_RATIO = 200
_MAX_FOODS = 250_000
_MAX_LOOKUP_ROWS = 50_000
_MAX_CHILD_ROWS = 2_000_000
_MAX_CHILDREN_PER_FOOD = 1_000
_MAX_RETAINED_ISSUES = 1_000


class USDAFormatError(ValueError):
    """The bulk input is not a supported USDA Foundation or SR Legacy archive."""


class _LimitedReader(BufferedIOBase):
    """Cap actual decompressed bytes even when ZIP metadata is dishonest."""

    def __init__(self, handle: IO[bytes], limit: int = _MAX_INPUT_BYTES) -> None:
        self._handle = handle
        self._remaining = limit

    def readable(self) -> bool:
        return True

    def _bounded_size(self, size: int | None) -> int:
        if size is None or size < 0:
            return self._remaining + 1
        return min(size, self._remaining + 1)

    def read(self, size: int | None = -1) -> bytes:
        data = self._handle.read(self._bounded_size(size))
        if len(data) > self._remaining:
            raise USDAFormatError(f"input exceeds the {_MAX_INPUT_BYTES}-byte decompressed limit")
        self._remaining -= len(data)
        return data

    def read1(self, size: int = -1) -> bytes:
        read1 = getattr(self._handle, "read1", self._handle.read)
        data = read1(self._bounded_size(size))
        if len(data) > self._remaining:
            raise USDAFormatError(f"input exceeds the {_MAX_INPUT_BYTES}-byte decompressed limit")
        self._remaining -= len(data)
        return data

    def readinto(self, buffer: Any) -> int:
        view = memoryview(buffer)
        data = self.read(len(view))
        view[: len(data)] = data
        return len(data)

    def close(self) -> None:
        if not self.closed:
            self._handle.close()
        super().close()


@dataclass(frozen=True)
class USDAReferenceRecord:
    fdc_id: str
    description: str
    food_category: str | None
    nutrients_json: dict[str, Any]
    portions_json: list[dict[str, Any]]
    updated_at: datetime
    source: str = USDA_SOURCE
    license: str = USDA_LICENSE

    def database_values(self) -> dict[str, Any]:
        return {
            "fdc_id": self.fdc_id,
            "description": self.description,
            "food_category": self.food_category,
            "source": self.source,
            "license": self.license,
            "nutrients_json": self.nutrients_json,
            "portions_json": self.portions_json,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class USDAImportIssue:
    source_path: str
    row_number: int | None
    fdc_id: str | None
    message: str


@dataclass(frozen=True)
class USDAParseOutcome:
    record: USDAReferenceRecord | None = None
    issue: USDAImportIssue | None = None

    def __post_init__(self) -> None:
        if (self.record is None) == (self.issue is None):
            raise ValueError("A parse outcome must contain exactly one record or issue")


@dataclass
class USDAImportReport:
    rows_seen: int = 0
    rows_written: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    rows_skipped_stale: int = 0
    rejected_count: int = 0
    issues: list[USDAImportIssue] = field(default_factory=list)

    @property
    def rows_rejected(self) -> int:
        return self.rejected_count

    @property
    def issues_omitted(self) -> int:
        return self.rejected_count - len(self.issues)

    def add_issue(self, issue: USDAImportIssue) -> None:
        self.rejected_count += 1
        if len(self.issues) < _MAX_RETAINED_ISSUES:
            self.issues.append(issue)


ProgressCallback = Callable[[USDAImportReport], None]


def _decimal(value: object, *, label: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{label} must be a number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{label} must be a number") from error
    if not result.is_finite():
        raise ValueError(f"{label} must be finite")
    return result


def _positive_identifier(value: object, *, label: str) -> str:
    text = str(value).strip() if value is not None else ""
    if not text.isdigit() or int(text) <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return text


def _publication_datetime(value: object) -> datetime:
    text = str(value).strip() if value is not None else ""
    for pattern in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            parsed = datetime.strptime(text, pattern)
        except ValueError:
            continue
        return parsed.replace(tzinfo=UTC)
    raise ValueError("publication date must use YYYY-MM-DD or M/D/YYYY")


def _mapping(value: object, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _sequence(value: object, *, label: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"{label} must be an array")
    return value


def _nutrient_code(nutrient: Mapping[str, Any]) -> str | None:
    nutrient_id_text = _positive_identifier(nutrient.get("id"), label="nutrient id")
    nutrient_id = int(nutrient_id_text)
    raw_unit = str(nutrient.get("unitName", "")).strip()
    unit = _UNIT_SUFFIXES.get(raw_unit.casefold())
    if unit is None:
        if nutrient_id in _NON_NUTRIENT_COMPONENT_IDS:
            return None
        raise ValueError(f"nutrient {nutrient_id} has unsupported unit {raw_unit!r}")
    common = _COMMON_NUTRIENTS.get(nutrient_id)
    if common is None:
        return f"usda_{nutrient_id}_{unit}"
    code, expected_unit = common
    if unit != expected_unit:
        raise ValueError(
            f"nutrient {nutrient_id} expected unit {expected_unit!r}, got {raw_unit!r}"
        )
    return code


def _parse_nutrients(value: object) -> dict[str, Any]:
    nutrients: dict[str, Decimal] = {}
    energy_candidates: dict[int, Decimal] = {}
    entries = _sequence(value, label="foodNutrients")
    if len(entries) > _MAX_CHILDREN_PER_FOOD:
        raise ValueError(
            f"foodNutrients must not contain more than {_MAX_CHILDREN_PER_FOOD} entries"
        )
    for raw_entry in entries:
        entry = _mapping(raw_entry, label="food nutrient")
        if entry.get("amount") is None:
            continue
        nutrient = _mapping(entry.get("nutrient"), label="nutrient metadata")
        nutrient_id = int(_positive_identifier(nutrient.get("id"), label="nutrient id"))
        if nutrient_id in {1008, 2047, 2048}:
            unit = _UNIT_SUFFIXES.get(str(nutrient.get("unitName", "")).strip().casefold())
            if unit == "kcal":
                amount = _decimal(entry.get("amount"), label="energy_kcal amount")
                if nutrient_id in energy_candidates and energy_candidates[nutrient_id] != amount:
                    raise ValueError(
                        f"duplicate energy nutrient {nutrient_id} has conflicting values"
                    )
                energy_candidates[nutrient_id] = amount
                continue
        code = _nutrient_code(nutrient)
        if code is None:
            continue
        amount = _decimal(entry.get("amount"), label=f"{code} amount")
        if code in nutrients and nutrients[code] != amount:
            raise ValueError(f"duplicate nutrient {code} has conflicting values")
        nutrients[code] = amount

    for nutrient_id in (1008, 2048, 2047):
        if nutrient_id in energy_candidates:
            nutrients["energy_kcal"] = energy_candidates[nutrient_id]
            break

    try:
        profile = NutrientProfile.from_authoritative_source(nutrients)
    except ValidationError as error:
        message = error.errors(include_url=False)[0]["msg"]
        raise ValueError(f"invalid nutrient profile: {message}") from error
    return profile.model_dump(mode="json")


def _portion_label(portion: Mapping[str, Any]) -> str:
    amount = _decimal(
        portion.get("amount", portion.get("value")),
        label="portion amount",
    )
    if amount <= 0:
        raise ValueError("portion amount must be greater than zero")
    measure = _mapping(portion.get("measureUnit", {}), label="portion measure unit")
    unit = str(measure.get("abbreviation") or measure.get("name") or "").strip()
    modifier = str(portion.get("modifier") or "").strip()
    description = str(portion.get("portionDescription") or "").strip()
    if unit.casefold() == "undetermined":
        unit = ""
    detail = modifier or unit or description or "portion"
    if unit and modifier and modifier.casefold() not in unit.casefold():
        detail = f"{unit} {modifier}"
    label = f"{format(amount.normalize(), 'f')} {detail}"
    if len(label) > 80:
        raise ValueError("portion name must not exceed 80 characters")
    return label


def _parse_portions(value: object) -> list[dict[str, Any]]:
    portions: list[dict[str, Any]] = []
    names: dict[str, Decimal] = {}
    raw_portions = _sequence(value or [], label="foodPortions")
    if len(raw_portions) > _MAX_CHILDREN_PER_FOOD:
        raise ValueError(
            f"foodPortions must not contain more than {_MAX_CHILDREN_PER_FOOD} entries"
        )
    for raw_portion in raw_portions:
        portion = _mapping(raw_portion, label="food portion")
        label = _portion_label(portion)
        normalized = label.casefold()
        grams = _decimal(portion.get("gramWeight"), label="portion gram weight")
        if normalized in names:
            if names[normalized] == grams:
                continue
            raise ValueError(f"duplicate portion {label!r} has conflicting gram weights")
        names[normalized] = grams
        try:
            parsed = HouseholdPortion(name=label, grams=grams)
        except ValidationError as error:
            message = error.errors(include_url=False)[0]["msg"]
            raise ValueError(f"invalid portion: {message}") from error
        portions.append(parsed.model_dump(mode="json"))
    return portions


def _parse_item(item: Mapping[str, Any], expected_type: USDADataType) -> USDAReferenceRecord:
    declared_type = item.get("dataType")
    if declared_type is not None and str(declared_type) != expected_type.value:
        raise ValueError(f"dataType {declared_type!r} does not match {expected_type.value!r}")
    fdc_id = _positive_identifier(item.get("fdcId"), label="FDC ID")
    description = str(item.get("description") or "").strip()
    if not description:
        raise ValueError("description must not be empty")
    if len(description) > 500:
        raise ValueError("description must not exceed 500 characters")
    category_value = item.get("foodCategory")
    food_category: str | None = None
    if category_value is not None:
        category = _mapping(category_value, label="foodCategory")
        food_category = str(category.get("description") or "").strip() or None
        if food_category is not None and len(food_category) > 255:
            raise ValueError("food category must not exceed 255 characters")
    return USDAReferenceRecord(
        fdc_id=fdc_id,
        description=description,
        food_category=food_category,
        nutrients_json=_parse_nutrients(item.get("foodNutrients")),
        portions_json=_parse_portions(item.get("foodPortions", [])),
        updated_at=_publication_datetime(item.get("publicationDate")),
    )


@contextmanager
def _open_binary(path: Path, member: str | None = None) -> Iterator[_LimitedReader]:
    if member is None:
        if path.stat().st_size > _MAX_INPUT_BYTES:
            raise USDAFormatError(f"{path} exceeds the {_MAX_INPUT_BYTES}-byte input limit")
        with path.open("rb") as raw_handle, _LimitedReader(raw_handle) as limited:
            yield limited
        return
    try:
        with ZipFile(path) as archive:
            _validate_zip_infos(path, archive.infolist())
            with archive.open(member) as zip_handle, _LimitedReader(zip_handle) as limited:
                yield limited
    except BadZipFile as error:
        raise USDAFormatError(f"{path} is not a valid ZIP archive") from error


def _validate_zip_infos(path: Path, infos: Sequence[ZipInfo]) -> None:
    files = [info for info in infos if not info.is_dir()]
    if len(files) > _MAX_ZIP_MEMBERS:
        raise USDAFormatError(f"{path} contains more than {_MAX_ZIP_MEMBERS} files")
    total_size = 0
    for info in files:
        if info.file_size > _MAX_INPUT_BYTES:
            raise USDAFormatError(
                f"{path}:{info.filename} exceeds the {_MAX_INPUT_BYTES}-byte input limit"
            )
        total_size += info.file_size
        if total_size > _MAX_ZIP_TOTAL_BYTES:
            raise USDAFormatError(f"{path} exceeds the {_MAX_ZIP_TOTAL_BYTES}-byte archive limit")
        if info.file_size:
            if info.compress_size == 0:
                raise USDAFormatError(f"{path}:{info.filename} has an invalid compressed size")
            if info.file_size / info.compress_size > _MAX_ZIP_COMPRESSION_RATIO:
                raise USDAFormatError(f"{path}:{info.filename} exceeds the compression-ratio limit")


def _zip_members(path: Path) -> dict[str, str]:
    try:
        with ZipFile(path) as archive:
            _validate_zip_infos(path, archive.infolist())
            members: dict[str, str] = {}
            for name in archive.namelist():
                if name.endswith("/"):
                    continue
                basename = PurePosixPath(name).name
                if basename in members:
                    raise USDAFormatError(f"{path} contains duplicate archive filename {basename}")
                members[basename] = name
            return members
    except BadZipFile as error:
        raise USDAFormatError(f"{path} is not a valid ZIP archive") from error


def _json_source(path: Path) -> tuple[Path, str | None] | None:
    if path.is_file() and path.suffix.casefold() == ".json":
        return path, None
    if path.is_file() and path.suffix.casefold() == ".zip":
        json_members = [
            member
            for name, member in _zip_members(path).items()
            if name.casefold().endswith(".json")
        ]
        if len(json_members) > 1:
            raise USDAFormatError(f"{path} contains more than one JSON file")
        if json_members:
            return path, json_members[0]
    return None


def _detect_json_root(path: Path, member: str | None) -> str:
    with _open_binary(path, member) as handle:
        for prefix, event, value in ijson.parse(handle):
            if prefix == "" and event == "map_key":
                return str(value)
            if event not in {"start_map"}:
                break
    raise USDAFormatError(f"{path} must contain a top-level JSON object")


def _iter_json(
    path: Path,
    member: str | None,
    allowed_data_types: frozenset[USDADataType],
) -> Iterator[USDAParseOutcome]:
    root = _detect_json_root(path, member)
    data_type = _JSON_ROOTS.get(root)
    if data_type is None or data_type not in allowed_data_types:
        allowed = ", ".join(sorted(item.value for item in allowed_data_types))
        raise USDAFormatError(f"{path} contains {root!r}; allowed USDA datasets: {allowed}")
    seen = 0
    fdc_ids: set[str] = set()
    with _open_binary(path, member) as handle:
        for row_number, raw_item in enumerate(ijson.items(handle, f"{root}.item"), start=1):
            seen += 1
            if seen > _MAX_FOODS:
                raise USDAFormatError(f"{path} contains more than {_MAX_FOODS} food records")
            try:
                item = _mapping(raw_item, label="food record")
                record = _parse_item(item, data_type)
                if record.fdc_id in fdc_ids:
                    raise ValueError(f"duplicate FDC ID {record.fdc_id}")
                fdc_ids.add(record.fdc_id)
            except (TypeError, ValueError) as error:
                raw_fdc_id = raw_item.get("fdcId") if isinstance(raw_item, Mapping) else None
                yield USDAParseOutcome(
                    issue=USDAImportIssue(
                        source_path=str(path),
                        row_number=row_number,
                        fdc_id=str(raw_fdc_id) if raw_fdc_id is not None else None,
                        message=str(error),
                    )
                )
            else:
                yield USDAParseOutcome(record=record)
    if seen == 0:
        raise USDAFormatError(f"{path} contains no records under {root}")


class _CSVFiles:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.members: dict[str, str]
        if path.is_dir():
            self.members = {}
            total_size = 0
            for candidate in path.rglob("*.csv"):
                if len(self.members) >= _MAX_ZIP_MEMBERS:
                    raise USDAFormatError(f"{path} contains more than {_MAX_ZIP_MEMBERS} CSV files")
                size = candidate.stat().st_size
                if size > _MAX_INPUT_BYTES:
                    raise USDAFormatError(
                        f"{candidate} exceeds the {_MAX_INPUT_BYTES}-byte input limit"
                    )
                total_size += size
                if total_size > _MAX_ZIP_TOTAL_BYTES:
                    raise USDAFormatError(
                        f"{path} exceeds the {_MAX_ZIP_TOTAL_BYTES}-byte dataset limit"
                    )
                if candidate.name in self.members:
                    raise USDAFormatError(
                        f"{path} contains duplicate CSV filename {candidate.name}"
                    )
                self.members[candidate.name] = str(candidate)
            self._zipped = False
        elif path.is_file() and path.suffix.casefold() == ".zip":
            self.members = {
                name: member
                for name, member in _zip_members(path).items()
                if name.casefold().endswith(".csv")
            }
            self._zipped = True
        else:
            raise USDAFormatError(f"{path} must be a JSON file, CSV directory, or ZIP archive")

    @contextmanager
    def open_text(self, name: str) -> Iterator[IO[str]]:
        member = self.members.get(name)
        if member is None:
            raise USDAFormatError(f"{self.path} is missing required CSV file {name}")
        if self._zipped:
            with _open_binary(self.path, member) as binary_handle:
                with TextIOWrapper(
                    cast(Any, binary_handle), encoding="latin-1", newline=""
                ) as text_handle:
                    yield text_handle
        else:
            with _open_binary(Path(member)) as binary_handle:
                with TextIOWrapper(
                    cast(Any, binary_handle), encoding="latin-1", newline=""
                ) as text_handle:
                    yield text_handle

    def rows(self, name: str) -> Iterator[dict[str, str]]:
        with self.open_text(name) as handle:
            yield from csv.DictReader(handle)


def _lookup(files: _CSVFiles, name: str, *, value_column: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for row_number, row in enumerate(files.rows(name), start=1):
        if row_number > _MAX_LOOKUP_ROWS:
            raise USDAFormatError(f"{files.path}:{name} exceeds the lookup-row limit")
        identifier = row.get("id", "").strip()
        value = row.get(value_column, "").strip()
        if identifier and value:
            result[identifier] = value
    return result


def _iter_csv(
    path: Path, allowed_data_types: frozenset[USDADataType]
) -> Iterator[USDAParseOutcome]:
    files = _CSVFiles(path)
    required = {
        "food.csv",
        "food_category.csv",
        "food_nutrient.csv",
        "food_portion.csv",
        "measure_unit.csv",
        "nutrient.csv",
    }
    missing = sorted(required - files.members.keys())
    if missing:
        raise USDAFormatError(f"{path} is missing required CSV files: {', '.join(missing)}")

    categories = _lookup(files, "food_category.csv", value_column="description")
    measure_units = _lookup(files, "measure_unit.csv", value_column="name")
    nutrient_rows: dict[str, dict[str, str]] = {}
    for row_number, row in enumerate(files.rows("nutrient.csv"), start=1):
        if row_number > _MAX_LOOKUP_ROWS:
            raise USDAFormatError(f"{path}:nutrient.csv exceeds the lookup-row limit")
        nutrient_id = row.get("id", "").strip()
        if nutrient_id:
            nutrient_rows[nutrient_id] = row
    foods: dict[str, tuple[USDADataType, dict[str, Any], int]] = {}
    for row_number, row in enumerate(files.rows("food.csv"), start=2):
        data_type = _CSV_DATA_TYPES.get(row.get("data_type", "").strip())
        if data_type is None or data_type not in allowed_data_types:
            continue
        fdc_id = row.get("fdc_id", "").strip()
        item: dict[str, Any] = {
            "fdcId": fdc_id,
            "dataType": data_type.value,
            "description": row.get("description"),
            "publicationDate": row.get("publication_date"),
            "foodCategory": {
                "description": categories.get(row.get("food_category_id", "").strip(), "")
            },
            "foodNutrients": [],
            "foodPortions": [],
        }
        if fdc_id in foods:
            yield USDAParseOutcome(
                issue=USDAImportIssue(
                    source_path=str(path),
                    row_number=row_number,
                    fdc_id=fdc_id or None,
                    message=f"duplicate FDC ID {fdc_id}",
                )
            )
            continue
        foods[fdc_id] = (data_type, item, row_number)
        if len(foods) > _MAX_FOODS:
            raise USDAFormatError(f"{path}:food.csv exceeds the food-row limit")
    if not foods:
        allowed = ", ".join(sorted(item.value for item in allowed_data_types))
        raise USDAFormatError(f"{path} contains no allowed USDA food rows ({allowed})")

    food_nutrients: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    child_rows = 0
    for row in files.rows("food_nutrient.csv"):
        fdc_id = row.get("fdc_id", "").strip()
        if fdc_id not in foods:
            continue
        child_rows += 1
        if child_rows > _MAX_CHILD_ROWS:
            raise USDAFormatError(f"{path}:food_nutrient.csv exceeds the child-row limit")
        if len(food_nutrients[fdc_id]) >= _MAX_CHILDREN_PER_FOOD:
            raise USDAFormatError(f"{path}: FDC {fdc_id} has too many food_nutrient rows")
        nutrient = nutrient_rows.get(row.get("nutrient_id", "").strip())
        if nutrient is None:
            food_nutrients[fdc_id].append({"amount": row.get("amount"), "nutrient": {}})
            continue
        food_nutrients[fdc_id].append(
            {
                "amount": row.get("amount"),
                "nutrient": {
                    "id": nutrient.get("id"),
                    "name": nutrient.get("name"),
                    "unitName": nutrient.get("unit_name"),
                },
            }
        )

    food_portions: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    child_rows = 0
    for row in files.rows("food_portion.csv"):
        fdc_id = row.get("fdc_id", "").strip()
        if fdc_id not in foods:
            continue
        child_rows += 1
        if child_rows > _MAX_CHILD_ROWS:
            raise USDAFormatError(f"{path}:food_portion.csv exceeds the child-row limit")
        if len(food_portions[fdc_id]) >= _MAX_CHILDREN_PER_FOOD:
            raise USDAFormatError(f"{path}: FDC {fdc_id} has too many food_portion rows")
        unit = measure_units.get(row.get("measure_unit_id", "").strip(), "")
        food_portions[fdc_id].append(
            {
                "amount": row.get("amount"),
                "measureUnit": {"name": unit, "abbreviation": unit},
                "portionDescription": row.get("portion_description"),
                "modifier": row.get("modifier"),
                "gramWeight": row.get("gram_weight"),
            }
        )

    for fdc_id, (data_type, item, row_number) in foods.items():
        item["foodNutrients"] = food_nutrients[fdc_id]
        item["foodPortions"] = food_portions[fdc_id]
        try:
            record = _parse_item(item, data_type)
        except (TypeError, ValueError) as error:
            yield USDAParseOutcome(
                issue=USDAImportIssue(
                    source_path=str(path),
                    row_number=row_number,
                    fdc_id=fdc_id or None,
                    message=str(error),
                )
            )
        else:
            yield USDAParseOutcome(record=record)


def iter_usda(
    path: str | Path,
    *,
    allowed_data_types: Iterable[USDADataType] = DEFAULT_DATA_TYPES,
) -> Iterator[USDAParseOutcome]:
    """Stream validated Foundation and SR Legacy records from USDA JSON or relational CSV."""
    resolved = Path(path)
    allowed = frozenset(allowed_data_types)
    if not allowed or not allowed <= DEFAULT_DATA_TYPES:
        raise ValueError("allowed_data_types must contain Foundation and/or SR Legacy")
    json_source = _json_source(resolved)
    if json_source is not None:
        try:
            yield from _iter_json(*json_source, allowed)
        except ijson.JSONError as error:
            raise USDAFormatError(f"{resolved} contains malformed JSON: {error}") from error
        return
    yield from _iter_csv(resolved, allowed)


async def _write_batch(
    session: AsyncSession, records: Sequence[USDAReferenceRecord]
) -> tuple[int, int, int]:
    values = [record.database_values() for record in records]
    statement = postgresql_insert(FoodReference).values(values)
    upsert = statement.on_conflict_do_update(
        index_elements=[FoodReference.fdc_id],
        set_={
            "description": statement.excluded.description,
            "food_category": statement.excluded.food_category,
            "source": statement.excluded.source,
            "license": statement.excluded.license,
            "nutrients_json": statement.excluded.nutrients_json,
            "portions_json": statement.excluded.portions_json,
            "updated_at": statement.excluded.updated_at,
        },
        where=statement.excluded.updated_at >= FoodReference.updated_at,
    )
    returning_statement: Any = upsert.returning(literal_column("xmax = 0").label("inserted"))
    inserted_flags = list((await session.execute(returning_statement)).scalars())
    inserted = sum(bool(flag) for flag in inserted_flags)
    updated = len(inserted_flags) - inserted
    skipped_stale = len(records) - len(inserted_flags)
    return inserted, updated, skipped_stale


async def import_usda(
    session: AsyncSession,
    paths: Iterable[str | Path],
    *,
    batch_size: int = 500,
    progress: ProgressCallback | None = None,
) -> USDAImportReport:
    """Upsert valid USDA records; the caller controls the surrounding transaction."""
    if batch_size <= 0:
        raise ValueError("batch_size must be greater than zero")
    report = USDAImportReport()
    batch: list[USDAReferenceRecord] = []

    async def flush() -> None:
        if not batch:
            return
        inserted, updated, skipped_stale = await _write_batch(session, batch)
        report.rows_written += inserted + updated
        report.rows_inserted += inserted
        report.rows_updated += updated
        report.rows_skipped_stale += skipped_stale
        batch.clear()
        if progress is not None:
            progress(report)

    for path in paths:
        for outcome in iter_usda(path):
            report.rows_seen += 1
            if outcome.issue is not None:
                report.add_issue(outcome.issue)
                continue
            if outcome.record is None:  # pragma: no cover - enforced by the dataclass
                raise AssertionError("parse outcome did not contain a record")
            batch.append(outcome.record)
            if len(batch) >= batch_size:
                await flush()
        await flush()
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import USDA Foundation and SR Legacy bulk JSON/CSV into foods_reference."
    )
    parser.add_argument("paths", nargs="+", type=Path, help="USDA JSON, ZIP, or CSV directory")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--batch-size", type=int, default=500)
    return parser


async def _run_cli(arguments: argparse.Namespace) -> int:
    settings = get_settings()
    database_url = arguments.database_url or settings.process_database_url(JobRole.ADMINISTRATION)
    engine = build_administration_engine(
        database_url, manifest_path=settings.database_capacity_manifest_path
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    def show_progress(report: USDAImportReport) -> None:
        print(
            f"USDA import: {report.rows_seen} read, {report.rows_written} written, "
            f"{report.rows_rejected} rejected",
            file=sys.stderr,
        )

    try:
        async with session_factory() as session, session.begin():
            report = await import_usda(
                session,
                arguments.paths,
                batch_size=arguments.batch_size,
                progress=show_progress,
            )
    finally:
        await engine.dispose()

    for issue in report.issues:
        location = f"{issue.source_path}:{issue.row_number or '?'}"
        fdc = f" FDC {issue.fdc_id}" if issue.fdc_id else ""
        print(f"{location}:{fdc} {issue.message}", file=sys.stderr)
    if report.issues_omitted:
        print(
            f"USDA import: {report.issues_omitted} additional issues omitted",
            file=sys.stderr,
        )
    print(
        json.dumps(
            {
                "rows_seen": report.rows_seen,
                "rows_written": report.rows_written,
                "rows_inserted": report.rows_inserted,
                "rows_updated": report.rows_updated,
                "rows_skipped_stale": report.rows_skipped_stale,
                "rows_rejected": report.rows_rejected,
            },
            sort_keys=True,
        )
    )
    return 2 if report.issues else 0


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    try:
        return asyncio.run(_run_cli(arguments))
    except (OSError, USDAFormatError, ValueError) as error:
        print(f"USDA import failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
