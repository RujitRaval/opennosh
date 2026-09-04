#!/usr/bin/env python3
"""Validate the privacy boundary of the public impact metric manifest."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

MANIFEST = Path("config/impact-metrics.v1.json")
SCHEMA = Path("schemas/impact-metrics.schema.json")
EXPECTED = {
    "verified_adopters": "explicit_proof_only",
    "community_declarations": "explicit_proof_only",
    "accepted_contributions": "explicit_proof_only",
    "pack_installs": "global_only",
    "api_reads": "global_only",
    "artifact_downloads": "global_only",
}


def validate_repository(root: Path) -> list[str]:
    manifest = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    schema = json.loads((root / SCHEMA).read_text(encoding="utf-8"))
    issues = [
        f"manifest schema {'/'.join(map(str, error.absolute_path)) or '/'}: {error.message}"
        for error in sorted(
            Draft202012Validator(schema).iter_errors(manifest),
            key=lambda item: list(item.absolute_path),
        )
    ]
    observed = {metric["id"]: metric["regionalization"] for metric in manifest.get("metrics", [])}
    if observed != EXPECTED:
        issues.append("impact metric inventory or regionalization policy changed")
    return issues


def main() -> int:
    issues = validate_repository(Path("."))
    if issues:
        for issue in issues:
            print(f"impact metrics: {issue}")
        return 1
    print("Impact metric manifest validated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
