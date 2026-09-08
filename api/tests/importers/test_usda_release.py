from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from zipfile import ZipFile

import opennosh_api.importers.usda_release as release_module
import pytest
from opennosh_api.capacity import JobRole
from opennosh_api.importers.usda import USDAImportReport
from opennosh_api.importers.usda_release import (
    USDAReferenceRelease,
    VerifiedDataset,
    _apply_release,
    apply_release,
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
        "https://user" + "@fdc.nal.usda.gov/fdc-datasets/foundation.zip",
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


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("id", "Foundation Fixture", "lowercase ASCII slug"),
        ("filename", "../foundation.zip", "bounded ZIP basename"),
        ("sha256", "A" * 64, "lowercase hexadecimal"),
    ),
)
def test_release_manifest_rejects_unsafe_dataset_identity_fields(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    archive = _archive(tmp_path)
    payload = json.loads(_release(archive).model_dump_json())
    payload["datasets"][0][field] = value

    with pytest.raises(ValueError, match=message):
        USDAReferenceRelease.model_validate(payload)


def test_release_manifest_rejects_unsafe_release_id(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    payload = json.loads(_release(archive).model_dump_json())
    payload["release_id"] = "Fixture Release"

    with pytest.raises(ValueError, match="release id must be"):
        USDAReferenceRelease.model_validate(payload)


@pytest.mark.parametrize(
    ("duplicate_field", "message"),
    (
        ("id", "dataset ids must be unique"),
        ("filename", "dataset filenames must be unique"),
        ("data_type", "dataset data types must be unique"),
    ),
)
def test_release_manifest_rejects_duplicate_dataset_identity(
    tmp_path: Path,
    duplicate_field: str,
    message: str,
) -> None:
    archive = _archive(tmp_path)
    payload = json.loads(_release(archive).model_dump_json())
    first = payload["datasets"][0]
    second = {
        **first,
        "id": "legacy-fixture",
        "data_type": "SR Legacy",
        "filename": "legacy.zip",
        "url": "https://fdc.nal.usda.gov/fdc-datasets/legacy.zip",
    }
    second[duplicate_field] = first[duplicate_field]
    payload["datasets"].append(second)
    payload["expected_reference_records"] = 2
    payload["expected_rejected_records"] = 4

    with pytest.raises(ValueError, match=message):
        USDAReferenceRelease.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("expected_reference_records", 2, "accepted rows"),
        ("expected_rejected_records", 3, "rejected rows"),
    ),
)
def test_release_manifest_rejects_inconsistent_release_totals(
    tmp_path: Path,
    field: str,
    value: int,
    message: str,
) -> None:
    archive = _archive(tmp_path)
    payload = json.loads(_release(archive).model_dump_json())
    payload[field] = value

    with pytest.raises(ValueError, match=message):
        USDAReferenceRelease.model_validate(payload)


def test_default_release_manifest_loads_from_the_package() -> None:
    release = release_module.load_release_manifest()

    assert release.release_id == (
        "usda-foundation-2026-04-30-fndds-2024-10-31-and-sr-legacy-2018-04"
    )
    assert release.expected_reference_records == 13_497


class _DownloadResponse:
    def __init__(self, payload: bytes, *, final_url: str) -> None:
        self._chunks = [payload[:17], payload[17:], b""]
        self._final_url = final_url

    def __enter__(self) -> _DownloadResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def geturl(self) -> str:
        return self._final_url

    def read(self, _size: int) -> bytes:
        return self._chunks.pop(0)


def test_release_download_is_streamed_and_verified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = _archive(tmp_path)
    release = _release(archive)
    payload = archive.read_bytes()
    dataset = release.datasets[0]
    monkeypatch.setattr(
        release_module,
        "urlopen",
        lambda *_args, **_kwargs: _DownloadResponse(payload, final_url=dataset.url),
    )
    download_directory = tmp_path / "download"
    download_directory.mkdir()

    datasets = resolve_release_files(release, download_directory=download_directory)

    assert datasets[0].path.read_bytes() == payload


def test_release_download_rejects_non_usda_redirect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = _archive(tmp_path)
    release = _release(archive)
    monkeypatch.setattr(
        release_module,
        "urlopen",
        lambda *_args, **_kwargs: _DownloadResponse(
            archive.read_bytes(),
            final_url="https://example.com/foundation.zip",
        ),
    )
    download_directory = tmp_path / "redirect"
    download_directory.mkdir()

    with pytest.raises(ValueError, match="redirected outside USDA"):
        resolve_release_files(release, download_directory=download_directory)


def test_release_rejects_truncated_file(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    release = _release(archive)
    archive.write_bytes(archive.read_bytes()[:-1])

    with pytest.raises(ValueError, match="downloaded size"):
        resolve_release_files(
            release,
            source_directory=tmp_path,
            download_directory=tmp_path / "unused",
        )


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

    result = asyncio.run(_apply_release(cast(Any, session), release, datasets))

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
        asyncio.run(_apply_release(cast(Any, unexpected), release, datasets))
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
        asyncio.run(_apply_release(cast(Any, session), release, datasets))

    assert len(session.queries) == 1
    assert "pg_advisory_xact_lock" in session.queries[0]


def _two_dataset_release(archive: Path) -> USDAReferenceRelease:
    payload = json.loads(_release(archive).model_dump_json())
    first = payload["datasets"][0]
    first.update(rows_seen=1, rows_accepted=1, rows_rejected=0)
    payload["datasets"].append(
        {
            **first,
            "id": "legacy-fixture",
            "data_type": "SR Legacy",
            "filename": "legacy.zip",
            "url": "https://fdc.nal.usda.gov/fdc-datasets/legacy.zip",
        }
    )
    payload["expected_reference_records"] = 2
    payload["expected_rejected_records"] = 0
    return USDAReferenceRelease.model_validate(payload)


def test_inspection_rejects_duplicate_accepted_ids_across_datasets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = _archive(tmp_path)
    release = _two_dataset_release(archive)
    outcome = SimpleNamespace(issue=None, record=SimpleNamespace(fdc_id="same-id"))
    monkeypatch.setattr(release_module, "iter_usda", lambda *_args, **_kwargs: [outcome])
    datasets = [
        VerifiedDataset(specification=dataset, path=tmp_path / dataset.filename)
        for dataset in release.datasets
    ]

    with pytest.raises(ValueError, match="duplicate accepted FDC id"):
        inspect_release(release, datasets)


def test_inspection_rejects_dataset_and_release_total_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = _archive(tmp_path)
    release = _release(archive)
    outcome = SimpleNamespace(issue=None, record=SimpleNamespace(fdc_id="one-id"))
    monkeypatch.setattr(release_module, "iter_usda", lambda *_args, **_kwargs: [outcome])
    datasets = [VerifiedDataset(specification=release.datasets[0], path=archive)]

    with pytest.raises(ValueError, match="parsed rows"):
        inspect_release(release, datasets)

    exact_dataset = release.datasets[0].model_copy(
        update={"rows_seen": 1, "rows_accepted": 1, "rows_rejected": 0}
    )
    exact_datasets = [VerifiedDataset(specification=exact_dataset, path=archive)]
    accepted_drift = release.model_copy(
        update={"expected_reference_records": 2, "expected_rejected_records": 0}
    )
    with pytest.raises(ValueError, match="accepted record total"):
        inspect_release(accepted_drift, exact_datasets)

    rejected_drift = release.model_copy(
        update={"expected_reference_records": 1, "expected_rejected_records": 1}
    )
    with pytest.raises(ValueError, match="rejected record total"):
        inspect_release(rejected_drift, exact_datasets)


class _AsyncContext:
    def __init__(self, value: object) -> None:
        self.value = value

    async def __aenter__(self) -> object:
        return self.value

    async def __aexit__(self, *_args: object) -> None:
        return None


class _AdministrationSession:
    def begin(self) -> _AsyncContext:
        return _AsyncContext(None)


class _AdministrationEngine:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


def test_apply_release_owns_transaction_and_disposes_engine(
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
    engine = _AdministrationEngine()
    session = _AdministrationSession()
    monkeypatch.setattr(release_module, "build_administration_engine", lambda _url: engine)
    monkeypatch.setattr(
        release_module,
        "async_sessionmaker",
        lambda *_args, **_kwargs: lambda: _AsyncContext(session),
    )

    async def apply_fixture(*_args: object, **_kwargs: object) -> dict[str, int]:
        return {"reference_records": 1}

    monkeypatch.setattr(release_module, "_apply_release", apply_fixture)

    result = asyncio.run(
        apply_release(release, datasets, database_url="postgresql+asyncpg://example.invalid/db")
    )

    assert result == {"reference_records": 1}
    assert engine.disposed is True


def _write_manifest(tmp_path: Path, release: USDAReferenceRelease) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(release.model_dump_json(), encoding="utf-8")
    return path


def test_cli_dry_run_reports_verified_release(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    archive = _archive(tmp_path)
    manifest = _write_manifest(tmp_path, _release(archive))

    status = release_module.main(
        [
            "--manifest",
            str(manifest),
            "--source-directory",
            str(tmp_path),
            "--dry-run",
        ]
    )

    assert status == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "verified"
    assert report["rows_accepted"] == 1
    assert report["datasets"] == [
        {
            "id": "foundation-fixture",
            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        }
    ]


def test_cli_apply_uses_administration_database_and_reports_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    archive = _archive(tmp_path)
    manifest = _write_manifest(tmp_path, _release(archive))
    requested_roles: list[object] = []

    class _Settings:
        def process_database_url(self, role: object) -> str:
            requested_roles.append(role)
            return "postgresql+asyncpg://example.invalid/db"

    async def apply_fixture(
        _release: USDAReferenceRelease,
        _datasets: object,
        *,
        database_url: str,
    ) -> dict[str, int]:
        assert database_url == "postgresql+asyncpg://example.invalid/db"
        return {"reference_records": 1, "search_snapshots_invalidated": 2}

    monkeypatch.setattr(release_module, "get_settings", lambda: _Settings())
    monkeypatch.setattr(release_module, "apply_release", apply_fixture)

    status = release_module.main(["--manifest", str(manifest), "--source-directory", str(tmp_path)])

    assert status == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "applied"
    assert report["reference_records"] == 1
    assert requested_roles == [JobRole.ADMINISTRATION]


def test_cli_reports_manifest_errors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    status = release_module.main(["--manifest", str(tmp_path / "missing.json"), "--dry-run"])

    assert status == 2
    assert "USDA reference release failed" in capsys.readouterr().err
