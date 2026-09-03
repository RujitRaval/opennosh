from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from importlib import resources
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator, FormatChecker

_MANIFEST_NAME = "developer-compatibility.v1.json"
_SCHEMA_NAME = "developer-compatibility.schema.json"
_SEMVER_PATTERN = re.compile(
    r"^(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})$"
)


def developer_compatibility_digest(payload: Mapping[str, object]) -> str:
    unsigned = dict(payload)
    unsigned.pop("compatibility_sha256", None)
    encoded = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def validate_developer_compatibility(
    manifest: Mapping[str, Any], schema: Mapping[str, Any]
) -> None:
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(manifest),
        key=lambda item: list(item.path),
    )
    if errors:
        first = errors[0]
        location = "/".join(str(part) for part in first.absolute_path) or "/"
        raise ValueError(f"developer compatibility {location}: {first.message}")
    if manifest.get("compatibility_sha256") != developer_compatibility_digest(manifest):
        raise ValueError("developer compatibility digest mismatch")


def load_developer_compatibility() -> dict[str, Any]:
    package = resources.files("opennosh_api.contracts")
    manifest_path = package.joinpath(_MANIFEST_NAME)
    schema_path = package.joinpath(_SCHEMA_NAME)
    if not manifest_path.is_file() or not schema_path.is_file():
        repository = Path(__file__).resolve().parents[3]
        manifest_path = repository / "config/developer-compatibility.v1.json"
        schema_path = repository / "schemas/developer-compatibility.schema.json"
    manifest = cast(
        dict[str, Any], json.loads(manifest_path.read_text(encoding="utf-8"))
    )
    schema = cast(dict[str, Any], json.loads(schema_path.read_text(encoding="utf-8")))
    validate_developer_compatibility(manifest, schema)
    return manifest


def supports_openapi_version(manifest: Mapping[str, Any], version: str) -> bool:
    if _SEMVER_PATTERN.fullmatch(version) is None:
        return False
    candidate = tuple(int(part) for part in version.split("."))
    for supported in manifest.get("openapi", {}).get("supported", []):
        if not isinstance(supported, Mapping) or supported.get("major") != candidate[0]:
            continue
        minimum_value = str(supported.get("minimum", ""))
        if _SEMVER_PATTERN.fullmatch(minimum_value) is None:
            return False
        minimum = tuple(int(part) for part in minimum_value.split("."))
        return candidate >= minimum
    return False
