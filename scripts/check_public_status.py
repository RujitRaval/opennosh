#!/usr/bin/env python3
"""Validate the fixed, bounded public-status component contract."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator
from opennosh_api.public_operations.manifest import (
    PUBLIC_COMPONENT_IDS,
    load_public_status_manifest,
)

MANIFEST = Path("config/public-status.v1.json")
SCHEMA = Path("schemas/public-status.schema.json")


def validate_repository(root: Path) -> list[str]:
    document = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    schema = json.loads((root / SCHEMA).read_text(encoding="utf-8"))
    issues = [
        f"manifest schema {'/'.join(map(str, error.absolute_path)) or '/'}: {error.message}"
        for error in sorted(
            Draft202012Validator(schema).iter_errors(document),
            key=lambda item: list(item.absolute_path),
        )
    ]
    try:
        manifest = load_public_status_manifest(root / MANIFEST)
    except (OSError, ValueError) as error:
        issues.append(f"manifest contract: {error}")
        return issues
    if tuple(component.component_id for component in manifest.components) != PUBLIC_COMPONENT_IDS:
        issues.append("public status component inventory changed")
    if len(manifest.digest) != 64:
        issues.append("public status manifest digest is invalid")
    return issues


def main() -> int:
    issues = validate_repository(Path("."))
    if issues:
        for issue in issues:
            print(f"public status: {issue}")
        return 1
    manifest = load_public_status_manifest(MANIFEST)
    print(f"Public status manifest validated ({manifest.digest}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
