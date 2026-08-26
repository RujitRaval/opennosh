"""Enforce repository and changed-line Python coverage floors."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


class ChangedCoverageError(ValueError):
    """Raised when changed executable lines miss the required coverage floor."""


def changed_lines(base: str, head: str, prefix: str) -> dict[str, set[int]]:
    result = subprocess.run(
        ["git", "diff", "--unified=0", "--no-ext-diff", f"{base}...{head}", "--", prefix],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    current: str | None = None
    lines: dict[str, set[int]] = {}
    for raw in result.stdout.splitlines():
        if raw.startswith("+++ b/"):
            current = raw[6:]
            lines.setdefault(current, set())
            continue
        match = HUNK.match(raw)
        if current is None or match is None:
            continue
        start = int(match.group(1))
        count = int(match.group(2) or "1")
        lines[current].update(range(start, start + count))
    return lines


def check_report(
    report: dict[str, Any],
    changed: dict[str, set[int]],
    *,
    changed_threshold: float,
    repository_threshold: float,
) -> dict[str, Any]:
    repository_percent = float(report["totals"]["percent_covered"])
    if repository_percent + 1e-9 < repository_threshold:
        raise ChangedCoverageError(
            "Repository line coverage "
            f"{repository_percent:.2f}% is below {repository_threshold:.2f}%"
        )
    executable: set[tuple[str, int]] = set()
    covered: set[tuple[str, int]] = set()
    for filename, file_report in report["files"].items():
        normalized = filename.removeprefix(str(ROOT) + "/")
        requested = changed.get(normalized, set())
        if not requested:
            continue
        executed = set(file_report.get("executed_lines", []))
        missing = set(file_report.get("missing_lines", []))
        for line in requested & (executed | missing):
            executable.add((normalized, line))
            if line in executed:
                covered.add((normalized, line))
    if not executable:
        return {
            "repository_percent": repository_percent,
            "changed_percent": 100.0,
            "changed_executable_lines": 0,
            "missing": [],
        }
    changed_percent = 100 * len(covered) / len(executable)
    missing_lines = sorted(executable - covered)
    if changed_percent + 1e-9 < changed_threshold:
        rendered = ", ".join(f"{filename}:{line}" for filename, line in missing_lines)
        raise ChangedCoverageError(
            f"Changed-line coverage {changed_percent:.2f}% is below {changed_threshold:.2f}%; "
            f"untested={rendered}"
        )
    return {
        "repository_percent": repository_percent,
        "changed_percent": changed_percent,
        "changed_executable_lines": len(executable),
        "missing": [f"{filename}:{line}" for filename, line in missing_lines],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--coverage-json", type=Path, required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--prefix", default="api/opennosh_api")
    contract = json.loads((ROOT / "config/trust-gates.v1.json").read_text(encoding="utf-8"))
    coverage = contract["coverage"]
    parser.add_argument(
        "--changed-threshold",
        type=float,
        default=float(coverage["python_changed_line_percent"]),
    )
    parser.add_argument(
        "--repository-threshold",
        type=float,
        default=float(coverage["python_repository_line_percent"]),
    )
    arguments = parser.parse_args()
    report = json.loads(arguments.coverage_json.read_text(encoding="utf-8"))
    changed = changed_lines(arguments.base, arguments.head, arguments.prefix)
    try:
        result = check_report(
            report,
            changed,
            changed_threshold=arguments.changed_threshold,
            repository_threshold=arguments.repository_threshold,
        )
    except ChangedCoverageError as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
