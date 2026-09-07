"""Checksum-pinned, fail-closed USDA reference-data release loader."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from opennosh_api.capacity import JobRole
from opennosh_api.database import build_administration_engine
from opennosh_api.importers.usda import (
    USDADataType,
    USDAImportReport,
    import_usda,
    iter_usda,
)
from opennosh_api.settings import get_settings

_DEFAULT_MANIFEST_NAME = "usda-reference-release.v1.json"
_DOWNLOAD_CHUNK_BYTES = 1024 * 1024
_DATASET_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
_FILENAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}\.zip$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_USDA_DOWNLOAD_HOST = "fdc.nal.usda.gov"


def _is_plain_usda_https_url(value: str) -> bool:
    parsed = urlparse(value)
    return (
        parsed.scheme == "https"
        and parsed.netloc == _USDA_DOWNLOAD_HOST
        and not parsed.query
        and not parsed.fragment
    )


class USDAReleaseDataset(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    data_type: USDADataType
    filename: str
    url: str
    sha256: str
    size_bytes: int = Field(gt=0, le=4 * 1024 * 1024 * 1024)
    rows_seen: int = Field(gt=0)
    rows_accepted: int = Field(gt=0)
    rows_rejected: int = Field(ge=0)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if _DATASET_ID_PATTERN.fullmatch(value) is None:
            raise ValueError("dataset id must be a lowercase ASCII slug")
        return value

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        if _FILENAME_PATTERN.fullmatch(value) is None:
            raise ValueError("dataset filename must be one bounded ZIP basename")
        return value

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        if not _is_plain_usda_https_url(value):
            raise ValueError("dataset URL must be a plain USDA FoodData Central HTTPS URL")
        return value

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if _SHA256_PATTERN.fullmatch(value) is None:
            raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
        return value

    @model_validator(mode="after")
    def validate_row_accounting(self) -> USDAReleaseDataset:
        if self.rows_accepted + self.rows_rejected != self.rows_seen:
            raise ValueError("accepted plus rejected rows must equal rows seen")
        return self


class USDAReferenceRelease(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    release_id: str
    expected_reference_records: int = Field(gt=0)
    expected_rejected_records: int = Field(ge=0)
    datasets: list[USDAReleaseDataset] = Field(min_length=1, max_length=4)

    @field_validator("release_id")
    @classmethod
    def validate_release_id(cls, value: str) -> str:
        if _DATASET_ID_PATTERN.fullmatch(value) is None:
            raise ValueError("release id must be a lowercase ASCII slug")
        return value

    @model_validator(mode="after")
    def validate_totals(self) -> USDAReferenceRelease:
        dataset_ids = [dataset.id for dataset in self.datasets]
        filenames = [dataset.filename for dataset in self.datasets]
        data_types = [dataset.data_type for dataset in self.datasets]
        if len(set(dataset_ids)) != len(dataset_ids):
            raise ValueError("dataset ids must be unique")
        if len(set(filenames)) != len(filenames):
            raise ValueError("dataset filenames must be unique")
        if len(set(data_types)) != len(data_types):
            raise ValueError("dataset data types must be unique")
        if sum(dataset.rows_accepted for dataset in self.datasets) != (
            self.expected_reference_records
        ):
            raise ValueError("dataset accepted rows must equal expected reference records")
        if sum(dataset.rows_rejected for dataset in self.datasets) != (
            self.expected_rejected_records
        ):
            raise ValueError("dataset rejected rows must equal expected rejected records")
        return self


@dataclass(frozen=True)
class VerifiedDataset:
    specification: USDAReleaseDataset
    path: Path


def _default_manifest_path() -> Path:
    packaged = files("opennosh_api").joinpath(_DEFAULT_MANIFEST_NAME)
    if packaged.is_file():
        return Path(str(packaged))
    return Path(__file__).resolve().parents[3] / "config" / _DEFAULT_MANIFEST_NAME


def load_release_manifest(path: str | Path | None = None) -> USDAReferenceRelease:
    resolved = Path(path) if path is not None else _default_manifest_path()
    payload: Any = json.loads(resolved.read_text(encoding="utf-8"))
    return USDAReferenceRelease.model_validate(payload)


def _verify_file(path: Path, dataset: USDAReleaseDataset) -> None:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(_DOWNLOAD_CHUNK_BYTES):
            size += len(chunk)
            if size > dataset.size_bytes:
                raise ValueError(f"{dataset.id}: downloaded file exceeds the pinned size")
            digest.update(chunk)
    if size != dataset.size_bytes:
        raise ValueError(
            f"{dataset.id}: downloaded size {size} does not match {dataset.size_bytes}"
        )
    if digest.hexdigest() != dataset.sha256:
        raise ValueError(f"{dataset.id}: downloaded SHA-256 does not match the release manifest")


def _download_dataset(dataset: USDAReleaseDataset, destination: Path) -> Path:
    path = destination / dataset.filename
    request = Request(dataset.url, headers={"User-Agent": "OpenNosh USDA release importer"})
    with urlopen(request, timeout=60) as response, path.open("xb") as output:  # noqa: S310
        final_url = response.geturl()
        if not _is_plain_usda_https_url(final_url):
            raise ValueError(f"{dataset.id}: download redirected outside USDA")
        size = 0
        while chunk := response.read(_DOWNLOAD_CHUNK_BYTES):
            size += len(chunk)
            if size > dataset.size_bytes:
                raise ValueError(f"{dataset.id}: downloaded file exceeds the pinned size")
            output.write(chunk)
    _verify_file(path, dataset)
    return path


def resolve_release_files(
    release: USDAReferenceRelease,
    *,
    source_directory: Path | None = None,
    download_directory: Path,
) -> list[VerifiedDataset]:
    verified: list[VerifiedDataset] = []
    for dataset in release.datasets:
        path = (
            source_directory / dataset.filename
            if source_directory is not None
            else _download_dataset(dataset, download_directory)
        )
        _verify_file(path, dataset)
        verified.append(VerifiedDataset(specification=dataset, path=path))
    return verified


def inspect_release(
    release: USDAReferenceRelease,
    datasets: Sequence[VerifiedDataset],
) -> dict[str, int]:
    accepted_ids: set[str] = set()
    total_seen = 0
    total_accepted = 0
    total_rejected = 0
    for verified in datasets:
        specification = verified.specification
        seen = 0
        accepted = 0
        rejected = 0
        for outcome in iter_usda(
            verified.path,
            allowed_data_types=(specification.data_type,),
        ):
            seen += 1
            if outcome.issue is not None:
                rejected += 1
                continue
            if outcome.record is None:  # pragma: no cover - dataclass invariant
                raise AssertionError("USDA parse outcome did not contain a record")
            if outcome.record.fdc_id in accepted_ids:
                raise ValueError(f"duplicate accepted FDC id {outcome.record.fdc_id}")
            accepted_ids.add(outcome.record.fdc_id)
            accepted += 1
        observed = (seen, accepted, rejected)
        expected = (
            specification.rows_seen,
            specification.rows_accepted,
            specification.rows_rejected,
        )
        if observed != expected:
            raise ValueError(
                f"{specification.id}: parsed rows {observed} do not match pinned rows {expected}"
            )
        total_seen += seen
        total_accepted += accepted
        total_rejected += rejected
    if total_accepted != release.expected_reference_records:
        raise ValueError("accepted record total does not match the release manifest")
    if total_rejected != release.expected_rejected_records:
        raise ValueError("rejected record total does not match the release manifest")
    return {
        "rows_seen": total_seen,
        "rows_accepted": total_accepted,
        "rows_rejected": total_rejected,
    }


async def _apply_release(
    session: AsyncSession,
    release: USDAReferenceRelease,
    datasets: Sequence[VerifiedDataset],
) -> dict[str, int]:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext('opennosh.food-search-projection'))")
    )
    inserted = 0
    updated = 0
    skipped_stale = 0
    for verified in datasets:
        report: USDAImportReport = await import_usda(
            session,
            (verified.path,),
            allowed_data_types=(verified.specification.data_type,),
        )
        expected = verified.specification
        if (
            report.rows_seen != expected.rows_seen
            or report.rows_rejected != expected.rows_rejected
            or report.rows_seen - report.rows_rejected != expected.rows_accepted
        ):
            raise ValueError(f"{expected.id}: import accounting drifted after validation")
        inserted += report.rows_inserted
        updated += report.rows_updated
        skipped_stale += report.rows_skipped_stale

    reference_records = int(
        (
            await session.execute(text("SELECT COUNT(*) FROM foods_reference"))
        ).scalar_one()
    )
    if reference_records != release.expected_reference_records:
        raise ValueError(
            "foods_reference contains records outside the pinned release; refusing to delete them"
        )
    invalidation = await session.execute(
        text(
            """
            UPDATE food_search_snapshots
            SET created_at = TIMESTAMPTZ '1970-01-01 00:00:00+00'
            WHERE selected_pack_ids = '[]'::jsonb
              AND created_at > TIMESTAMPTZ '1970-01-01 00:00:00+00'
            """
        )
    )
    return {
        "reference_records": reference_records,
        "rows_inserted": inserted,
        "rows_updated": updated,
        "rows_skipped_stale": skipped_stale,
        "search_snapshots_invalidated": max(int(getattr(invalidation, "rowcount", 0) or 0), 0),
    }


async def apply_release(
    release: USDAReferenceRelease,
    datasets: Sequence[VerifiedDataset],
    *,
    database_url: str,
) -> dict[str, int]:
    engine = build_administration_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session, session.begin():
            return await _apply_release(session, release, datasets)
    finally:
        await engine.dispose()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--source-directory", type=Path, default=None)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _run(arguments: argparse.Namespace) -> int:
    release = load_release_manifest(arguments.manifest)
    with TemporaryDirectory(prefix="opennosh-usda-release-") as directory:
        datasets = resolve_release_files(
            release,
            source_directory=arguments.source_directory,
            download_directory=Path(directory),
        )
        inspection = inspect_release(release, datasets)
        result: dict[str, object] = {
            "schema_version": "1.0",
            "release_id": release.release_id,
            "status": "verified" if arguments.dry_run else "applied",
            **inspection,
            "datasets": [
                {
                    "id": verified.specification.id,
                    "sha256": verified.specification.sha256,
                }
                for verified in datasets
            ],
        }
        if not arguments.dry_run:
            settings = get_settings()
            database_url = arguments.database_url or settings.process_database_url(
                JobRole.ADMINISTRATION
            )
            result.update(
                asyncio.run(apply_release(release, datasets, database_url=database_url))
            )
    print(json.dumps(result, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return _run(_build_parser().parse_args(argv))
    except (OSError, ValueError) as error:
        print(f"USDA reference release failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
