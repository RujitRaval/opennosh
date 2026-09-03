#!/usr/bin/env python3
"""Exercise the built wheel as an installed package, including bundled data."""

from __future__ import annotations

import argparse
import importlib
import sys
import tempfile
import zipfile
from pathlib import Path

from check_python_distribution import EXPECTED_CONSOLE_SCRIPTS


def validate_installed_wheel(wheel: Path, expected_version: str) -> list[str]:
    issues: list[str] = []
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory)
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(target)

        sys.path.insert(0, str(target))
        try:
            capacity = importlib.import_module("opennosh_api.capacity")
            contracts = importlib.import_module("opennosh_api.contracts")
            validation = importlib.import_module("opennosh_api.foodpacks.validation")
            main = importlib.import_module("opennosh_api.main")
            sdk = importlib.import_module("opennosh_api.sdk")
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
            manifest = capacity.load_capacity_manifest()
            if manifest.schema_version != "1.0":
                issues.append("installed wheel: capacity manifest is unavailable")
            compatibility = contracts.load_developer_compatibility()
            if compatibility.get("schema_version") != "1.0":
                issues.append("installed wheel: developer compatibility manifest is unavailable")
            if main.read_app_version() != expected_version:
                issues.append("installed wheel: application version does not match VERSION")
            if sdk.PACKAGE_VERSION != expected_version:
                issues.append("installed wheel: SDK version does not match VERSION")
            for client_name in ("OpenNoshClient", "AsyncOpenNoshClient"):
                client_type = getattr(sdk, client_name, None)
                if not isinstance(client_type, type):
                    issues.append(f"installed wheel: SDK does not export {client_name}")
                    continue
                client = client_type("hosted")
                if client.origin != "https://opennosh.org":
                    issues.append(f"installed wheel: {client_name} hosted target is invalid")
                if not callable(getattr(client, "download_pack", None)):
                    issues.append(f"installed wheel: {client_name} public operations are missing")
            for command, target_name in EXPECTED_CONSOLE_SCRIPTS.items():
                module_name, attribute_name = target_name.split(":", maxsplit=1)
                module = importlib.import_module(module_name)
                target = getattr(module, attribute_name, None)
                if not callable(target):
                    issues.append(
                        f"installed wheel: {command} target {target_name} is not callable"
                    )
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
