from __future__ import annotations

import json
import zipfile
from pathlib import Path

import opennosh_api.sdk.cli as public_cli
import pytest
from opennosh_api.cli import build_parser, main
from opennosh_api.foodpacks.validation import ValidationIssue, ValidationReport, load_pack_directory
from opennosh_api.foods.schemas import FoodCapabilities
from opennosh_api.sdk import OpenNoshProblem, OpenNoshResponse


def response(data: object, *, content_type: str = "application/json") -> OpenNoshResponse[object]:
    return OpenNoshResponse(
        data=data,
        status=200,
        url="https://opennosh.org/api/v1/foods/capabilities",
        etag='"fixture"',
        last_modified=None,
        cache_control="public, max-age=60",
        content_type=content_type,
    )


def test_public_parser_exposes_the_locked_command_set() -> None:
    examples = {
        "capabilities": [],
        "search": ["rice"],
        "food": ["community", "rice"],
        "missions": [],
        "activity": [],
        "manifest": ["1.2.3.4"],
        "provenance": ["1.2.3.4", "community", "rice"],
        "download-pack": ["1.2.3.4", "core", "1.0.0", "--output", "pack.zip"],
    }
    for command, extra in examples.items():
        arguments = build_parser().parse_args(["public", command, *extra])
        assert arguments.public_command == command

    arguments = build_parser().parse_args(["packs", "validate", "pack.zip"])
    assert arguments.packs_command == "validate"
    arguments = build_parser().parse_args(
        ["public", "search", "rice", "--source", "federation"]
    )
    assert arguments.source == "federation"


def test_public_json_is_compact_sorted_and_uses_target_precedence(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    targets: list[str] = []

    class FakeClient:
        def __init__(self, target: str) -> None:
            targets.append(target)

        def capabilities(self) -> OpenNoshResponse[FoodCapabilities]:
            return response(
                FoodCapabilities(
                    schema_version="1.0",
                    barcode_lookup_enabled=False,
                    federation_search_enabled=False,
                )
            )

    monkeypatch.setenv("OPENNOSH_TARGET", "https://environment.example")
    monkeypatch.setattr(public_cli, "OpenNoshClient", FakeClient)

    assert main(["public", "capabilities", "--target", "https://explicit.example", "--json"]) == 0
    output = capsys.readouterr()
    assert targets == ["https://explicit.example"]
    assert output.err == ""
    assert output.out.endswith("\n") and output.out.count("\n") == 1
    payload = json.loads(output.out)
    assert payload["data"]["schema_version"] == "1.0"
    assert output.out == json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"


def test_public_human_capabilities_prints_the_response_data(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeClient:
        def __init__(self, _target: str) -> None:
            pass

        def capabilities(self) -> OpenNoshResponse[FoodCapabilities]:
            return response(
                FoodCapabilities(
                    schema_version="1.0",
                    barcode_lookup_enabled=False,
                    federation_search_enabled=False,
                )
            )

    monkeypatch.setattr(public_cli, "OpenNoshClient", FakeClient)

    assert main(["public", "capabilities"]) == 0
    output = capsys.readouterr().out
    assert "OpenNosh public response: HTTP 200" in output
    assert '"barcode_lookup_enabled": false' in output


def test_public_human_food_output_preserves_required_attribution(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    record = {
        "record": {
            "source": "community",
            "source_id": "rajma",
            "attribution": {"license": "CC0-1.0", "source": "community"},
        },
        "release": {"release_version": "1.2.3.4", "state": "verified"},
        "provenance_url": "/proof",
    }

    class FakeClient:
        def __init__(self, _target: str) -> None:
            pass

        def get_public_food(self, *_args: object, **_kwargs: object) -> OpenNoshResponse[object]:
            return response(record)

    monkeypatch.setattr(public_cli, "OpenNoshClient", FakeClient)

    assert main(["public", "food", "community", "rajma"]) == 0
    output = capsys.readouterr()
    assert "state: verified" in output.out
    assert "release_version: 1.2.3.4" in output.out
    assert "license" in output.out and "CC0-1.0" in output.out
    assert "source: community" in output.out
    assert "provenance_url: /proof" in output.out


def test_public_problem_exit_codes_and_details_are_stable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeClient:
        def __init__(self, _target: str) -> None:
            pass

        def capabilities(self) -> OpenNoshResponse[object]:
            raise OpenNoshProblem(503, "service_unavailable", "Try again later.")

    monkeypatch.setattr(public_cli, "OpenNoshClient", FakeClient)
    assert main(["public", "capabilities", "--json"]) == 4
    output = capsys.readouterr()
    assert json.loads(output.out)["code"] == "service_unavailable"
    assert output.err == ""


def test_public_human_problem_escapes_terminal_control_characters(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeClient:
        def __init__(self, _target: str) -> None:
            pass

        def capabilities(self) -> OpenNoshResponse[object]:
            raise OpenNoshProblem(404, "resource_not_found", "missing\n\x1b]8;;spoof")

    monkeypatch.setattr(public_cli, "OpenNoshClient", FakeClient)
    assert main(["public", "capabilities"]) == 3
    output = capsys.readouterr()
    assert "\x1b" not in output.err
    assert "\\n\\u001b" in output.err


@pytest.mark.parametrize(
    ("arguments", "method"),
    [
        (["public", "search", "rice", "--json"], "search_foods"),
        (
            [
                "public",
                "food",
                "community",
                "rice",
                "--release-version",
                "1.2.3.4",
                "--json",
            ],
            "get_release_food",
        ),
        (["public", "missions", "--limit", "5", "--json"], "list_missions"),
        (["public", "activity", "--json"], "get_mission_activity"),
        (["public", "manifest", "1.2.3.4", "--json"], "get_release_manifest"),
        (
            ["public", "provenance", "1.2.3.4", "community", "rice", "--json"],
            "get_provenance",
        ),
    ],
)
def test_public_commands_dispatch_through_the_sdk(
    arguments: list[str],
    method: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []

    class FakeClient:
        def __init__(self, _target: str) -> None:
            pass

        def __getattr__(self, name: str) -> object:
            def invoke(*_args: object, **_kwargs: object) -> OpenNoshResponse[object]:
                calls.append(name)
                return response({"state": "verified", "release_version": "1.2.3.4"})

            return invoke

    monkeypatch.setattr(public_cli, "OpenNoshClient", FakeClient)

    assert main(arguments) == 0
    assert calls == [method]
    assert json.loads(capsys.readouterr().out)["data"]["state"] == "verified"


def test_public_food_rejects_conflicting_version_selectors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        main(
            [
                "public",
                "food",
                "community",
                "rice",
                "--version",
                "2",
                "--release-version",
                "1.2.3.4",
            ]
        )
        == 2
    )
    assert "invalid input or target" in capsys.readouterr().err


def test_public_json_validation_failure_is_compact_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["public", "search", "x", "--json"]) == 2
    output = capsys.readouterr()
    assert output.err == ""
    payload = json.loads(output.out)
    assert payload["code"] == "invalid_input"
    assert output.out == json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"


def test_public_json_usage_failure_is_compact_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["public", "search", "--json"]) == 2
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert output.err == ""
    assert payload["code"] == "invalid_input"
    assert output.out == json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"


@pytest.mark.parametrize("limit", ["0", "101"])
def test_public_mission_limit_is_validated_locally(
    limit: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["public", "missions", "--limit", limit, "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "invalid_input"


def test_public_unavailable_proof_uses_exit_three(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeClient:
        def __init__(self, _target: str) -> None:
            pass

        def get_mission_activity(self) -> OpenNoshResponse[object]:
            return response({"state": "unavailable", "regions": []})

    monkeypatch.setattr(public_cli, "OpenNoshClient", FakeClient)
    assert main(["public", "activity", "--json"]) == 3
    assert json.loads(capsys.readouterr().out)["data"]["state"] == "unavailable"


def test_public_json_preserves_release_headers(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeClient:
        def __init__(self, _target: str) -> None:
            pass

        def get_provenance(self, *_args: object) -> OpenNoshResponse[str]:
            return OpenNoshResponse(
                data="<p>verified</p>",
                status=200,
                url="https://opennosh.org/provenance",
                etag='"fixture"',
                last_modified=None,
                cache_control="public, immutable",
                content_type="text/html",
                release_version="1.2.3.4",
                release_state="stale",
                stale_age_seconds=60,
                warning="verified stale response",
            )

    monkeypatch.setattr(public_cli, "OpenNoshClient", FakeClient)
    assert (
        main(["public", "provenance", "1.2.3.4", "community", "rice", "--json"])
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["release_version"] == "1.2.3.4"
    assert payload["release_state"] == "stale"
    assert payload["stale_age_seconds"] == 60


def test_download_pack_refuses_to_overwrite_existing_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    class FakeClient:
        def __init__(self, _target: str) -> None:
            pass

        def download_pack(self, *_args: object) -> OpenNoshResponse[bytes]:
            return response(b"PK fixture", content_type="application/zip")

    output_path = tmp_path / "pack.zip"
    output_path.write_bytes(b"keep")
    monkeypatch.setattr(public_cli, "OpenNoshClient", FakeClient)

    assert (
        main(
            [
                "public",
                "download-pack",
                "1.2.3.4",
                "core",
                "1.0.0",
                "--output",
                str(output_path),
            ]
        )
        == 2
    )
    assert output_path.read_bytes() == b"keep"
    assert "invalid input or target" in capsys.readouterr().err


def test_download_pack_human_output_escapes_path_and_prints_release_metadata(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    class FakeClient:
        def __init__(self, _target: str) -> None:
            pass

        def download_pack(self, *_args: object) -> OpenNoshResponse[bytes]:
            return OpenNoshResponse(
                data=b"PK fixture",
                status=200,
                url="https://opennosh.org/pack.zip",
                etag='"fixture"',
                last_modified=None,
                cache_control="public, immutable",
                content_type="application/zip",
                release_version="1.2.3.4",
                release_state="verified",
            )

    monkeypatch.setattr(public_cli, "OpenNoshClient", FakeClient)
    output_path = tmp_path / "pack\n\x1b[2J.zip"
    assert (
        main(
            [
                "public",
                "download-pack",
                "1.2.3.4",
                "core",
                "1.0.0",
                "--output",
                str(output_path),
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "\x1b" not in output
    assert "\\n\\u001b" in output
    assert "release_version: 1.2.3.4" in output
    assert "release_state: verified" in output


def test_download_pack_maps_an_unwritable_destination_to_exit_two(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    class FakeClient:
        def __init__(self, _target: str) -> None:
            pass

        def download_pack(self, *_args: object) -> OpenNoshResponse[bytes]:
            return response(b"PK fixture", content_type="application/zip")

    monkeypatch.setattr(public_cli, "OpenNoshClient", FakeClient)
    output_path = tmp_path / "missing" / "pack.zip"
    assert (
        main(
            [
                "public",
                "download-pack",
                "1.2.3.4",
                "core",
                "1.0.0",
                "--output",
                str(output_path),
            ]
        )
        == 2
    )
    assert not output_path.exists()
    assert "invalid input or target" in capsys.readouterr().err


def test_packs_validate_accepts_json_and_zip_with_compact_reports(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = Path(__file__).resolve().parents[3]
    source = root / "packs" / "indian-staples-north"
    document = load_pack_directory(source).document
    json_path = tmp_path / "pack.json"
    json_path.write_text(json.dumps(document), encoding="utf-8")

    assert main(["packs", "validate", str(json_path), "--json"]) == 0
    json_output = capsys.readouterr().out
    assert (
        json_output
        == json.dumps(json.loads(json_output), sort_keys=True, separators=(",", ":")) + "\n"
    )
    assert json.loads(json_output)["valid"] is True

    zip_path = tmp_path / "pack.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(source / "pack.yaml", "pack.yaml")
        archive.write(source / "foods" / "foods.yaml", "foods/foods.yaml")
    assert main(["packs", "validate", str(zip_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True


def test_packs_validate_rejects_unsafe_zip_without_extracting(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    zip_path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("pack.yaml", "id: unsafe")
        archive.writestr("foods/foods.yaml", "[]")
        archive.writestr("../escape", "blocked")

    assert main(["packs", "validate", str(zip_path), "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["valid"] is False
    assert payload["errors"][0]["code"] == "archive_invalid"
    assert not (tmp_path / "escape").exists()


def test_packs_validate_rejects_nonstandard_json_numbers(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path = tmp_path / "pack.json"
    input_path.write_text('{"pack":{},"foods":[NaN]}', encoding="utf-8")

    assert main(["packs", "validate", str(input_path), "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["errors"][0]["code"] == "json_invalid"


def test_packs_validate_rejects_symlink_input(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / "pack.json"
    target.write_text('{"pack":{},"foods":[]}', encoding="utf-8")
    link = tmp_path / "linked.json"
    link.symlink_to(target)

    assert main(["packs", "validate", str(link), "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["errors"][0]["code"] == "input_invalid"


def test_packs_validate_rejects_a_later_ambiguous_zip_end_record(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    zip_path = tmp_path / "ambiguous.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("pack.yaml", "id: ambiguous")
        archive.writestr("foods/foods.yaml", "[]")
    with zip_path.open("ab") as handle:
        handle.write(b"PK\x05\x06" + b"\x00" * 18)

    assert main(["packs", "validate", str(zip_path), "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["errors"][0]["code"] == "archive_invalid"


def test_packs_validate_human_output_escapes_terminal_control_characters(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = ValidationReport(
        errors=(
            ValidationIssue(
                severity="error",
                code="schema_invalid",
                message="bad\n\x1b]8;;message",
                path=("\x1b[2J",),
            ),
        )
    )
    monkeypatch.setattr(public_cli, "validate_pack_input", lambda _path: report)

    assert main(["packs", "validate", "pack.json"]) == 2
    output = capsys.readouterr().out
    assert "\x1b" not in output
    assert "\\u001b" in output


@pytest.mark.parametrize(
    ("name", "content", "code"),
    [
        ("pack.txt", b"{}", "input_format_unsupported"),
        ("pack.json", b"[]", "json_not_object"),
        ("pack.zip", b"not a zip", "archive_invalid"),
    ],
)
def test_packs_validate_reports_stable_input_failures(
    name: str,
    content: bytes,
    code: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path = tmp_path / name
    input_path.write_bytes(content)

    assert main(["packs", "validate", str(input_path), "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["errors"][0]["code"] == code
