from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

import opennosh_api.cli as cli
import opennosh_api.importers.wger as standalone_wger
import pytest


def test_exercise_import_command_parses_offline_paths() -> None:
    arguments = cli.build_parser().parse_args(
        ["exercises", "import-wger", "one.json", "two.json", "--batch-size", "25", "--json"]
    )

    assert arguments.command == "exercises"
    assert arguments.exercise_command == "import-wger"
    assert arguments.paths == [Path("one.json"), Path("two.json")]
    assert arguments.batch_size == 25
    assert arguments.json is True


def test_commons_build_command_requires_explicit_release_inputs() -> None:
    arguments = cli.build_parser().parse_args(
        [
            "commons",
            "build-starter-release",
            "--packs-root",
            "packs",
            "--output",
            "/secure/release",
            "--release-version",
            "0.56.0.0",
            "--published-at",
            "2026-08-27T02:00:00+00:00",
            "--source-commit",
            "a" * 40,
            "--manifest-key-id",
            "manifest-production",
            "--manifest-private-key",
            "/secure/manifest.key",
            "--receipt-key-id",
            "receipt-production",
            "--receipt-private-key",
            "/secure/receipt.key",
            "--decision-reference",
            "https://github.com/RujitRaval/opennosh/issues/97",
            "--approving-actor",
            "github:RujitRaval",
            "--json",
        ]
    )

    assert arguments.command == "commons"
    assert arguments.commons_command == "build-starter-release"
    assert arguments.release_version == "0.56.0.0"
    assert arguments.packs_root == Path("packs")
    assert arguments.json is True


def test_commons_publish_command_requires_the_reviewed_wrangler_path() -> None:
    arguments = cli.build_parser().parse_args(
        [
            "commons",
            "publish-starter-release",
            "/secure/release",
            "--inventory-sha256",
            "a" * 64,
            "--bucket",
            "opennosh-public-commons",
            "--origin-url",
            "https://commons-artifacts.opennosh.org",
            "--wrangler",
            "/approved/wrangler",
        ]
    )

    assert arguments.commons_command == "publish-starter-release"
    assert arguments.wrangler == Path("/approved/wrangler")


def test_commons_warm_command_bounds_operator_concurrency() -> None:
    arguments = cli.build_parser().parse_args(
        [
            "commons",
            "warm-live-release",
            "/secure/release",
            "--inventory-sha256",
            "a" * 64,
            "--api-origin",
            "https://opennosh.org",
            "--concurrency",
            "4",
        ]
    )

    assert arguments.commons_command == "warm-live-release"
    assert arguments.concurrency == 4


def _commons_inventory() -> SimpleNamespace:
    return SimpleNamespace(
        release_version="0.56.0.0",
        food_count=165,
        pack_count=4,
        objects=(object(), object()),
    )


def test_commons_verify_command_executes_and_reports_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    inventory = _commons_inventory()

    async def verified(_directory: Path, received: SimpleNamespace) -> None:
        assert received is inventory

    monkeypatch.setattr(cli, "load_verified_inventory", lambda *_args, **_kwargs: inventory)
    monkeypatch.setattr(cli, "verify_starter_release", verified)
    arguments = cli.build_parser().parse_args(
        [
            "commons",
            "verify-starter-release",
            "/secure/release",
            "--inventory-sha256",
            "a" * 64,
            "--json",
        ]
    )

    assert cli.run_commons_command(arguments) == 0
    output = capsys.readouterr()
    assert '"verified": true' in output.out
    assert '"object_count": 2' in output.out
    assert output.err == ""


def test_commons_publish_command_executes_the_verified_release(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    inventory = _commons_inventory()
    writer = object()

    async def verified(_directory: Path, _inventory: SimpleNamespace) -> None:
        return None

    async def published(**options: object) -> SimpleNamespace:
        assert options["inventory"] is inventory
        assert options["writer"] is writer
        return SimpleNamespace(
            release_version="0.56.0.0",
            uploaded_immutable=172,
            reused_immutable=0,
            pointer_replaced=True,
        )

    monkeypatch.setattr(cli, "load_verified_inventory", lambda *_args, **_kwargs: inventory)
    monkeypatch.setattr(cli, "verify_starter_release", verified)
    monkeypatch.setattr(cli, "WranglerR2ObjectWriter", lambda _path: writer)
    monkeypatch.setattr(cli, "publish_starter_release_to_r2", published)
    arguments = cli.build_parser().parse_args(
        [
            "commons",
            "publish-starter-release",
            "/secure/release",
            "--inventory-sha256",
            "a" * 64,
            "--bucket",
            "opennosh-public-commons",
            "--origin-url",
            "https://commons-artifacts.opennosh.org",
            "--wrangler",
            "/approved/wrangler",
            "--json",
        ]
    )

    assert cli.run_commons_command(arguments) == 0
    output = capsys.readouterr()
    assert '"uploaded_immutable": 172' in output.out
    assert '"pointer_replaced": true' in output.out
    assert output.err == ""


def test_commons_warm_command_executes_and_errors_are_controlled(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    inventory = _commons_inventory()

    async def verified(_directory: Path, _inventory: SimpleNamespace) -> None:
        return None

    async def warmed(**options: object) -> SimpleNamespace:
        assert options["concurrency"] == 4
        return SimpleNamespace(
            release_version="0.56.0.0",
            foods_warmed=165,
            provenance_warmed=165,
            packs_warmed=4,
            latest_checkpoint_advanced=True,
        )

    monkeypatch.setattr(cli, "load_verified_inventory", lambda *_args, **_kwargs: inventory)
    monkeypatch.setattr(cli, "verify_starter_release", verified)
    monkeypatch.setattr(cli, "warm_and_verify_public_api", warmed)
    argv = [
        "commons",
        "warm-live-release",
        "/secure/release",
        "--inventory-sha256",
        "a" * 64,
        "--api-origin",
        "https://opennosh.org",
        "--concurrency",
        "4",
    ]

    assert cli.main(argv) == 0
    output = capsys.readouterr()
    assert "0.56.0.0, 165 foods, 4 packs" in output.out
    assert output.err == ""

    monkeypatch.setattr(
        cli,
        "load_verified_inventory",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("inventory changed")),
    )
    assert cli.main(argv) == 2
    assert "Commons release failed: inventory changed" in capsys.readouterr().err


def test_exercise_import_command_reports_rejections(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake_import(arguments: argparse.Namespace) -> dict[str, object]:
        del arguments
        return {
            "rows_seen": 2,
            "rows_written": 1,
            "rows_inserted": 1,
            "rows_updated": 0,
            "rows_skipped_stale": 0,
            "rows_rejected": 1,
            "issues_omitted": 0,
            "issues": [
                {
                    "source_path": "fixture.json",
                    "row_number": 2,
                    "source_id": "202",
                    "message": "license is not allowlisted",
                }
            ],
        }

    monkeypatch.setattr(cli, "_run_wger_import", fake_import)
    arguments = cli.build_parser().parse_args(["exercises", "import-wger", "fixture.json"])

    assert cli.run_exercise_command(arguments) == 2
    captured = capsys.readouterr()
    assert "2 read, 1 written, 0 stale, 1 rejected" in captured.out
    assert "fixture.json:2: license is not allowlisted" in captured.err


@pytest.mark.parametrize("json_output", [False, True])
def test_exercise_import_command_reports_success(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    json_output: bool,
) -> None:
    async def fake_import(arguments: argparse.Namespace) -> dict[str, object]:
        del arguments
        return {
            "rows_seen": 2,
            "rows_written": 2,
            "rows_inserted": 2,
            "rows_updated": 0,
            "rows_skipped_stale": 0,
            "rows_rejected": 0,
            "issues_omitted": 0,
            "issues": [],
        }

    monkeypatch.setattr(cli, "_run_wger_import", fake_import)
    argv = ["exercises", "import-wger", "fixture.json"]
    if json_output:
        argv.append("--json")

    assert cli.main(argv) == 0
    captured = capsys.readouterr()
    if json_output:
        assert '"rows_written": 2' in captured.out
    else:
        assert "2 read, 2 written, 0 stale, 0 rejected" in captured.out
    assert captured.err == ""


def test_exercise_import_command_and_standalone_entrypoint_report_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def failed_import(arguments: argparse.Namespace) -> dict[str, object]:
        del arguments
        raise ValueError("bad export")

    monkeypatch.setattr(cli, "_run_wger_import", failed_import)
    assert cli.main(["exercises", "import-wger", "fixture.json"]) == 2
    assert "wger import failed: bad export" in capsys.readouterr().err

    async def failed_standalone(arguments: argparse.Namespace) -> int:
        del arguments
        raise standalone_wger.WgerFormatError("partial export")

    monkeypatch.setattr(standalone_wger, "_run_cli", failed_standalone)
    assert standalone_wger.main(["fixture.json"]) == 2
    assert "wger import failed: partial export" in capsys.readouterr().err


def test_standalone_wger_entrypoint_returns_success(monkeypatch: pytest.MonkeyPatch) -> None:
    async def successful_standalone(arguments: argparse.Namespace) -> int:
        assert arguments.paths == [Path("fixture.json")]
        return 0

    monkeypatch.setattr(standalone_wger, "_run_cli", successful_standalone)
    assert standalone_wger.main(["fixture.json"]) == 0
