#!/usr/bin/env python3
"""Route human release PRs and governed machine PRs through strict metadata gates."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from scripts.check_pr_title import valid_title

GOVERNED_BRANCH_RE = re.compile(r"^opennosh/contribution/[0-9a-f]{24}$")
GOVERNED_TITLE_RE = re.compile(
    r"^Governed contribution: [a-z0-9](?:[a-z0-9-]{0,158}[a-z0-9])?$"
)


def valid_metadata(
    head_ref: str,
    title: str,
    *,
    expected_version: str,
    changelog: str,
) -> bool:
    if GOVERNED_BRANCH_RE.fullmatch(head_ref):
        return bool(GOVERNED_TITLE_RE.fullmatch(title.strip()))
    if not (head_ref.startswith("agent/") or head_ref.startswith("codex/")):
        return False
    return valid_title(title, expected_version) and f"## [{expected_version}]" in changelog


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parents[1]
    if len(argv) != 3:
        print("Usage: check_pr_metadata.py <head-ref> <title>")
        return 1
    expected_version = (root / "VERSION").read_text(encoding="utf-8").strip()
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    if not valid_metadata(
        argv[1],
        argv[2],
        expected_version=expected_version,
        changelog=changelog,
    ):
        print(
            "PR metadata must be a versioned agent/codex release or an exact "
            "governed contribution branch and title"
        )
        return 1
    print("Pull-request metadata is valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
