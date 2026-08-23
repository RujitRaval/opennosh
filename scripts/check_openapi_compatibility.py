from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any


def _load_ref(reference: str, path: Path) -> dict[str, Any] | None:
    result = subprocess.run(
        ["git", "show", f"{reference}:{path.as_posix()}"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return json.loads(result.stdout)


def _major(schema: dict[str, Any]) -> int:
    version = schema.get("info", {}).get("x-opennosh-contract-version", "0.0.0")
    return int(str(version).split(".", 1)[0])


def _schema_breaks(
    previous: dict[str, Any],
    current: dict[str, Any],
    location: str,
) -> list[str]:
    breaks: list[str] = []
    previous_type = previous.get("type")
    current_type = current.get("type")
    if previous_type and current_type and previous_type != current_type:
        breaks.append(f"{location}: type changed from {previous_type} to {current_type}")

    previous_enum = set(previous.get("enum", []))
    current_enum = set(current.get("enum", []))
    for value in sorted(previous_enum - current_enum, key=str):
        breaks.append(f"{location}: enum value removed: {value}")

    previous_properties = previous.get("properties", {})
    current_properties = current.get("properties", {})
    if isinstance(previous_properties, dict) and isinstance(current_properties, dict):
        for name in sorted(set(previous_properties) - set(current_properties)):
            breaks.append(f"{location}: property removed: {name}")
        previous_required = set(previous.get("required", []))
        current_required = set(current.get("required", []))
        for name in sorted(current_required - previous_required):
            breaks.append(f"{location}: existing consumers now require property: {name}")
        for name in sorted(set(previous_properties) & set(current_properties)):
            breaks.extend(
                _schema_breaks(
                    previous_properties[name],
                    current_properties[name],
                    f"{location}.{name}",
                )
            )
    return breaks


def breaking_changes(previous: dict[str, Any], current: dict[str, Any]) -> list[str]:
    breaks: list[str] = []
    previous_paths = previous.get("paths", {})
    current_paths = current.get("paths", {})
    for path in sorted(set(previous_paths) - set(current_paths)):
        breaks.append(f"operation path removed: {path}")
    for path in sorted(set(previous_paths) & set(current_paths)):
        old_path = previous_paths[path]
        new_path = current_paths[path]
        for method in sorted(set(old_path) & set(new_path)):
            if method.lower() not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                continue
            old_responses = old_path[method].get("responses", {})
            new_responses = new_path[method].get("responses", {})
            for status in sorted(set(old_responses) - set(new_responses)):
                breaks.append(f"{method.upper()} {path}: response removed: {status}")
        for method in sorted(set(old_path) - set(new_path)):
            if method.lower() in {"get", "post", "put", "patch", "delete", "head", "options"}:
                breaks.append(f"operation removed: {method.upper()} {path}")

    previous_schemas = previous.get("components", {}).get("schemas", {})
    current_schemas = current.get("components", {}).get("schemas", {})
    for name in sorted(set(previous_schemas) - set(current_schemas)):
        breaks.append(f"schema removed: {name}")
    for name in sorted(set(previous_schemas) & set(current_schemas)):
        breaks.extend(
            _schema_breaks(previous_schemas[name], current_schemas[name], f"schema {name}")
        )
    return breaks


def main() -> None:
    parser = argparse.ArgumentParser(description="Reject unversioned OpenAPI breaking changes.")
    parser.add_argument("--current", type=Path, default=Path("web/lib/generated/openapi.json"))
    parser.add_argument("--previous-ref", default="HEAD^")
    args = parser.parse_args()

    current = json.loads(args.current.read_text(encoding="utf-8"))
    previous = _load_ref(args.previous_ref, args.current)
    if previous is None:
        print("No previous contract found; treating this as the initial contract.")
        return

    changes = breaking_changes(previous, current)
    if not changes:
        print("No breaking OpenAPI changes detected.")
        return
    if _major(current) > _major(previous):
        print("Breaking changes are covered by a contract major-version increase.")
        return

    formatted = "\n".join(f"  - {change}" for change in changes)
    raise SystemExit(f"Breaking OpenAPI changes require a major-version increase:\n{formatted}")


if __name__ == "__main__":
    main()
