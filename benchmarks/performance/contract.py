"""Load and validate the versioned opennosh performance contract."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker, ValidationError

CONTRACT_DIRECTORY = Path(__file__).resolve().parent
DEFAULT_CONTRACT_PATH = CONTRACT_DIRECTORY / "contract.v1.json"
CONTRACT_SCHEMA_PATH = CONTRACT_DIRECTORY / "contract.schema.json"
REQUIRED_DISTRIBUTIONS = {
    "source",
    "locale_script",
    "name_length",
    "variant",
    "duplicate_cluster",
    "missing_field",
    "license",
    "evidence",
    "projection_state",
    "release_age",
}
REQUIRED_QUERY_IDS = {
    "exact",
    "prefix",
    "fuzzy",
    "misspelled",
    "non_latin",
    "no_result",
    "filtered",
    "conflicting_variant",
    "first_page",
    "deep_cursor",
    "detail",
    "provenance",
}
REQUIRED_TRAFFIC_IDS = {
    "anonymous_read",
    "tracker",
    "publication",
    "pack_ingestion",
    "projection_rebuild",
}
REQUIRED_BOUNDARIES = {"postgresql", "fastapi", "same_origin_proxy", "edge_browser"}


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True)
class BenchmarkContract:
    path: Path
    document: dict[str, Any]
    sha256: str

    @property
    def seed(self) -> int:
        return int(self.document["seed"])

    def profile(self, profile_id: str) -> dict[str, Any]:
        for profile in self.document["profiles"]:
            if profile["id"] == profile_id:
                return dict(profile)
        raise ValueError(f"unknown benchmark profile: {profile_id}")


def _weighted_total(items: list[dict[str, Any]], *, label: str) -> None:
    total = sum(int(item["weight_bps"]) for item in items)
    if total != 10_000:
        raise ValueError(f"{label} weights must total 10000 basis points, found {total}")


def validate_contract(document: dict[str, Any]) -> None:
    schema = json.loads(CONTRACT_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    try:
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(document)
    except ValidationError as error:
        location = ".".join(str(part) for part in error.absolute_path) or "document"
        raise ValueError(
            f"benchmark contract schema violation at {location}: {error.message}"
        ) from error

    if document.get("schema_version") != "1.0.0":
        raise ValueError("unsupported benchmark contract schema_version")
    profiles = document.get("profiles", [])
    expected_profiles = [
        ("launch-reference", 1),
        ("10x", 10),
        ("100x", 100),
    ]
    actual_profiles = [(item.get("id"), item.get("scale_multiplier")) for item in profiles]
    if actual_profiles != expected_profiles:
        raise ValueError("profiles must be ordered launch-reference, 10x, 100x")
    launch_records = int(document["capacity_model"]["launch_records"])
    for profile in profiles:
        expected_records = launch_records * int(profile["scale_multiplier"])
        if int(profile["records"]) != expected_records:
            raise ValueError(f"{profile['id']} records do not match the capacity multiplier")

    distributions = document.get("distributions", {})
    missing_distributions = REQUIRED_DISTRIBUTIONS - set(distributions)
    if missing_distributions:
        raise ValueError(f"missing distributions: {sorted(missing_distributions)}")
    for name, distribution in distributions.items():
        total = sum(int(weight) for weight in distribution.values())
        if total != 10_000:
            raise ValueError(f"distribution {name} must total 10000 basis points, found {total}")

    queries = document.get("query_mix", [])
    query_ids = {str(item["id"]) for item in queries}
    if query_ids != REQUIRED_QUERY_IDS:
        raise ValueError(f"query mix IDs differ: {sorted(query_ids ^ REQUIRED_QUERY_IDS)}")
    _weighted_total(queries, label="query mix")

    traffic = document.get("traffic_mix", [])
    traffic_ids = {str(item["id"]) for item in traffic}
    if traffic_ids != REQUIRED_TRAFFIC_IDS:
        raise ValueError(f"traffic mix IDs differ: {sorted(traffic_ids ^ REQUIRED_TRAFFIC_IDS)}")
    _weighted_total(traffic, label="traffic mix")

    boundaries = document.get("measurement_boundaries", [])
    boundary_ids = {str(item["id"]) for item in boundaries}
    if boundary_ids != REQUIRED_BOUNDARIES:
        difference = sorted(boundary_ids ^ REQUIRED_BOUNDARIES)
        raise ValueError(f"measurement boundaries differ: {difference}")
    for boundary in boundaries:
        if boundary.get("cache_states") != ["cold", "warm"]:
            raise ValueError(f"{boundary['id']} must define cold and warm cache states")

    extraction = document.get("extraction_policy", {})
    if extraction.get("postgresql_gate_misses_required") != 2:
        raise ValueError("dedicated-search extraction requires two reproducible misses")
    if extraction.get("same_contract_and_profile_required") is not True:
        raise ValueError("extraction misses must use the same contract and profile")


def load_contract(path: Path = DEFAULT_CONTRACT_PATH) -> BenchmarkContract:
    resolved = path.resolve()
    raw = resolved.read_bytes()
    document = json.loads(raw)
    if not isinstance(document, dict):
        raise ValueError("benchmark contract must be a JSON object")
    validate_contract(document)
    return BenchmarkContract(path=resolved, document=document, sha256=sha256_bytes(raw))
