#!/usr/bin/env python3
"""Validate the repository's Markdown documents without external dependencies."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote


CORE_DOCUMENTS = {
    "README.md",
    "02-PRD.md",
    "03-TRD.md",
    "04-DATA-LICENSING.md",
    "08-PRODUCT-DECISIONS.md",
    "LICENSES.md",
    "NOTICE.md",
    "docs/foodpack-spec.md",
    "docs/license-notice-review.md",
}
LICENSE_NOTICE_REQUIREMENTS = {
    Path("NOTICE.md"): (
        "MIT License",
        "CC0 1.0 Universal",
        "USDA FoodData Central",
        "ODbL 1.0",
        "DbCL 1.0",
        "CC BY-SA 3.0",
        "Private user data",
        "Test fixtures",
        "api/tests/fixtures/usda/",
        "api/tests/open_food_facts/fixtures/",
        "api/tests/fixtures/wger/",
        "https://creativecommons.org/licenses/by-sa/3.0/legalcode",
    ),
    Path("LICENSES.md"): (
        "NOTICE.md",
        "MIT",
        "CC0 1.0 Universal",
        "USDA",
        "ODbL 1.0",
        "DbCL 1.0",
        "CC BY-SA 3.0",
    ),
    Path("web/app/(public)/[language]/notices/page.tsx"): (
        "getCatalog(language)",
        "catalog.notices",
    ),
    Path("web/lib/i18n/catalog.ts"): (
        "MIT License",
        "CC0 1.0 Universal",
        "USDA FoodData Central",
        "ODbL 1.0",
        "DbCL 1.0",
        "CC BY-SA 3.0",
        "Private account data",
    ),
    Path("web/components/tracker/tracker-footer.tsx"): ('href="/en/notices"', "Licenses &amp; data notices"),
    Path("api/opennosh_api/exports/schemas.py"): (
        'license: Literal["CC0-1.0"]',
        "Contributor credit remains visible as a community norm.",
    ),
    Path("api/opennosh_api/foods/schemas.py"): (
        'database_license: Literal["ODbL-1.0"]',
        'contents_license: Literal["DbCL-1.0"]',
        "https://opendatacommons.org/licenses/odbl/1-0/",
        "https://opendatacommons.org/licenses/dbcl/1-0/",
    ),
    Path("api/opennosh_api/exercises/schemas.py"): (
        'license_spdx: str = "CC-BY-SA-3.0"',
        "share_alike_notice",
        "Attribution and ShareAlike",
        "requirements apply to redistribution and adaptations.",
    ),
    Path("api/opennosh_api/integrations/open_food_facts.py"): (
        '"User-Agent": self._user_agent',
        '"code",',
        '"product_name",',
        '"nutriments",',
        '"nutrition",',
    ),
    Path(".github/pull_request_template.md"): (
        "authority to dedicate its eligible original material under CC0 1.0",
        "not copied from a proprietary application or restricted database",
    ),
    Path("api/Dockerfile"): ("COPY LICENSE LICENSES.md NOTICE.md ./",),
    Path("web/Dockerfile"): (
        "COPY --chown=nextjs:nodejs LICENSE LICENSES.md NOTICE.md ./licenses/",
    ),
    Path("pyproject.toml"): (
        'license = "MIT"',
        'license-files = ["LICENSE", "LICENSES.md", "NOTICE.md", "AUTHORS.md"]',
    ),
}
RETIRED_IDENTITY_RE = re.compile(r"\bopen[-_ ]?plate\b", re.IGNORECASE)
RETIRED_DOCUMENT_RE = re.compile(r"08-open-questions\.md", re.IGNORECASE)
CURRENT_IDENTITY_RE = re.compile(r"\bopennosh\b", re.IGNORECASE)
USER_FACING_TEXT_SUFFIXES = {".md", ".toml", ".txt", ".yaml", ".yml"}
IDENTITY_SCAN_EXCLUSIONS = {
    Path("scripts/check_docs.py"),
    Path("tests/test_check_docs.py"),
}
IGNORED_DIRECTORIES = {
    ".git",
    ".gstack",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "build",
    "dist",
    "node_modules",
    "venv",
    "vendor",
}
HEADING_RE = re.compile(r"^(#{1,6})\s+\S")
LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]*)\)")
LINK_DEFINITION_RE = re.compile(r"^\s*\[[^\]]+\]:\s*(<[^>]+>|\S+)")
INLINE_CODE_RE = re.compile(r"`+[^`]*`+")
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def markdown_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*.md")
        if not any(part in IGNORED_DIRECTORIES for part in path.parts)
    )


def user_facing_text_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in USER_FACING_TEXT_SUFFIXES
        and path.relative_to(root) not in IDENTITY_SCAN_EXCLUSIONS
        and not any(part in IGNORED_DIRECTORIES for part in path.parts)
    )


def validate_project_identity(root: Path) -> list[str]:
    issues: list[str] = []
    for path in user_facing_text_files(root):
        relative = path.relative_to(root)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            for _match in RETIRED_IDENTITY_RE.finditer(line):
                issues.append(
                    f"{relative}:{line_number}: retired project identity; use 'opennosh'"
                )
            if RETIRED_DOCUMENT_RE.search(line):
                issues.append(
                    f"{relative}:{line_number}: retired decision filename; "
                    "use '08-PRODUCT-DECISIONS.md'"
                )
            for match in CURRENT_IDENTITY_RE.finditer(line):
                if match.group(0) != "opennosh":
                    issues.append(
                        f"{relative}:{line_number}: project name must be exact lowercase 'opennosh'"
                    )
    return issues


def validate_license_notices(root: Path) -> list[str]:
    issues: list[str] = []
    for relative, required_fragments in LICENSE_NOTICE_REQUIREMENTS.items():
        path = root / relative
        if not path.is_file():
            issues.append(f"missing license-notice surface: {relative}")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            issues.append(f"{relative}: license-notice surface is not valid UTF-8")
            continue
        for fragment in required_fragments:
            if fragment not in text:
                issues.append(
                    f"{relative}: missing required license-notice text: {fragment}"
                )
    return issues


def link_destination(raw: str) -> str:
    value = raw.strip()
    if not value:
        return ""
    if value.startswith("<") and ">" in value:
        return value[1 : value.index(">")]
    return value.split(maxsplit=1)[0]


def validate_markdown_tree(root: Path, require_core: bool = True) -> list[str]:
    issues: list[str] = []
    files = markdown_files(root)

    if not files:
        return ["repository contains no Markdown documents", *validate_project_identity(root)]

    if require_core:
        missing = sorted(name for name in CORE_DOCUMENTS if not (root / name).is_file())
        issues.extend(f"missing core document: {name}" for name in missing)
        issues.extend(validate_license_notices(root))

    for path in files:
        relative = path.relative_to(root)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            issues.append(f"{relative}: file is not valid UTF-8")
            continue

        if not text.strip():
            issues.append(f"{relative}: document is empty")
            continue

        h1_count = 0
        previous_level = 0
        fence_marker: tuple[str, int] | None = None

        for line_number, line in enumerate(text.splitlines(), start=1):
            if line.endswith(" ") or line.endswith("\t"):
                issues.append(f"{relative}:{line_number}: trailing whitespace")

            fence = FENCE_RE.match(line)
            if fence_marker is not None:
                if fence:
                    marker, suffix = fence.groups()
                    if (
                        marker[0] == fence_marker[0]
                        and len(marker) >= fence_marker[1]
                        and not suffix.strip()
                    ):
                        fence_marker = None
                continue
            if fence:
                marker = fence.group(1)
                fence_marker = (marker[0], len(marker))
                continue

            heading = HEADING_RE.match(line)
            if heading:
                level = len(heading.group(1))
                if level == 1:
                    h1_count += 1
                if previous_level and level > previous_level + 1:
                    issues.append(
                        f"{relative}:{line_number}: heading jumps from H{previous_level} to H{level}"
                    )
                previous_level = level

            line_without_code = INLINE_CODE_RE.sub("", line)
            raw_targets = [match.group(1) for match in LINK_RE.finditer(line_without_code)]
            definition = LINK_DEFINITION_RE.match(line_without_code)
            if definition:
                raw_targets.append(definition.group(1))

            for raw_target in raw_targets:
                raw_target = link_destination(raw_target)
                if not raw_target or raw_target.startswith(("#", "http://", "https://", "mailto:")):
                    continue
                target_path = unquote(raw_target.split("#", maxsplit=1)[0])
                resolved_target = (path.parent / target_path).resolve()
                try:
                    resolved_target.relative_to(root.resolve())
                except ValueError:
                    issues.append(
                        f"{relative}:{line_number}: local link escapes repository: {target_path}"
                    )
                    continue
                if target_path and not resolved_target.exists():
                    issues.append(
                        f"{relative}:{line_number}: local link target does not exist: {target_path}"
                    )

        if fence_marker is not None:
            issues.append(f"{relative}: unclosed fenced code block")
        if h1_count != 1:
            issues.append(f"{relative}: expected exactly one H1 heading, found {h1_count}")

    issues.extend(validate_project_identity(root))
    return issues


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    issues = validate_markdown_tree(root)
    if issues:
        print("Repository documentation validation failed:")
        for issue in issues:
            print(f"- {issue}")
        return 1

    print(f"Markdown validation passed for {len(markdown_files(root))} files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
