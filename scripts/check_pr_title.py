#!/usr/bin/env python3
"""Enforce the pull-request title format produced by GStack ship."""

from __future__ import annotations

import re
import sys
from pathlib import Path


TITLE_RE = re.compile(
    r"^v(?P<version>\d+\.\d+\.\d+\.\d+) "
    r"(feat|fix|docs|chore|refactor|test|build|ci): .+"
)


def valid_title(title: str, expected_version: str | None = None) -> bool:
    match = TITLE_RE.fullmatch(title.strip())
    return bool(match and (expected_version is None or match.group("version") == expected_version))


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parents[1]
    expected_version = (root / "VERSION").read_text(encoding="utf-8").strip()
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    version_is_documented = f"## [{expected_version}]" in changelog
    if len(argv) != 2 or not valid_title(argv[1], expected_version) or not version_is_documented:
        print(
            f"PR title must start with v{expected_version}, use an allowed change type, "
            "and match the version documented in CHANGELOG.md"
        )
        return 1
    print("Pull-request title is valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
