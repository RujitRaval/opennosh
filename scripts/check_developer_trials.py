#!/usr/bin/env python3
"""Validate redacted external developer-trial evidence and the preview/GA threshold."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

SCHEMA_PATH = Path("schemas/developer-integration-trial.schema.json")
REPORTS_PATH = Path("docs/evidence/developer-trials")
COMPATIBILITY_PATH = Path("config/developer-compatibility.v1.json")
MAX_REPORT_BYTES = 64 * 1024


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("document must be a JSON object")
    return payload


def validate_repository(root: Path) -> list[str]:
    issues: list[str] = []
    try:
        schema = _load_object(root / SCHEMA_PATH)
        compatibility = _load_object(root / COMPATIBILITY_PATH)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        return [f"developer trial configuration: {error}"]

    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    operators: set[str] = set()
    reports_path = root / REPORTS_PATH
    for path in sorted(reports_path.glob("*.json")):
        try:
            if path.stat().st_size > MAX_REPORT_BYTES:
                raise ValueError(f"report exceeds {MAX_REPORT_BYTES} bytes")
            report = _load_object(path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            issues.append(f"{path.relative_to(root)}: {error}")
            continue
        errors = sorted(validator.iter_errors(report), key=lambda item: list(item.path))
        for error in errors:
            location = ".".join(str(part) for part in error.path) or "document"
            issues.append(f"{path.relative_to(root)}: {location}: {error.message}")
        if errors:
            continue
        report_id = str(report["report_id"])
        operator = str(report["operator"]["github_login"])
        reviewer = str(report["reviewer"]["github_login"])
        if path.stem != report_id:
            issues.append(f"{path.relative_to(root)}: filename must match report_id")
        if operator.casefold() == reviewer.casefold():
            issues.append(f"{path.relative_to(root)}: operator and reviewer must differ")
        observed_at = datetime.fromisoformat(str(report["observed_at"]).replace("Z", "+00:00"))
        reviewed_at = datetime.fromisoformat(
            str(report["reviewer"]["reviewed_at"]).replace("Z", "+00:00")
        )
        if reviewed_at < observed_at:
            issues.append(f"{path.relative_to(root)}: review cannot predate observation")
        normalized_operator = operator.casefold()
        if normalized_operator in operators:
            issues.append(f"{path.relative_to(root)}: operator must be unique across reports")
        operators.add(normalized_operator)
        if "get_public_food" not in report["operations"]:
            issues.append(f"{path.relative_to(root)}: operations must include get_public_food")

    if compatibility.get("status") == "stable" and len(operators) < 2:
        issues.append("stable developer compatibility requires two independent trial operators")
    return issues


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    issues = validate_repository(root)
    if issues:
        for issue in issues:
            print(issue)
        return 1
    print("developer trial evidence: valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
