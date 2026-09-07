from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import opennosh_api.importers.usda_release as release_module
import pytest
from opennosh_api.importers.usda import USDAImportReport
from opennosh_api.importers.usda_release import (
    USDAReferenceRelease,
    _apply_release,
    inspect_release,
    resolve_release_files,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "usda"


def _archive(tmp_path: Path) -> Path:
    archive = tmp_path / "foundation.zip"
    with ZipFile(archive, "w") as output:
        output.write(FIXTURES / "foundation.json", "foundation.json")
    return archive


def _release(archive: Path, **overrides: Any) -> USDAReferenceRelease:
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "release_id": "fixture-release",
        "expected_reference_records": 1,
        "expected_rejected_records": 2,
        "datasets": [
            {
                "id": "foundation-fixture",
                "data_type": "Foundation",
                "filename": archive.name,
                "url": f"https://fdc.nal.usda.gov/fdc-datasets/{archive.name}",
                "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "size_bytes": archive.stat().st_size,
                "rows_seen": 3,
                "rows_accepted": 1,
                "rows_rejected": 2,
            }
        ],
    }
    payload.update(overrides)
    return USDAReferenceRelease.model_validate(payload)


def test_release_files_are_checksum_verified_and_exactly_accounted(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    release = _release(archive)

    datasets = resolve_release_files(
        release,
        source_directory=tmp_path,
        download_directory=tmp_path / "unused",
    )

    assert inspect_release(release, datasets) == {
        "rows_seen": 3,
        "rows_accepted": 1,
        "rows_rejected": 2,
    }


def test_release_rejects_tampered_bytes_before_parsing(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    release = _release(archive)
    archive.write_bytes(archive.read_bytes() + b"tampered")

    with pytest.raises(ValueError, match="pinned size"):
        resolve_release_files(
            release,
            source_directory=tmp_path,
            download_directory=tmp_path / "unused",
        )


def test_release_rejects_same_size_checksum_tampering_before_parsing(
    tmp_path: Path,
) -> None:
    archive = _archive(tmp_path)
    release = _release(archive)
    tampered = bytearray(archive.read_bytes())
    tampered[-1] ^= 1
    archive.write_bytes(tampered)

    with pytest.raises(ValueError, match="SHA-256"):
        resolve_release_files(
            release,
            source_directory=tmp_path,
            download_directory=tmp_path / "unused",
        )


@pytest.mark.parametrize(
    "unsafe_url",
    (
        "https://example.com/foundation.zip",
        "http://fdc.nal.usda.gov/fdc-datasets/foundation.zip",
        "https://fdc.nal.usda.gov:444/fdc-datasets/foundation.zip",
        "https://user@fdc.nal.usda.gov/fdc-datasets/foundation.zip",
    ),
)
def test_release_manifest_rejects_non_usda_urls(
    tmp_path: Path,
    unsafe_url: str,
) -> None:
    archive = _archive(tmp_path)
    release = _release(archive)
    payload = json.loads(release.model_dump_json())
    payload["datasets"][0]["url"] = unsafe_url
    with pytest.raises(ValueError, match="USDA FoodData Central"):
        USDAReferenceRelease.model_validate(payload)


def test_release_manifest_rejects_bad_accounting(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    release = _release(archive)
    payload = json.loads(release.model_dump_json())
    payload["datasets"][0]["rows_rejected"] = 1
    with pytest.raises(ValueError, match="accepted plus rejected"):
        USDAReferenceRelease.model_validate(payload)


class _Result:
    def __init__(self, *, scalar: int | None = None, rowcount: int | None = None) -> None:
        self._scalar = scalar
        self.rowcount = rowcount

    def scalar_one(self) -> int:
        assert self._scalar is not None
        return self._scalar


class _Session:
    def __init__(self, reference_records: int) -> None:
        self.reference_records = reference_records
        self.queries: list[str] = []

    async def execute(self, statement: object) -> _Result:
        query = str(statement)
        self.queries.append(query)
        if "COUNT(*) FROM foods_reference" in query:
            return _Result(scalar=self.reference_records)
        return _Result(rowcount=3)


def test_apply_is_atomic_about_exact_table_count_and_invalidates_retained_search(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = _archive(tmp_path)
    release = _release(archive)
    datasets = resolve_release_files(
        release,
        source_directory=tmp_path,
        download_directory=tmp_path / "unused",
    )

    async def import_fixture(*_args: object, **_kwargs: object) -> USDAImportReport:
        return USDAImportReport(
            rows_seen=3,
            rows_written=1,
            rows_inserted=1,
            rejected_count=2,
        )

    monkeypatch.setattr(release_module, "import_usda", import_fixture)
    session = _Session(reference_records=1)

    result = asyncio.run(_apply_release(session, release, datasets))

    assert result == {
        "reference_records": 1,
        "rows_inserted": 1,
        "rows_updated": 0,
        "rows_skipped_stale": 0,
        "search_snapshots_invalidated": 3,
    }
    assert "pg_advisory_xact_lock" in session.queries[0]
    assert "UPDATE food_search_snapshots" in session.queries[-1]

    unexpected = _Session(reference_records=2)
    with pytest.raises(ValueError, match="refusing to delete"):
        asyncio.run(_apply_release(unexpected, release, datasets))
    assert len(unexpected.queries) == 2


def test_apply_rejects_import_accounting_drift_before_count_or_invalidation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = _archive(tmp_path)
    release = _release(archive)
    datasets = resolve_release_files(
        release,
        source_directory=tmp_path,
        download_directory=tmp_path / "unused",
    )

    async def import_drifted(*_args: object, **_kwargs: object) -> USDAImportReport:
        return USDAImportReport(
            rows_seen=3,
            rows_written=1,
            rows_inserted=1,
            rejected_count=1,
        )

    monkeypatch.setattr(release_module, "import_usda", import_drifted)
    session = _Session(reference_records=1)

    with pytest.raises(ValueError, match="accounting drifted"):
        asyncio.run(_apply_release(session, release, datasets))

    assert len(session.queries) == 1
    assert "pg_advisory_xact_lock" in session.queries[0]
