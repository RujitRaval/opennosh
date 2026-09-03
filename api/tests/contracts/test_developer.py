from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from opennosh_api.contracts.developer import (
    developer_compatibility_digest,
    load_developer_compatibility,
    supports_openapi_version,
    validate_developer_compatibility,
)


def test_packaged_developer_compatibility_is_valid() -> None:
    manifest = load_developer_compatibility()

    assert manifest["schema_version"] == "1.0"
    assert manifest["status"] == "preview"
    assert manifest["compatibility_sha256"] == developer_compatibility_digest(manifest)
    assert manifest["clients"]["python"]["current"] == manifest["release_version"]
    assert manifest["clients"]["cli"]["current"] == manifest["release_version"]
    assert manifest["clients"]["mcp"]["contract_major"] == 1
    assert manifest["clients"]["mcp"]["status"] == "preview"
    assert manifest["clients"]["mcp"]["discovery_enabled"] is False
    assert manifest["clients"]["embed"]["contract_major"] == 1
    assert manifest["clients"]["embed"]["status"] == "preview"
    assert manifest["clients"]["embed"]["discovery_enabled"] is False


@pytest.mark.parametrize("version", ["1.0.0", "1.99.0", "2.0.0", "2.4.1"])
def test_current_and_previous_openapi_families_are_supported(version: str) -> None:
    assert supports_openapi_version(load_developer_compatibility(), version) is True


@pytest.mark.parametrize(
    "version",
    [
        "0.99.0",
        "3.0.0",
        "2.0",
        "02.0.0",
        "².0.0",
        f"{'9' * 5_000}.0.0",
        "2.0.0-rc1",
        "invalid",
    ],
)
def test_undeclared_or_malformed_openapi_versions_are_rejected(version: str) -> None:
    assert supports_openapi_version(load_developer_compatibility(), version) is False


def test_manifest_digest_tampering_is_rejected() -> None:
    manifest = load_developer_compatibility()
    tampered = copy.deepcopy(manifest)
    tampered["status"] = "stable"

    with pytest.raises(ValueError, match="digest mismatch"):
        validate_developer_compatibility(tampered, _permissive_schema())


def test_schema_violation_is_rejected_before_digest() -> None:
    manifest = load_developer_compatibility()
    tampered = copy.deepcopy(manifest)
    tampered["unexpected"] = True

    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {key: {} for key in manifest},
    }
    with pytest.raises(ValueError, match="Additional properties"):
        validate_developer_compatibility(tampered, schema)


def test_invalid_deprecation_date_is_rejected() -> None:
    manifest = load_developer_compatibility()
    tampered = copy.deepcopy(manifest)
    tampered["clients"]["javascript"]["deprecation_date"] = "2026-99-99"
    tampered["compatibility_sha256"] = developer_compatibility_digest(tampered)

    schema = copy.deepcopy(_load_schema())
    with pytest.raises(ValueError, match="deprecation_date"):
        validate_developer_compatibility(tampered, schema)


def _permissive_schema() -> dict[str, object]:
    return {"type": "object"}


def _load_schema() -> dict[str, object]:
    return json.loads(
        (Path(__file__).resolve().parents[3] / "schemas/developer-compatibility.schema.json")
        .read_text(encoding="utf-8")
    )
