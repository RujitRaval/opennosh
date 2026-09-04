#!/usr/bin/env python3
"""Validate a publication-readiness JSON report and its canonical digest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from opennosh_api.publication.readiness import readiness_digest


def validate_report(report: dict[str, object], schema: dict[str, object]) -> list[str]:
    issues = [
        f"schema {'/'.join(map(str, error.absolute_path)) or '/'}: {error.message}"
        for error in sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(report),
            key=lambda item: list(item.absolute_path),
        )
    ]
    if report.get("readiness_sha256") != readiness_digest(report):
        issues.append("readiness_sha256 does not match canonical report content")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", nargs="?", type=Path)
    parser.add_argument(
        "--schema", type=Path, default=Path("schemas/publication-readiness.schema.json")
    )
    arguments = parser.parse_args()
    raw = arguments.report.read_text(encoding="utf-8") if arguments.report else sys.stdin.read()
    report = json.loads(raw)
    schema = json.loads(arguments.schema.read_text(encoding="utf-8"))
    issues = validate_report(report, schema)
    if issues:
        print("\n".join(issues), file=sys.stderr)
        return 1
    print(f"Publication readiness: valid ({report['readiness_sha256']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
