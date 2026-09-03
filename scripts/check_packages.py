#!/usr/bin/env python3
"""Validate public package identities against the repository release version."""

from __future__ import annotations

import argparse
import json
import re
import tomllib
from pathlib import Path
from typing import Any


def npm_version(release_version: str) -> str:
    parts = release_version.split(".")
    if len(parts) != 4 or any(not part.isdigit() for part in parts):
        raise ValueError("VERSION must contain four numeric components")
    return ".".join(parts[:3])


def validate_repository(root: Path) -> list[str]:
    issues: list[str] = []
    release_version = (root / "VERSION").read_text(encoding="utf-8").strip()
    try:
        expected_npm_version = npm_version(release_version)
    except ValueError as error:
        return [f"VERSION: {error}"]

    with (root / "pyproject.toml").open("rb") as handle:
        python_metadata = tomllib.load(handle)
    project = python_metadata.get("project", {})
    if project.get("name") != "opennosh":
        issues.append("pyproject.toml: public project name must be opennosh")
    if "version" not in project.get("dynamic", []):
        issues.append("pyproject.toml: version must be dynamic")
    hatch_version = python_metadata.get("tool", {}).get("hatch", {}).get("version", {})
    if hatch_version.get("path") != "VERSION":
        issues.append("pyproject.toml: Hatch must read the canonical VERSION file")

    npm_path = root / "packages" / "npm" / "package.json"
    npm_metadata: dict[str, Any] = json.loads(npm_path.read_text(encoding="utf-8"))
    if npm_metadata.get("name") != "opennosh":
        issues.append("packages/npm/package.json: public package name must be opennosh")
    if npm_metadata.get("version") != expected_npm_version:
        issues.append(
            "packages/npm/package.json: version must match the first three VERSION components"
        )
    if npm_metadata.get("private") is True:
        issues.append("packages/npm/package.json: public bootstrap package cannot be private")
    if npm_metadata.get("bin", {}).get("opennosh") != "bin/opennosh.mjs":
        issues.append("packages/npm/package.json: opennosh executable is missing")
    root_export = npm_metadata.get("exports", {}).get(".", {})
    if (
        npm_metadata.get("type") != "module"
        or npm_metadata.get("main") != "./src/index.js"
        or npm_metadata.get("types") != "./src/index.d.ts"
        or root_export.get("import") != "./src/index.js"
        or root_export.get("types") != "./src/index.d.ts"
    ):
        issues.append("packages/npm/package.json: ESM SDK root export is missing")
    if "src" not in npm_metadata.get("files", []):
        issues.append("packages/npm/package.json: SDK sources are missing from packed files")
    sdk_source_path = root / "packages" / "npm" / "src" / "index.js"
    sdk_source = (
        sdk_source_path.read_text(encoding="utf-8") if sdk_source_path.is_file() else ""
    )
    sdk_version = re.search(r'^export const PACKAGE_VERSION = "([^"]+)";$', sdk_source, re.M)
    if not sdk_source or sdk_version is None or sdk_version.group(1) != expected_npm_version:
        issues.append("packages/npm/src/index.js: SDK version is stale")

    package_license = root / "packages" / "npm" / "LICENSE"
    if package_license.read_bytes() != (root / "LICENSE").read_bytes():
        issues.append("packages/npm/LICENSE: must match the repository MIT license")

    lock_path = root / "packages" / "npm" / "package-lock.json"
    lock_metadata = json.loads(lock_path.read_text(encoding="utf-8"))
    if (
        lock_metadata.get("name") != "opennosh"
        or lock_metadata.get("version") != expected_npm_version
    ):
        issues.append("packages/npm/package-lock.json: root identity is stale")
    root_lock = lock_metadata.get("packages", {}).get("", {})
    if (
        root_lock.get("name") != "opennosh"
        or root_lock.get("version") != expected_npm_version
    ):
        issues.append("packages/npm/package-lock.json: package identity is stale")

    dockerfile = (root / "api" / "Dockerfile").read_text(encoding="utf-8")
    package_documents = "COPY README.md AUTHORS.md ./"
    if package_documents not in dockerfile:
        issues.append("api/Dockerfile: package metadata documents must be copied before install")

    return issues


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", type=Path, default=Path(__file__).resolve().parents[1])
    arguments = parser.parse_args()
    issues = validate_repository(arguments.root.resolve())
    if issues:
        for issue in issues:
            print(issue)
        return 1
    print("package identities: valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
