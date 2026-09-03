"""Public-read CLI backed exclusively by the supported Python SDK."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from opennosh_api.foods.cursors import SEARCH_CURSOR_MAX_LENGTH
from opennosh_api.foods.service import (
    SEARCH_LIMIT_MAX,
    normalize_locale,
    normalize_pack_ids,
    normalize_search_query,
)
from opennosh_api.sdk.client import OpenNoshClient, OpenNoshProblem, OpenNoshResponse
from opennosh_api.sdk.packs import validate_pack_input

TARGET_ENVIRONMENT_VARIABLE = "OPENNOSH_TARGET"
_RELEASE_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$")
_UPSTREAM_PROBLEM_CODES = {
    "network_error",
    "request_timeout",
    "redirect_refused",
    "response_too_large",
    "unexpected_response",
    "rate_limited",
    "upstream_unavailable",
    "service_unavailable",
    "internal_error",
}


def _common(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--target",
        help=(
            "OpenNosh target: hosted or one exact HTTPS origin; defaults to "
            "$OPENNOSH_TARGET, then hosted"
        ),
    )
    command.add_argument("--json", action="store_true", help="Print stable compact JSON")


def add_public_parser(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    public = commands.add_parser("public", help="Read verified public OpenNosh data")
    actions = public.add_subparsers(dest="public_command", required=True)

    capabilities = actions.add_parser("capabilities", help="Inspect public food capabilities")
    _common(capabilities)

    search = actions.add_parser("search", help="Search public foods")
    search.add_argument("query")
    search.add_argument("--locale")
    search.add_argument("--source", choices=("usda", "community", "federation"))
    search.add_argument("--pack", action="append", default=[])
    search.add_argument("--limit", type=int)
    search.add_argument("--cursor")
    _common(search)

    food = actions.add_parser("food", help="Read the latest or an exact release food")
    food.add_argument("source", choices=("usda", "community"))
    food.add_argument("source_id")
    food.add_argument("--version")
    food.add_argument("--release-version")
    _common(food)

    missions = actions.add_parser("missions", help="Read the public mission catalog")
    missions.add_argument("--limit", type=int)
    _common(missions)

    activity = actions.add_parser("activity", help="Read privacy-safe mission activity")
    _common(activity)

    manifest = actions.add_parser("manifest", help="Read an exact signed release manifest")
    manifest.add_argument("release_version")
    _common(manifest)

    provenance = actions.add_parser("provenance", help="Read exact release provenance HTML")
    provenance.add_argument("release_version")
    provenance.add_argument("source", choices=("usda", "community"))
    provenance.add_argument("source_id")
    _common(provenance)

    download = actions.add_parser("download-pack", help="Download one exact immutable pack")
    download.add_argument("release_version")
    download.add_argument("pack_id")
    download.add_argument("pack_version")
    download.add_argument("--output", type=Path, required=True)
    _common(download)


def add_packs_parser(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    packs = commands.add_parser("packs", help="Inspect and validate OpenNosh food packs")
    actions = packs.add_subparsers(dest="packs_command", required=True)
    validate = actions.add_parser("validate", help="Validate one JSON or ZIP food pack")
    validate.add_argument("input", type=Path)
    validate.add_argument("--json", action="store_true", help="Print stable compact JSON")


def _json_value(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


def _response_payload(response: OpenNoshResponse[Any]) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "status": response.status,
        "data": _json_value(response.data),
        "cache": {
            "etag": response.etag,
            "last_modified": response.last_modified,
            "cache_control": response.cache_control,
        },
        "content_type": response.content_type,
        "url": response.url,
        "release_version": response.release_version,
        "release_state": response.release_state,
        "stale_age_seconds": response.stale_age_seconds,
        "warning": response.warning,
    }


def _find_metadata(value: object, names: tuple[str, ...]) -> dict[str, object]:
    found: dict[str, object] = {}

    def visit(item: object) -> None:
        if isinstance(item, BaseModel):
            visit(item.model_dump(mode="json"))
        elif isinstance(item, Mapping):
            for key, nested in item.items():
                if key in names and key not in found and nested is not None:
                    found[str(key)] = nested
                visit(nested)
        elif isinstance(item, list | tuple):
            for nested in item:
                visit(nested)

    visit(value)
    return found


def _print_human(response: OpenNoshResponse[Any]) -> None:
    metadata = _find_metadata(
        response.data,
        (
            "state",
            "verification",
            "release_version",
            "source",
            "source_id",
            "license",
            "attribution",
            "immutable_url",
            "provenance_url",
        ),
    )
    print(f"OpenNosh public response: HTTP {response.status}")
    for name, release_value in (
        ("release_version", response.release_version),
        ("release_state", response.release_state),
        ("stale_age_seconds", response.stale_age_seconds),
        ("warning", response.warning),
    ):
        if release_value is not None:
            print(f"{name}: {_terminal_text(str(release_value))}")
    for name in sorted(metadata):
        value = metadata[name]
        if isinstance(value, Mapping):
            value = json.dumps(_json_value(value), sort_keys=True, separators=(",", ":"))
        print(f"{name}: {_terminal_text(str(value))}")
    if isinstance(response.data, str):
        print(json.dumps(response.data, ensure_ascii=True))
    else:
        print(json.dumps(_json_value(response.data), indent=2, sort_keys=True))


def _terminal_text(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)[1:-1]


def _write_new_file(output: Path, data: bytes) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary_path, output)
    except FileExistsError:
        raise ValueError("download output already exists") from None
    except OSError as error:
        raise ValueError("download output could not be written") from error
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def _target(arguments: argparse.Namespace) -> str:
    return arguments.target or os.environ.get(TARGET_ENVIRONMENT_VARIABLE) or "hosted"


def _invoke(arguments: argparse.Namespace, client: OpenNoshClient) -> OpenNoshResponse[Any]:
    command = arguments.public_command
    if command == "capabilities":
        return client.capabilities()
    if command == "search":
        query = normalize_search_query(arguments.query)
        locale = normalize_locale(arguments.locale)
        packs = list(normalize_pack_ids(arguments.pack))
        if arguments.limit is not None and not 1 <= arguments.limit <= SEARCH_LIMIT_MAX:
            raise ValueError("limit is outside the public search range")
        if arguments.cursor is not None and len(arguments.cursor) > SEARCH_CURSOR_MAX_LENGTH:
            raise ValueError("cursor is outside the public search range")
        return client.search_foods(
            query,
            locale=locale,
            source=arguments.source,
            packs=packs,
            limit=arguments.limit,
            cursor=arguments.cursor,
        )
    if command == "food":
        if arguments.version is not None and _RELEASE_VERSION.fullmatch(arguments.version) is None:
            raise ValueError("version must be a four-part release version")
        if arguments.release_version:
            if arguments.version:
                raise ValueError("--version cannot be combined with --release-version")
            return client.get_release_food(
                arguments.release_version, arguments.source, arguments.source_id
            )
        return client.get_public_food(
            arguments.source, arguments.source_id, version=arguments.version
        )
    if command == "missions":
        if arguments.limit is not None and not 1 <= arguments.limit <= 100:
            raise ValueError("limit is outside the public mission range")
        return client.list_missions(limit=arguments.limit)
    if command == "activity":
        return client.get_mission_activity()
    if command == "manifest":
        return client.get_release_manifest(arguments.release_version)
    if command == "provenance":
        return client.get_provenance(
            arguments.release_version, arguments.source, arguments.source_id
        )
    if command == "download-pack":
        return client.download_pack(
            arguments.release_version, arguments.pack_id, arguments.pack_version
        )
    raise AssertionError(f"unsupported public command: {command}")


def _problem_payload(problem: OpenNoshProblem) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "status": problem.status,
        "code": problem.code,
        "detail": problem.detail,
        "request_reference": problem.request_reference,
        "recovery_actions": [action.model_dump(mode="json") for action in problem.recovery_actions],
        "retry_after_seconds": problem.retry_after_seconds,
    }


def _proof_is_unavailable(value: object) -> bool:
    if isinstance(value, BaseModel):
        return _proof_is_unavailable(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        if value.get("state") == "unavailable":
            return True
        return any(_proof_is_unavailable(nested) for nested in value.values())
    if isinstance(value, list | tuple):
        return any(_proof_is_unavailable(nested) for nested in value)
    return False


def run_public_command(arguments: argparse.Namespace) -> int:
    try:
        response = _invoke(arguments, OpenNoshClient(_target(arguments)))
        if arguments.public_command == "download-pack":
            output: Path = arguments.output
            data = bytes(response.data)
            _write_new_file(output, data)
            payload: dict[str, object] = {
                "schema_version": "1.0",
                "status": response.status,
                "output": str(output),
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "content_type": response.content_type,
                "url": response.url,
                "release_version": response.release_version,
                "release_state": response.release_state,
                "stale_age_seconds": response.stale_age_seconds,
                "warning": response.warning,
            }
        else:
            payload = _response_payload(response)
    except (TypeError, ValueError):
        if arguments.json:
            print(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "status": 0,
                        "code": "invalid_input",
                        "detail": "invalid input or target",
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        else:
            print("OpenNosh public command failed: invalid input or target", file=sys.stderr)
        return 2
    except OpenNoshProblem as problem:
        if arguments.json:
            print(json.dumps(_problem_payload(problem), sort_keys=True, separators=(",", ":")))
        else:
            print(
                f"OpenNosh public read failed: {_terminal_text(problem.code)}: "
                f"{_terminal_text(problem.detail)}",
                file=sys.stderr,
            )
        if problem.status == 0 or problem.status >= 500 or problem.code in _UPSTREAM_PROBLEM_CODES:
            return 4
        return 3
    if arguments.json:
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    elif arguments.public_command == "download-pack":
        print(
            f"OpenNosh pack downloaded: {_terminal_text(str(payload['output']))} "
            f"({payload['bytes']} bytes, sha256 {payload['sha256']})"
        )
        if response.release_version is not None:
            print(f"release_version: {_terminal_text(response.release_version)}")
        if response.release_state is not None:
            print(f"release_state: {_terminal_text(response.release_state)}")
    else:
        _print_human(response)
    return 3 if _proof_is_unavailable(response.data) else 0


def run_packs_command(arguments: argparse.Namespace) -> int:
    if arguments.packs_command != "validate":
        raise AssertionError(f"unsupported packs command: {arguments.packs_command}")
    report = validate_pack_input(arguments.input)
    if arguments.json:
        print(json.dumps(report.to_dict(), sort_keys=True, separators=(",", ":")))
    else:
        print(
            "OpenNosh pack validation: "
            f"{len(report.errors)} error(s), {len(report.warnings)} warning(s)"
        )
        for issue in (*report.errors, *report.warnings):
            print(
                f"{_terminal_text(issue.severity.upper())} {_terminal_text(issue.code)} "
                f"{_terminal_text(issue.json_pointer or '/')}: "
                f"{_terminal_text(issue.message)}"
            )
    return 0 if report.valid else 2
