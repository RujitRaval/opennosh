#!/usr/bin/env python3
"""Install shipped artifacts in empty directories and run both public-read starters."""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]

SEARCH_BODY = {
    "schema_version": "2.0",
    "items": [
        {
            "id": "community:rajma-masala",
            "source": "community",
            "source_id": "rajma-masala",
            "name": "Rajma masala",
            "name_local": None,
            "category": "Punjabi home-style preparation",
            "attribution": {
                "source": "community",
                "license": "CC0-1.0",
                "contributed_by": "Punjab Foods Collective",
            },
            "conflict": False,
            "variant_count": 1,
        }
    ],
    "limit": 1,
    "has_more": False,
    "next_cursor": None,
    "snapshot_id": "11111111-1111-4111-8111-111111111111",
    "snapshot_expires_at": "2026-09-04T21:00:00Z",
    "release_set": None,
}

DETAIL_BODY = {
    "schema_version": "1.0",
    "record": {
        "schema_version": "1.0",
        "id": "community:rajma-masala",
        "source": "community",
        "source_id": "rajma-masala",
        "name": "Rajma masala",
        "name_local": None,
        "category": "Punjabi home-style preparation",
        "attribution": {
            "source": "community",
            "license": "CC0-1.0",
            "contributed_by": "Punjab Foods Collective",
        },
        "nutrients": {"energy_kcal": "127"},
        "portions": [],
    },
    "release": {
        "release_version": "0.82.1.0",
        "published_at": "2026-09-03T02:00:00Z",
        "state": "verified",
        "stale_age_seconds": 0,
    },
    "immutable_url": "/api/v1/public/releases/0.82.1.0/foods/community/rajma-masala",
    "provenance_url": (
        "/api/v1/public/releases/0.82.1.0/foods/community/rajma-masala/provenance"
    ),
}


class FixtureServer(ThreadingHTTPServer):
    endpoint_kind: str
    requests: list[dict[str, object]]


class FixtureHandler(BaseHTTPRequestHandler):
    server: FixtureServer

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        path = urlsplit(self.path).path
        self.server.requests.append(
            {
                "authorization": self.headers.get("authorization"),
                "cookie": self.headers.get("cookie"),
                "endpoint_kind": self.server.endpoint_kind,
                "path": path,
            }
        )
        detail_body = copy.deepcopy(DETAIL_BODY)
        if self.server.endpoint_kind == "self_hosted":
            detail_body["release"]["state"] = "stale"
            detail_body["release"]["stale_age_seconds"] = 300
        bodies = {
            "/api/v1/foods/search": SEARCH_BODY,
            "/api/v1/public/foods/community/rajma-masala": detail_body,
        }
        body = bodies.get(path)
        if body is None:
            self.send_error(404)
            return
        encoded = json.dumps(body, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_arguments: object) -> None:
        return


def _run(
    command: list[str], *, cwd: Path, environment: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
        timeout=180,
    )


def _result_is_valid(output: str, *, endpoint_kind: str) -> bool:
    try:
        payload: Any = json.loads(output)
    except json.JSONDecodeError:
        return False
    return payload == {
        "schema_version": "1.0",
        "state": "stale_verified" if endpoint_kind == "self_hosted" else "verified",
        "food": {
            "attribution": "Punjab Foods Collective",
            "license": "CC0-1.0",
            "name": "Rajma masala",
            "provenance_url": (
                "/api/v1/public/releases/0.82.1.0/foods/community/"
                "rajma-masala/provenance"
            ),
            "release_version": "0.82.1.0",
            "source": "community:rajma-masala",
        },
    }


def validate_starters(npm_tarball: Path, wheel: Path) -> list[str]:
    issues: list[str] = []
    for executable in ("node", "npm", "uv"):
        if shutil.which(executable) is None:
            return [f"developer starters: required executable {executable} is unavailable"]
    if not npm_tarball.is_file() or not wheel.is_file():
        return ["developer starters: packed npm and wheel artifacts are required"]

    fixtures: list[tuple[FixtureServer, threading.Thread]] = []
    for endpoint_kind in ("hosted", "self_hosted"):
        server = FixtureServer(("127.0.0.1", 0), FixtureHandler)
        server.endpoint_kind = endpoint_kind
        server.requests = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        fixtures.append((server, thread))
    try:
        with tempfile.TemporaryDirectory(prefix="opennosh-starters-") as directory:
            temporary_root = Path(directory)
            clean_environment = {
                key: value
                for key, value in os.environ.items()
                if key not in {"PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"}
            }
            clean_environment.update(
                {
                    "OPENNOSH_QUERY": "rajma",
                    "npm_config_cache": str(temporary_root / "npm-cache"),
                }
            )

            javascript = temporary_root / "javascript"
            shutil.copytree(ROOT / "examples/javascript-public-read", javascript)
            installed = _run(
                ["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund", str(npm_tarball)],
                cwd=javascript,
                environment=clean_environment,
            )
            if installed.returncode != 0:
                issues.append("JavaScript starter: clean npm install failed")
            else:
                resolved = _run(
                    [
                        "node",
                        "--input-type=module",
                        "--eval",
                        "console.log(import.meta.resolve('opennosh'))",
                    ],
                    cwd=javascript,
                    environment=clean_environment,
                )
                expected_package = (javascript / "node_modules/opennosh").resolve()
                if resolved.returncode != 0 or str(expected_package) not in resolved.stdout:
                    issues.append(
                        "JavaScript starter: package did not resolve from the clean install"
                    )
                for server, _thread in fixtures:
                    clean_environment["OPENNOSH_TARGET"] = (
                        f"http://127.0.0.1:{server.server_port}"
                    )
                    result = _run(
                        ["node", "index.mjs"],
                        cwd=javascript,
                        environment=clean_environment,
                    )
                    if result.returncode != 0 or not _result_is_valid(
                        result.stdout, endpoint_kind=server.endpoint_kind
                    ):
                        issues.append(
                            "JavaScript starter: "
                            f"{server.endpoint_kind} public-read journey failed"
                        )

            python_root = temporary_root / "python"
            shutil.copytree(ROOT / "examples/python-public-read", python_root)
            environment_root = temporary_root / "python-environment"
            created = _run(
                ["uv", "venv", str(environment_root), "--python", sys.executable, "--seed"],
                cwd=temporary_root,
                environment=clean_environment,
            )
            python_executable = environment_root / (
                "Scripts/python.exe" if os.name == "nt" else "bin/python"
            )
            if created.returncode != 0:
                issues.append("Python starter: clean environment creation failed")
            else:
                installed = _run(
                    ["uv", "pip", "install", "--python", str(python_executable), str(wheel)],
                    cwd=temporary_root,
                    environment=clean_environment,
                )
                if installed.returncode != 0:
                    issues.append("Python starter: clean wheel install failed")
                else:
                    resolved = _run(
                        [
                            str(python_executable),
                            "-c",
                            "import opennosh_api; print(opennosh_api.__file__)",
                        ],
                        cwd=python_root,
                        environment=clean_environment,
                    )
                    if (
                        resolved.returncode != 0
                        or not Path(resolved.stdout.strip())
                        .resolve()
                        .is_relative_to(environment_root.resolve())
                    ):
                        issues.append(
                            "Python starter: package did not resolve from the clean install"
                        )
                    for server, _thread in fixtures:
                        clean_environment["OPENNOSH_TARGET"] = (
                            f"http://127.0.0.1:{server.server_port}"
                        )
                        result = _run(
                            [str(python_executable), "main.py"],
                            cwd=python_root,
                            environment=clean_environment,
                        )
                        if result.returncode != 0 or not _result_is_valid(
                            result.stdout, endpoint_kind=server.endpoint_kind
                        ):
                            issues.append(
                                "Python starter: "
                                f"{server.endpoint_kind} public-read journey failed"
                            )

            expected_paths = [
                "/api/v1/foods/search",
                "/api/v1/public/foods/community/rajma-masala",
            ] * 2
            for server, _thread in fixtures:
                if [request["path"] for request in server.requests] != expected_paths:
                    issues.append(
                        "developer starters: "
                        f"{server.endpoint_kind} request journey did not match search then detail"
                    )
                if any(
                    request["endpoint_kind"] != server.endpoint_kind
                    or request["authorization"] is not None
                    or request["cookie"] is not None
                    for request in server.requests
                ):
                    issues.append(
                        "developer starters: an ambient credential or endpoint mismatch "
                        f"reached the {server.endpoint_kind} fixture"
                    )
    finally:
        for server, thread in fixtures:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    return issues


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--npm-tarball", required=True, type=Path)
    parser.add_argument("--wheel", required=True, type=Path)
    arguments = parser.parse_args()
    issues = validate_starters(arguments.npm_tarball.resolve(), arguments.wheel.resolve())
    if issues:
        for issue in issues:
            print(issue)
        return 1
    print("developer starters: clean-install hosted-shape and self-hosted journeys valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
