"""Fail-closed scale and identity assessment for USDA Branded Foods archives."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal
from zipfile import BadZipFile, ZipFile

import ijson  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, field_validator

_DEFAULT_MANIFEST_NAME = "usda-branded-assessment.v1.json"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SUPPORTED_GTIN_LENGTHS = frozenset({8, 12, 13, 14})
_READ_CHUNK_BYTES = 1024 * 1024


class BrandedAssessmentManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    dataset_id: str
    filename: str
    sha256: str
    size_bytes: int = Field(gt=0, le=512 * 1024 * 1024)
    json_member: str
    uncompressed_size_bytes: int = Field(gt=0, le=4 * 1024 * 1024 * 1024)
    expected_rows: int | None = Field(default=None, gt=0, le=3_000_000)
    maximum_rows: int = Field(gt=0, le=3_000_000)
    maximum_activation_records: int = Field(gt=0, le=3_000_000)

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if _SHA256_PATTERN.fullmatch(value) is None:
            raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
        return value


@dataclass(frozen=True)
class Identity:
    key: str
    gtin14: str


def _default_manifest_path() -> Path:
    packaged = files("opennosh_api").joinpath(_DEFAULT_MANIFEST_NAME)
    if packaged.is_file():
        return Path(str(packaged))
    return Path(__file__).resolve().parents[3] / "config" / _DEFAULT_MANIFEST_NAME


def load_manifest(path: str | Path | None = None) -> BrandedAssessmentManifest:
    resolved = Path(path) if path is not None else _default_manifest_path()
    return BrandedAssessmentManifest.model_validate_json(resolved.read_text(encoding="utf-8"))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_READ_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def verify_archive(path: Path, manifest: BrandedAssessmentManifest) -> None:
    if path.name != manifest.filename:
        raise ValueError("archive filename does not match the pinned manifest")
    if path.stat().st_size != manifest.size_bytes:
        raise ValueError("archive size does not match the pinned manifest")
    if _file_sha256(path) != manifest.sha256:
        raise ValueError("archive SHA-256 does not match the pinned manifest")
    try:
        with ZipFile(path) as archive:
            files_in_archive = [item for item in archive.infolist() if not item.is_dir()]
            if len(files_in_archive) != 1:
                raise ValueError("Branded Foods archive must contain exactly one file")
            member = files_in_archive[0]
            if member.filename != manifest.json_member:
                raise ValueError("archive JSON member does not match the pinned manifest")
            if member.file_size != manifest.uncompressed_size_bytes:
                raise ValueError("archive expanded size does not match the pinned manifest")
            if member.compress_size == 0 or member.file_size / member.compress_size > 100:
                raise ValueError("archive compression ratio exceeds the safety limit")
    except BadZipFile as error:
        raise ValueError("archive is not a valid ZIP file") from error


def canonical_gtin(value: object) -> str | None:
    raw = str(value).strip() if value is not None else ""
    if not raw.isascii() or not raw.isdigit() or len(raw) not in _SUPPORTED_GTIN_LENGTHS:
        return None
    body = raw[:-1]
    weighted = sum(
        int(digit) * (3 if index % 2 == 0 else 1) for index, digit in enumerate(reversed(body))
    )
    if (10 - weighted % 10) % 10 != int(raw[-1]):
        return None
    return raw.zfill(14)


def _normalized_text(value: object) -> str:
    return " ".join(str(value or "").split()).casefold()


def _identity(item: dict[str, Any]) -> tuple[Identity | None, str | None]:
    raw_gtin = str(item.get("gtinUpc") or "").strip()
    if not raw_gtin:
        return None, "missing_gtin"
    gtin14 = canonical_gtin(raw_gtin)
    if gtin14 is None:
        return None, "invalid_gtin"
    country = _normalized_text(item.get("marketCountry"))
    if not country:
        return None, "missing_market_country"
    return Identity(key=f"{country}\x1f{gtin14}", gtin14=gtin14), None


def _signature(item: dict[str, Any]) -> str:
    product_fields = {
        "description": _normalized_text(item.get("description")),
        "brand_owner": _normalized_text(item.get("brandOwner")),
        "brand_name": _normalized_text(item.get("brandName")),
        "ingredients": _normalized_text(item.get("ingredients")),
        "serving_size": str(item.get("servingSize") or ""),
        "serving_size_unit": _normalized_text(item.get("servingSizeUnit")),
        "household_serving": _normalized_text(item.get("householdServingFullText")),
        "food_nutrients": item.get("foodNutrients") or [],
        "food_portions": item.get("foodPortions") or [],
    }
    encoded = json.dumps(
        product_fields, default=str, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


@contextmanager
def _items(path: Path, manifest: BrandedAssessmentManifest) -> Iterator[Iterator[dict[str, Any]]]:
    with ZipFile(path) as archive, archive.open(manifest.json_member) as handle:
        yield ijson.items(handle, "BrandedFoods.item")


def assess_archive(
    path: Path,
    manifest: BrandedAssessmentManifest,
    *,
    workspace: Path,
) -> dict[str, Any]:
    verify_archive(path, manifest)
    database_path = workspace / "branded-identities.sqlite3"
    connection = sqlite3.connect(database_path)
    try:
        connection.executescript(
            """
            PRAGMA journal_mode = OFF;
            PRAGMA synchronous = OFF;
            PRAGMA temp_store = FILE;
            CREATE TABLE foods (
                fdc_id TEXT PRIMARY KEY,
                identity_key TEXT,
                signature TEXT
            );
            """
        )
        counters = {
            "rows_seen": 0,
            "duplicate_fdc_ids": 0,
            "missing_gtin": 0,
            "invalid_gtin": 0,
            "missing_market_country": 0,
        }
        batch: list[tuple[str, str | None, str | None]] = []
        with _items(path, manifest) as items:
            for raw_item in items:
                counters["rows_seen"] += 1
                if counters["rows_seen"] > manifest.maximum_rows:
                    raise ValueError("Branded Foods row count exceeds the assessment limit")
                if raw_item.get("dataType") != "Branded":
                    raise ValueError("archive contains a non-Branded food record")
                fdc_id = str(raw_item.get("fdcId") or "").strip()
                if not fdc_id.isdigit() or int(fdc_id) <= 0:
                    raise ValueError("archive contains an invalid FDC ID")
                identity, reason = _identity(raw_item)
                if reason is not None:
                    counters[reason] += 1
                batch.append(
                    (
                        fdc_id,
                        identity.key if identity is not None else None,
                        _signature(raw_item) if identity is not None else None,
                    )
                )
                if len(batch) >= 10_000:
                    counters["duplicate_fdc_ids"] += _insert_batch(connection, batch)
                    batch.clear()
            counters["duplicate_fdc_ids"] += _insert_batch(connection, batch)
        connection.execute("CREATE INDEX foods_identity ON foods(identity_key)")
        connection.commit()
        valid_identity_rows = int(
            connection.execute(
                "SELECT COUNT(*) FROM foods WHERE identity_key IS NOT NULL"
            ).fetchone()[0]
        )
        duplicate_identity_groups, duplicate_identity_records, conflicting_groups = (
            connection.execute(
                """
                SELECT COUNT(*), COALESCE(SUM(group_size), 0),
                       COALESCE(SUM(CASE WHEN signature_count > 1 THEN 1 ELSE 0 END), 0)
                FROM (
                    SELECT COUNT(*) AS group_size, COUNT(DISTINCT signature) AS signature_count
                    FROM foods
                    WHERE identity_key IS NOT NULL
                    GROUP BY identity_key
                    HAVING COUNT(*) > 1
                )
                """
            ).fetchone()
        )
        duplicate_identity_groups = int(duplicate_identity_groups)
        duplicate_identity_records = int(duplicate_identity_records)
        conflicting_groups = int(conflicting_groups)
        exact_duplicate_groups = duplicate_identity_groups - conflicting_groups
        eligible_records = valid_identity_rows - duplicate_identity_records
        # Keep the accounting exhaustive even for a malformed archive that repeats an
        # FDC ID: every source row is either eligible or quarantined. Duplicate FDC IDs
        # still block activation below, while this total remains truthful.
        quarantined_records = counters["rows_seen"] - eligible_records
        activation_blockers: list[str] = []
        if counters["duplicate_fdc_ids"]:
            activation_blockers.append("duplicate_fdc_ids")
        if conflicting_groups:
            activation_blockers.append("conflicting_gtin_country_identities")
        if eligible_records > manifest.maximum_activation_records:
            activation_blockers.append("search_projection_scale_limit")
        if manifest.expected_rows is not None and counters["rows_seen"] != manifest.expected_rows:
            raise ValueError("Branded Foods row count does not match the pinned manifest")
        return {
            "schema_version": "1.0",
            "dataset_id": manifest.dataset_id,
            "sha256": manifest.sha256,
            **counters,
            "valid_identity_rows": valid_identity_rows,
            "duplicate_identity_groups": duplicate_identity_groups,
            "duplicate_identity_records": duplicate_identity_records,
            "conflicting_identity_groups": conflicting_groups,
            "exact_duplicate_identity_groups": exact_duplicate_groups,
            "eligible_records_after_quarantine": eligible_records,
            "quarantined_records": quarantined_records,
            "deduplication_key": "normalized marketCountry + checksum-valid GTIN-14",
            "duplicate_action": "quarantine_entire_identity_group",
            "invalid_identity_action": "quarantine",
            "maximum_activation_records": manifest.maximum_activation_records,
            "activation_status": "hold" if activation_blockers else "eligible",
            "activation_blockers": activation_blockers,
        }
    finally:
        connection.close()


def _insert_batch(
    connection: sqlite3.Connection, batch: Sequence[tuple[str, str | None, str | None]]
) -> int:
    duplicates = 0
    for row in batch:
        try:
            connection.execute(
                "INSERT INTO foods (fdc_id, identity_key, signature) VALUES (?, ?, ?)", row
            )
        except sqlite3.IntegrityError:
            duplicates += 1
    return duplicates


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    try:
        manifest = load_manifest(arguments.manifest)
        with TemporaryDirectory(prefix="opennosh-branded-assessment-") as directory:
            report = assess_archive(arguments.archive, manifest, workspace=Path(directory))
        encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
        if arguments.output is not None:
            arguments.output.write_text(encoded, encoding="utf-8")
        print(encoded, end="")
        return 0
    except (OSError, ValueError, ijson.JSONError) as error:
        print(f"USDA Branded Foods assessment failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
