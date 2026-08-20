from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import opennosh_api.cli as cli_module
import opennosh_api.foodpacks.loader as loader_module
import pytest
import yaml
from opennosh_api.cli import build_parser, run_food_command
from opennosh_api.foodpacks.loader import (
    FoodPackBatchLoadReport,
    FoodPackLoadReport,
    load_food_pack_root_with_retries,
    load_food_pack_with_retries,
    prepare_food_pack,
)
from opennosh_api.foodpacks.validation import FoodPackLoadError
from sqlalchemy.exc import DBAPIError

FIXTURE = Path(__file__).parent / "fixtures" / "valid" / "balanced-pack"


def _copy_pack(tmp_path: Path) -> Path:
    destination = tmp_path / "balanced-pack"
    shutil.copytree(FIXTURE, destination)
    return destination


def _foods(path: Path) -> list[dict[str, object]]:
    loaded = yaml.safe_load((path / "foods" / "foods.yaml").read_text(encoding="utf-8"))
    assert isinstance(loaded, list)
    return loaded


def test_prepare_food_pack_canonicalises_volume_and_preserves_credit() -> None:
    prepared = prepare_food_pack(FIXTURE)

    assert not prepared.pack_rejected
    assert not prepared.errors
    assert len(prepared.records) == 2
    volume = prepared.records[1]
    assert volume.nutrients_json == {
        "basis": "per_100g",
        "density_g_per_ml": "1.03",
        "nutrients": {
            "carbohydrate_g": "6.7961165048543689320388349514563106796116504854369",
            "energy_kcal": "77.669902912621359223300970873786407766990291262136",
            "fat_g": "3.8834951456310679611650485436893203883495145631068",
            "protein_g": "3.8834951456310679611650485436893203883495145631068",
        },
    }
    exported = volume.export_entry()
    assert exported["contributed_by"] == "test-contributor"
    assert exported["source_uri"] == "https://example.gov/foods/lassi"
    assert exported["source_license"] == "public-domain"
    assert exported["basis"] == "per_100g"
    assert exported["density_g_per_ml"] == "1.03"


def test_prepare_food_pack_reports_one_bad_entry_and_keeps_valid_entries(
    tmp_path: Path,
) -> None:
    pack = _copy_pack(tmp_path)
    foods = _foods(pack)
    foods[1]["name"] = ""
    (pack / "foods" / "foods.yaml").write_text(
        yaml.safe_dump(foods, sort_keys=False), encoding="utf-8"
    )

    prepared = prepare_food_pack(pack)

    assert not prepared.pack_rejected
    assert [record.slug for record in prepared.records] == ["balanced-thepla"]
    assert any(issue.path[:2] == ("foods", 1) for issue in prepared.errors)
    assert any(issue.slug == "public-domain-lassi" for issue in prepared.errors)


def test_prepare_food_pack_rejects_invalid_manifest(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    manifest = yaml.safe_load((pack / "pack.yaml").read_text(encoding="utf-8"))
    manifest["license"] = "ODbL-1.0"
    (pack / "pack.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )

    prepared = prepare_food_pack(pack)

    assert prepared.pack_rejected
    assert not prepared.records
    assert any(issue.path == ("pack", "license") for issue in prepared.errors)


def test_cli_exposes_food_pack_loader() -> None:
    arguments = build_parser().parse_args(
        ["foods", "load", "/tmp/a-pack", "--json", "--max-attempts", "4"]
    )

    assert arguments.command == "foods"
    assert arguments.food_command == "load"
    assert arguments.path == Path("/tmp/a-pack")
    assert arguments.json is True
    assert arguments.max_attempts == 4
    assert not hasattr(arguments, "database_url")


def test_report_shape_is_json_serialisable() -> None:
    prepared = prepare_food_pack(FIXTURE)
    payload = [record.export_entry() for record in prepared.records]

    assert json.loads(json.dumps(payload))[0]["contributed_by"] == "test-contributor"


def test_batch_report_aggregates_pack_results() -> None:
    report = FoodPackBatchLoadReport(
        packs=(
            FoodPackLoadReport(entries_seen=2, entries_inserted=1, entries_unchanged=1),
            FoodPackLoadReport(entries_seen=1, entries_rejected=1),
        )
    )

    assert report.entries_seen == 3
    assert report.entries_written == 1
    assert report.entries_unchanged == 1
    assert report.entries_inserted == 1
    assert report.entries_updated == 0
    assert report.entries_skipped_stale == 0
    assert report.entries_rejected == 1
    assert report.failed
    assert len(report.to_dict()["packs"]) == 2


class _FakeTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_args: object) -> None:
        return None


class _FakeSession:
    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def begin(self) -> _FakeTransaction:
        return _FakeTransaction()


class _FakeSessionFactory:
    def __call__(self) -> _FakeSession:
        return _FakeSession()


class _OriginalDatabaseError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


def _database_error(sqlstate: str) -> DBAPIError:
    return DBAPIError("SELECT 1", {}, _OriginalDatabaseError(sqlstate), False)


def test_transaction_retry_succeeds_after_serialization_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = FoodPackLoadReport(pack_id="balanced-pack")
    load = AsyncMock(side_effect=[_database_error("40001"), expected])
    monkeypatch.setattr(loader_module, "load_food_pack", load)

    actual = asyncio.run(
        load_food_pack_with_retries(_FakeSessionFactory(), FIXTURE, max_attempts=2)  # type: ignore[arg-type]
    )

    assert actual is expected
    assert load.await_count == 2


@pytest.mark.parametrize("sqlstate", ["23505", "08006"])
def test_transaction_retry_does_not_hide_nonretryable_errors(
    monkeypatch: pytest.MonkeyPatch, sqlstate: str
) -> None:
    load = AsyncMock(side_effect=_database_error(sqlstate))
    monkeypatch.setattr(loader_module, "load_food_pack", load)

    with pytest.raises(DBAPIError):
        asyncio.run(
            load_food_pack_with_retries(_FakeSessionFactory(), FIXTURE)  # type: ignore[arg-type]
        )

    assert load.await_count == 1


def test_transaction_retry_reraises_after_final_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    load = AsyncMock(side_effect=_database_error("40P01"))
    monkeypatch.setattr(loader_module, "load_food_pack", load)

    with pytest.raises(DBAPIError):
        asyncio.run(
            load_food_pack_with_retries(
                _FakeSessionFactory(), FIXTURE, max_attempts=2  # type: ignore[arg-type]
            )
        )

    assert load.await_count == 2


def test_transaction_retry_rejects_nonpositive_attempts() -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        asyncio.run(
            load_food_pack_with_retries(
                _FakeSessionFactory(), FIXTURE, max_attempts=0  # type: ignore[arg-type]
            )
        )


def test_root_loader_discovers_multiple_packs_in_stable_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("z-pack", "a-pack"):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "pack.yaml").write_text("id: placeholder\n", encoding="utf-8")

    seen: list[str] = []

    async def fake_load(
        _factory: object, path: str | Path, *, max_attempts: int
    ) -> FoodPackLoadReport:
        seen.append(Path(path).name)
        return FoodPackLoadReport(pack_id=Path(path).name, entries_seen=max_attempts)

    monkeypatch.setattr(loader_module, "load_food_pack_with_retries", fake_load)

    report = asyncio.run(
        load_food_pack_root_with_retries(
            _FakeSessionFactory(), tmp_path, max_attempts=2  # type: ignore[arg-type]
        )
    )

    assert seen == ["a-pack", "z-pack"]
    assert [pack.pack_id for pack in report.packs] == seen
    assert report.entries_seen == 4


def test_root_loader_rejects_an_empty_directory(tmp_path: Path) -> None:
    with pytest.raises(FoodPackLoadError) as caught:
        asyncio.run(
            load_food_pack_root_with_retries(
                _FakeSessionFactory(), tmp_path  # type: ignore[arg-type]
            )
        )

    assert caught.value.code == "packs_missing"


def _cli_arguments(*, json_output: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        food_command="load",
        path=FIXTURE,
        max_attempts=3,
        json=json_output,
    )


def test_cli_reports_success_as_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    batch = FoodPackBatchLoadReport(
        packs=(FoodPackLoadReport(pack_id="balanced-pack", entries_inserted=2),)
    )
    monkeypatch.setattr(cli_module, "_run_load", AsyncMock(return_value=batch))

    assert run_food_command(_cli_arguments(json_output=True)) == 0

    captured = capsys.readouterr()
    assert json.loads(captured.out)["entries_written"] == 2
    assert captured.err == ""


def test_cli_returns_failure_after_reporting_rejected_entries(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    batch = FoodPackBatchLoadReport(
        packs=(FoodPackLoadReport(pack_id="balanced-pack", entries_rejected=1),)
    )
    monkeypatch.setattr(cli_module, "_run_load", AsyncMock(return_value=batch))

    assert run_food_command(_cli_arguments()) == 2
    assert "1 rejected" in capsys.readouterr().out


@pytest.mark.parametrize(
    "error",
    [
        FoodPackLoadError(code="manifest_missing", message="missing", path=FIXTURE),
        OSError("unreadable"),
        ValueError("invalid"),
        _database_error("08006"),
    ],
)
def test_cli_reports_handled_failures(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
) -> None:
    monkeypatch.setattr(cli_module, "_run_load", AsyncMock(side_effect=error))

    assert run_food_command(_cli_arguments()) == 2
    assert "food-pack load failed:" in capsys.readouterr().err
