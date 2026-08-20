"""Validate and transactionally load CC0 food packs into foods_community."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import or_, select, text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from opennosh_api.foodpacks.validation import (
    FoodPackLoadError,
    ValidationIssue,
    discover_pack_directories,
    load_pack_directory,
    validate_pack_document,
)
from opennosh_api.models import FoodCommunity
from opennosh_api.nutrition import DeclaredNutrients, HouseholdPortion

_SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z.-]+)?$"
)
_RETRYABLE_SQLSTATES = frozenset({"40001", "40P01"})
DEFAULT_MAX_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class CommunityFoodRecord:
    pack_id: str
    pack_version: str
    slug: str
    name: str
    name_local: str | None
    locale: str
    category: str
    provenance: str
    source_uri: str | None
    source_license: str
    source_note: str | None
    nutrients_json: dict[str, Any]
    portions_json: list[dict[str, Any]]
    pack_license: str
    contributed_by: str

    def database_values(self) -> dict[str, Any]:
        return {item.name: getattr(self, item.name) for item in fields(self)}

    def export_entry(self) -> dict[str, Any]:
        """Return the clean CC0 representation used by the future export endpoint."""
        entry: dict[str, Any] = {
            "slug": self.slug,
            "name": self.name,
            "category": self.category,
            "contributed_by": self.contributed_by,
            "provenance": self.provenance,
            "source_uri": self.source_uri,
            "source_license": self.source_license,
            "basis": self.nutrients_json["basis"],
            "nutrients": self.nutrients_json["nutrients"],
            "portions": self.portions_json,
        }
        for key, value in (
            ("name_local", self.name_local),
            ("source_note", self.source_note),
            ("density_g_per_ml", self.nutrients_json.get("density_g_per_ml")),
        ):
            if value is not None:
                entry[key] = value
        return entry


@dataclass(frozen=True, slots=True)
class PreparedFoodPack:
    pack_id: str | None
    pack_version: str | None
    records: tuple[CommunityFoodRecord, ...] = ()
    errors: tuple[ValidationIssue, ...] = ()
    warnings: tuple[ValidationIssue, ...] = ()
    pack_rejected: bool = False


@dataclass(slots=True)
class FoodPackLoadReport:
    pack_id: str | None = None
    pack_version: str | None = None
    entries_seen: int = 0
    entries_inserted: int = 0
    entries_updated: int = 0
    entries_unchanged: int = 0
    entries_skipped_stale: int = 0
    entries_rejected: int = 0
    issues: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)
    pack_rejected: bool = False

    @property
    def entries_written(self) -> int:
        return self.entries_inserted + self.entries_updated

    def to_dict(self) -> dict[str, Any]:
        return {
            "pack_id": self.pack_id,
            "pack_version": self.pack_version,
            "pack_rejected": self.pack_rejected,
            "entries_seen": self.entries_seen,
            "entries_written": self.entries_written,
            "entries_inserted": self.entries_inserted,
            "entries_updated": self.entries_updated,
            "entries_unchanged": self.entries_unchanged,
            "entries_skipped_stale": self.entries_skipped_stale,
            "entries_rejected": self.entries_rejected,
            "errors": [issue.to_dict() for issue in self.issues],
            "warnings": [issue.to_dict() for issue in self.warnings],
        }


@dataclass(frozen=True, slots=True)
class FoodPackBatchLoadReport:
    packs: tuple[FoodPackLoadReport, ...]

    @property
    def entries_seen(self) -> int:
        return sum(pack.entries_seen for pack in self.packs)

    @property
    def entries_written(self) -> int:
        return sum(pack.entries_written for pack in self.packs)

    @property
    def entries_inserted(self) -> int:
        return sum(pack.entries_inserted for pack in self.packs)

    @property
    def entries_updated(self) -> int:
        return sum(pack.entries_updated for pack in self.packs)

    @property
    def entries_unchanged(self) -> int:
        return sum(pack.entries_unchanged for pack in self.packs)

    @property
    def entries_rejected(self) -> int:
        return sum(pack.entries_rejected for pack in self.packs)

    @property
    def entries_skipped_stale(self) -> int:
        return sum(pack.entries_skipped_stale for pack in self.packs)

    @property
    def failed(self) -> bool:
        return any(pack.pack_rejected or pack.entries_rejected for pack in self.packs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "packs_seen": len(self.packs),
            "entries_seen": self.entries_seen,
            "entries_written": self.entries_written,
            "entries_inserted": self.entries_inserted,
            "entries_updated": self.entries_updated,
            "entries_unchanged": self.entries_unchanged,
            "entries_skipped_stale": self.entries_skipped_stale,
            "entries_rejected": self.entries_rejected,
            "packs": [pack.to_dict() for pack in self.packs],
        }


def _entry_index(issue: ValidationIssue) -> int | None:
    if len(issue.path) >= 2 and issue.path[0] == "foods" and isinstance(issue.path[1], int):
        return issue.path[1]
    return None


def _entry_count_error_is_redundant(document: Mapping[str, object]) -> bool:
    pack = document.get("pack")
    foods = document.get("foods")
    return (
        isinstance(pack, Mapping)
        and isinstance(foods, list)
        and pack.get("entry_count") == len(foods)
    )


def _record_from_entry(
    pack: Mapping[str, Any], entry: Mapping[str, Any]
) -> CommunityFoodRecord:
    declared = DeclaredNutrients.model_validate(
        {
            "basis": entry["basis"],
            "density_g_per_ml": entry.get("density_g_per_ml"),
            "nutrients": entry["nutrients"],
        }
    )
    profile = declared.to_canonical()
    portions = [HouseholdPortion.model_validate(item) for item in entry.get("portions", [])]
    return CommunityFoodRecord(
        pack_id=pack["id"],
        pack_version=pack["version"],
        slug=entry["slug"],
        name=entry["name"],
        name_local=entry.get("name_local"),
        locale=pack["locale"],
        category=entry["category"],
        provenance=entry["provenance"],
        source_uri=entry["source_uri"],
        source_license=entry["source_license"],
        source_note=entry.get("source_note"),
        nutrients_json=profile.model_dump(mode="json"),
        portions_json=[portion.model_dump(mode="json") for portion in portions],
        pack_license=pack["license"],
        contributed_by=entry["contributed_by"],
    )


def prepare_food_pack(path: str | Path) -> PreparedFoodPack:
    """Load once, validate with the shared validator, and retain every valid entry."""
    loaded = load_pack_directory(path)
    document = loaded.document
    raw_pack = document.get("pack")
    raw_foods = document.get("foods")
    pack_id = raw_pack.get("id") if isinstance(raw_pack, Mapping) else None
    pack_version = raw_pack.get("version") if isinstance(raw_pack, Mapping) else None
    report = validate_pack_document(document)

    entry_errors: dict[int, list[ValidationIssue]] = {}
    pack_errors: list[ValidationIssue] = []
    for issue in report.errors:
        index = _entry_index(issue)
        if index is not None:
            entry_errors.setdefault(index, []).append(issue)
        elif issue.code == "entry_count_mismatch" and _entry_count_error_is_redundant(document):
            continue
        else:
            pack_errors.append(issue)

    if pack_errors or not isinstance(raw_pack, Mapping) or not isinstance(raw_foods, list):
        return PreparedFoodPack(
            pack_id=pack_id if isinstance(pack_id, str) else None,
            pack_version=pack_version if isinstance(pack_version, str) else None,
            errors=report.errors,
            warnings=report.warnings,
            pack_rejected=True,
        )

    records: list[CommunityFoodRecord] = []
    for index, raw_entry in enumerate(raw_foods):
        if index in entry_errors:
            continue
        if not isinstance(raw_entry, Mapping):
            continue
        try:
            records.append(_record_from_entry(raw_pack, raw_entry))
        except (KeyError, TypeError, ValueError, ValidationError) as error:
            issue = ValidationIssue(
                severity="error",
                code="entry_conversion_failed",
                message=str(error),
                path=("foods", index),
                pack_id=pack_id if isinstance(pack_id, str) else None,
                slug=raw_entry.get("slug") if isinstance(raw_entry.get("slug"), str) else None,
            )
            entry_errors.setdefault(index, []).append(issue)

    errors = [issue for issues in entry_errors.values() for issue in issues]
    return PreparedFoodPack(
        pack_id=pack_id if isinstance(pack_id, str) else None,
        pack_version=pack_version if isinstance(pack_version, str) else None,
        records=tuple(records),
        errors=tuple(dict.fromkeys(errors)),
        warnings=report.warnings,
    )


def _semver_key(version: str) -> tuple[int, int, int, tuple[tuple[int, int | str], ...]]:
    match = _SEMVER.fullmatch(version)
    if match is None:
        raise ValueError(f"invalid stored pack version: {version!r}")
    prerelease = match.group(4)
    if prerelease is None:
        prerelease_key: tuple[tuple[int, int | str], ...] = ((2, ""),)
    else:
        prerelease_key = tuple(
            (0, int(part)) if part.isdigit() else (1, part)
            for part in prerelease.split(".")
        )
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)), prerelease_key)


def _row_matches(row: FoodCommunity, record: CommunityFoodRecord) -> bool:
    return all(
        getattr(row, field_name) == value
        for field_name, value in record.database_values().items()
        if field_name != "pack_version"
    ) and row.pack_version == record.pack_version


def _apply_record(row: FoodCommunity, record: CommunityFoodRecord) -> None:
    for field_name, value in record.database_values().items():
        setattr(row, field_name, value)


async def load_food_pack(session: AsyncSession, path: str | Path) -> FoodPackLoadReport:
    """Load valid entries; the caller owns the surrounding transaction."""
    prepared = prepare_food_pack(path)
    rejected_indexes = {
        index
        for issue in prepared.errors
        if (index := _entry_index(issue)) is not None
    }
    report = FoodPackLoadReport(
        pack_id=prepared.pack_id,
        pack_version=prepared.pack_version,
        entries_seen=len(prepared.records) + len(rejected_indexes),
        entries_rejected=len(rejected_indexes),
        issues=list(prepared.errors),
        warnings=list(prepared.warnings),
        pack_rejected=prepared.pack_rejected,
    )
    if prepared.pack_rejected or not prepared.records or prepared.pack_id is None:
        return report

    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:pack_id, 0))"),
        {"pack_id": prepared.pack_id},
    )
    slugs = [record.slug for record in prepared.records]
    existing_rows = list(
        (
            await session.scalars(
                select(FoodCommunity).where(
                    or_(
                        FoodCommunity.pack_id == prepared.pack_id,
                        FoodCommunity.slug.in_(slugs),
                    )
                )
            )
        ).all()
    )
    existing_by_slug = {row.slug: row for row in existing_rows}
    current_versions = [
        row.pack_version for row in existing_rows if row.pack_id == prepared.pack_id
    ]
    if current_versions and _semver_key(prepared.pack_version or "") < max(
        _semver_key(version) for version in current_versions
    ):
        report.entries_skipped_stale = len(prepared.records)
        return report

    for record in prepared.records:
        existing = existing_by_slug.get(record.slug)
        if existing is not None:
            if existing.pack_id != record.pack_id:
                report.entries_rejected += 1
                report.issues.append(
                    ValidationIssue(
                        severity="error",
                        code="slug_collision",
                        message=f"Slug {record.slug!r} belongs to pack {existing.pack_id!r}",
                        path=("foods", record.slug, "slug"),
                        pack_id=record.pack_id,
                        slug=record.slug,
                    )
                )
            elif _row_matches(existing, record):
                report.entries_unchanged += 1
            else:
                _apply_record(existing, record)
                report.entries_updated += 1
            continue

        statement = (
            postgresql_insert(FoodCommunity)
            .values(record.database_values())
            .on_conflict_do_nothing(index_elements=[FoodCommunity.slug])
            .returning(FoodCommunity.id)
        )
        inserted_id = (await session.execute(statement)).scalar_one_or_none()
        if inserted_id is None:
            report.entries_rejected += 1
            report.issues.append(
                ValidationIssue(
                    severity="error",
                    code="slug_collision",
                    message=f"Slug {record.slug!r} was concurrently claimed by another pack",
                    path=("foods", record.slug, "slug"),
                    pack_id=record.pack_id,
                    slug=record.slug,
                )
            )
        else:
            report.entries_inserted += 1
    return report


def _sqlstate(error: DBAPIError) -> str | None:
    candidate: object | None = error.orig
    for _ in range(3):
        sqlstate = getattr(candidate, "sqlstate", None)
        if isinstance(sqlstate, str):
            return sqlstate
        candidate = getattr(candidate, "__cause__", None)
    return None


async def load_food_pack_with_retries(
    session_factory: async_sessionmaker[AsyncSession],
    path: str | Path,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> FoodPackLoadReport:
    if max_attempts <= 0:
        raise ValueError("max_attempts must be greater than zero")
    for attempt in range(1, max_attempts + 1):
        try:
            async with session_factory() as session, session.begin():
                return await load_food_pack(session, path)
        except DBAPIError as error:
            if _sqlstate(error) not in _RETRYABLE_SQLSTATES or attempt == max_attempts:
                raise
            await asyncio.sleep(0)
    raise AssertionError("retry loop exited unexpectedly")


async def load_food_pack_root_with_retries(
    session_factory: async_sessionmaker[AsyncSession],
    root: str | Path,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> FoodPackBatchLoadReport:
    """Discover a pack or repository root and load each pack deterministically."""
    directories = discover_pack_directories(root)
    if not directories:
        raise FoodPackLoadError(
            code="packs_missing",
            message="Food-pack path does not contain any pack.yaml manifests",
            path=Path(root),
        )
    reports = []
    for directory in directories:
        reports.append(
            await load_food_pack_with_retries(
                session_factory,
                directory,
                max_attempts=max_attempts,
            )
        )
    return FoodPackBatchLoadReport(packs=tuple(reports))
