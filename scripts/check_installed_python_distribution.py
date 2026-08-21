#!/usr/bin/env python3
"""Exercise the built wheel as an installed package, including bundled data."""

from __future__ import annotations

import argparse
import importlib
import sys
import tempfile
import zipfile
from pathlib import Path


def validate_installed_wheel(wheel: Path, expected_version: str) -> list[str]:
    issues: list[str] = []
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory)
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(target)

        sys.path.insert(0, str(target))
        try:
            validation = importlib.import_module("opennosh_api.foodpacks.validation")
            main = importlib.import_module("opennosh_api.main")
            package_root = target.resolve()
            module_path = Path(validation.__file__).resolve()
            if not module_path.is_relative_to(package_root):
                issues.append("installed wheel: imported opennosh_api outside extracted wheel")
            expected_schema = module_path.with_name("food-pack.schema.json")
            if validation.DEFAULT_SCHEMA_PATH.resolve() != expected_schema:
                issues.append("installed wheel: bundled food-pack schema was not selected")
            else:
                validation._schema_validator.cache_clear()
                validation._schema_validator()
            if main.read_app_version() != expected_version:
                issues.append("installed wheel: application version does not match VERSION")
        finally:
            sys.path.pop(0)
    return issues


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    expected_version = (root / "VERSION").read_text(encoding="utf-8").strip()
    issues = validate_installed_wheel(arguments.wheel.resolve(), expected_version)
    if issues:
        for issue in issues:
            print(issue)
        return 1
    print("Installed Python distribution: valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
