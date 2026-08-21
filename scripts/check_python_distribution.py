#!/usr/bin/env python3
"""Inspect built PyPI artifacts without installing them."""

from __future__ import annotations

import argparse
import tarfile
import zipfile
from email.parser import Parser
from pathlib import Path


def validate_distribution(root: Path, dist: Path) -> list[str]:
    issues: list[str] = []
    release_version = (root / "VERSION").read_text(encoding="utf-8").strip()
    wheels = list(dist.glob(f"opennosh-{release_version}-*.whl"))
    sdists = list(dist.glob(f"opennosh-{release_version}.tar.gz"))
    if len(wheels) != 1:
        issues.append(f"distribution: expected one opennosh {release_version} wheel")
    if len(sdists) != 1:
        issues.append(f"distribution: expected one opennosh {release_version} source archive")
    if issues:
        return issues

    dist_info = f"opennosh-{release_version}.dist-info"
    with zipfile.ZipFile(wheels[0]) as archive:
        names = set(archive.namelist())
        required = {
            "opennosh_api/foodpacks/food-pack.schema.json",
            f"{dist_info}/entry_points.txt",
            f"{dist_info}/licenses/AUTHORS.md",
            f"{dist_info}/licenses/LICENSE",
            f"{dist_info}/licenses/LICENSES.md",
            f"{dist_info}/licenses/NOTICE.md",
            f"{dist_info}/METADATA",
        }
        for missing in sorted(required - names):
            issues.append(f"wheel: missing {missing}")
        if f"{dist_info}/METADATA" in names:
            metadata = Parser().parsestr(
                archive.read(f"{dist_info}/METADATA").decode("utf-8")
            )
            if metadata["Name"] != "opennosh":
                issues.append("wheel metadata: Name must be opennosh")
            if metadata["Version"] != release_version:
                issues.append("wheel metadata: Version must match VERSION")

    with tarfile.open(sdists[0]) as archive:
        names = set(archive.getnames())
        prefix = f"opennosh-{release_version}"
        for relative in ("VERSION", "schemas/food-pack.schema.json", "NOTICE.md", "LICENSES.md"):
            if f"{prefix}/{relative}" not in names:
                issues.append(f"source archive: missing {relative}")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dist", type=Path)
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    issues = validate_distribution(root, arguments.dist.resolve())
    if issues:
        for issue in issues:
            print(issue)
        return 1
    print("Python distribution: valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
