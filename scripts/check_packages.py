#!/usr/bin/env python3
"""Validate public package identities against the repository release version."""

from __future__ import annotations

import argparse
import json
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

    package_license = root / "packages" / "npm" / "LICENSE"
    if package_license.read_bytes() != (root / "LICENSE").read_bytes():
        issues.append("packages/npm/LICENSE: must match the repository MIT license")

    lock_path = root / "packages" / "npm" / "package-lock.json"
    lock_metadata = json.loads(lock_path.read_text(encoding="utf-8"))
    if lock_metadata.get("name") != "opennosh" or lock_metadata.get("version") != expected_npm_version:
        issues.append("packages/npm/package-lock.json: root identity is stale")
    root_lock = lock_metadata.get("packages", {}).get("", {})
    if root_lock.get("name") != "opennosh" or root_lock.get("version") != expected_npm_version:
        issues.append("packages/npm/package-lock.json: package identity is stale")

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
