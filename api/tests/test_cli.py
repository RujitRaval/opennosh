from __future__ import annotations

import argparse
from pathlib import Path

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
