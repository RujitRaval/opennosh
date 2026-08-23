"""Semantic validation for benchmark result bundles and extraction evidence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from benchmarks.performance.contract import CONTRACT_DIRECTORY, BenchmarkContract
from benchmarks.performance.metrics import evaluate_gates

RESULT_SCHEMA_PATH = CONTRACT_DIRECTORY / "result.schema.json"


def _result_schema() -> dict[str, Any]:
    schema = json.loads(RESULT_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return cast(dict[str, Any], schema)


def _validate_result_schema(result: Mapping[str, Any]) -> None:
    try:
        Draft202012Validator(_result_schema(), format_checker=FormatChecker()).validate(
            dict(result)
        )
    except ValidationError as error:
        location = ".".join(str(part) for part in error.absolute_path) or "document"
        raise ValueError(
            f"benchmark result schema violation at {location}: {error.message}"
        ) from error


def _required_measurement_cells(contract: BenchmarkContract) -> set[tuple[str, str, str]]:
    cells = {
        ("postgresql", "cold", "first_page_search"),
        ("postgresql", "warm", "first_page_search"),
    }
    for boundary in contract.document["measurement_boundaries"]:
        boundary_id = str(boundary["id"])
        if boundary_id == "postgresql":
            continue
        for cache_state in boundary["cache_states"]:
            cells.add((boundary_id, str(cache_state), "anonymous_read"))
    for workload in contract.document["traffic_mix"]:
        cells.add(("postgresql", "warm", str(workload["id"])))
    return cells


def _validate_identity(result: Mapping[str, Any], contract: BenchmarkContract) -> None:
    result_contract = result["contract"]
    assert isinstance(result_contract, Mapping)
    expected = {
        "id": contract.document["contract_id"],
        "schema_version": contract.document["schema_version"],
        "sha256": contract.sha256,
    }
    actual = {key: result_contract.get(key) for key in expected}
    if actual != expected:
        raise ValueError(
            f"benchmark result contract identity differs: expected {expected}, found {actual}"
        )
    contract.profile(str(result["profile"]))


def _validate_measurement_coverage(result: Mapping[str, Any], contract: BenchmarkContract) -> None:
    measurements = result["measurements"]
    assert isinstance(measurements, list)
    cells = [
        (
            str(measurement["boundary"]),
            str(measurement["cache_state"]),
            str(measurement["workload"]),
        )
        for measurement in measurements
    ]
    duplicates = sorted(cell for cell in set(cells) if cells.count(cell) > 1)
    if duplicates:
        raise ValueError(f"benchmark result contains duplicate measurement cells: {duplicates}")
    missing = sorted(_required_measurement_cells(contract) - set(cells))
    if missing:
        raise ValueError(f"benchmark result is missing required measurement cells: {missing}")


def _validated_artifact_path(artifact_directory: Path, relative_path: str) -> Path:
    candidate = Path(relative_path)
    if candidate.is_absolute():
        raise ValueError(f"benchmark artifact path must be relative: {relative_path}")
    root = artifact_directory.resolve()
    resolved = (root / candidate).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"benchmark artifact path escapes its bundle: {relative_path}")
    if not resolved.is_file():
        raise ValueError(f"benchmark artifact is missing: {relative_path}")
    return resolved


def _validate_query_plans(result: Mapping[str, Any], artifact_directory: Path) -> None:
    plans = result["query_plans"]
    assert isinstance(plans, list)
    required = {("first_page", "cold"), ("first_page", "warm")}
    cells: set[tuple[str, str]] = set()
    for plan in plans:
        query_id = str(plan["query_id"])
        cache_state = str(plan["cache_state"])
        cell = (query_id, cache_state)
        if cell in cells:
            raise ValueError(f"benchmark result contains a duplicate query plan: {cell}")
        cells.add(cell)
        relative_path = str(plan["path"])
        path = _validated_artifact_path(artifact_directory, relative_path)
        actual_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_digest != plan["sha256"]:
            raise ValueError(
                f"benchmark query plan digest differs for {relative_path}: "
                f"expected {plan['sha256']}, found {actual_digest}"
            )
    missing = sorted(required - cells)
    if missing:
        raise ValueError(f"benchmark result is missing required query plans: {missing}")


def _metric_evidence_value(value: object) -> float:
    if not isinstance(value, Mapping):
        raise ValueError("resource metric evidence must be an object")
    measured = value.get("value")
    if not isinstance(measured, (int, float)) or isinstance(measured, bool):
        raise ValueError("resource metric evidence value must be numeric")
    return float(measured)


def _validate_gate_evaluation(
    result: Mapping[str, Any],
    contract: BenchmarkContract,
) -> None:
    evaluation = result["gate_evaluation"]
    assert isinstance(evaluation, Mapping)
    failures = evaluation["failures"]
    assert isinstance(failures, list)
    if evaluation["passed"] is not (not failures):
        raise ValueError("benchmark gate passed flag must equal whether failures is empty")

    expected = evaluate_gates(
        result["measurements"],
        contract.document["gates"],
        postgresql_miss_streak=0,
    )
    expected.pop("postgresql_miss_streak")
    resources = result["resources"]
    database = result["environment"]["database"]
    capacity = contract.document["gates"]["capacity"]
    if int(resources["connections_peak"]) > int(
        int(database["connection_ceiling"]) * float(capacity["max_connection_utilization"])
    ):
        expected["failures"].append("database connection utilization exceeded")
    if _metric_evidence_value(resources["projection_lag_p95_ms"]) > float(
        capacity["max_projection_lag_ms"]
    ):
        expected["failures"].append("projection lag exceeded")
    if _metric_evidence_value(resources["job_age_p95_ms"]) > float(capacity["max_job_age_ms"]):
        expected["failures"].append("job age exceeded")
    if result["execution"]["diagnostic_override"]:
        expected["failures"].append(
            "diagnostic --requests override cannot produce passing benchmark evidence"
        )
    expected["passed"] = not expected["failures"]
    if dict(evaluation) != expected:
        raise ValueError("benchmark gate evaluation differs from measured evidence")


def _expected_weighted_counts(
    items: list[dict[str, Any]],
    count: int,
) -> dict[str, int]:
    allocations = [count * int(item["weight_bps"]) // 10_000 for item in items]
    remainders = [count * int(item["weight_bps"]) % 10_000 for item in items]
    remaining = count - sum(allocations)
    ranked = sorted(
        range(len(items)),
        key=lambda index: (-remainders[index], str(items[index]["id"])),
    )
    for index in ranked[:remaining]:
        allocations[index] += 1
    return {
        str(item["id"]): allocation for item, allocation in zip(items, allocations, strict=True)
    }


def _validate_execution(result: Mapping[str, Any], contract: BenchmarkContract) -> None:
    execution = result["execution"]
    assert isinstance(execution, Mapping)
    profile = contract.profile(str(result["profile"]))
    profile_interactions = int(profile["interactions_per_boundary_cache"])
    configured = int(execution["configured_interactions_per_cell"])
    diagnostic = bool(execution["diagnostic_override"])
    if int(execution["profile_interactions_per_cell"]) != profile_interactions:
        raise ValueError("execution profile interaction count differs from the contract")
    if diagnostic is not (configured != profile_interactions):
        raise ValueError("execution diagnostic flag must reflect an interaction-count override")

    cells = execution["cells"]
    assert isinstance(cells, list)
    expected_cells = {
        (boundary, cache_state)
        for boundary in ("fastapi", "same_origin_proxy", "edge_browser")
        for cache_state in ("cold", "warm")
    }
    actual_cells = [(str(cell["boundary"]), str(cell["cache_state"])) for cell in cells]
    if set(actual_cells) != expected_cells or len(actual_cells) != len(expected_cells):
        raise ValueError("execution must contain each boundary/cache cell exactly once")
    for cell in cells:
        target = int(cell["target_interactions"])
        mixed_target = int(cell["mixed_target_interactions"])
        if target != configured or int(cell["completed_interactions"]) != target:
            raise ValueError("execution boundary interactions are incomplete")
        if (
            mixed_target != int(execution["mixed_interactions_per_cell"])
            or int(cell["mixed_completed_interactions"]) != mixed_target
        ):
            raise ValueError("execution mixed interactions are incomplete")
        query_counts = {str(key): int(value) for key, value in cell["query_counts"].items()}
        if query_counts != _expected_weighted_counts(contract.document["query_mix"], target):
            raise ValueError("execution query counts differ from the exact pinned mix")
        traffic_counts = {str(key): int(value) for key, value in cell["traffic_counts"].items()}
        if traffic_counts != _expected_weighted_counts(
            contract.document["traffic_mix"], mixed_target
        ):
            raise ValueError("execution traffic counts differ from the exact pinned mix")

    failures = [str(item) for item in result["gate_evaluation"]["failures"]]
    has_diagnostic_failure = any("diagnostic --requests override" in item for item in failures)
    if diagnostic != has_diagnostic_failure:
        raise ValueError("diagnostic execution must carry its non-passing gate failure")


def _validate_seed_evidence(
    result: Mapping[str, Any],
    contract: BenchmarkContract,
) -> None:
    reproducibility = result["reproducibility"]
    assert isinstance(reproducibility, Mapping)
    evidence = reproducibility.get("seed_evidence")
    if evidence is None:
        return
    assert isinstance(evidence, Mapping)
    records = int(evidence["records"])
    expected_records = int(contract.profile(str(result["profile"]))["records"])
    if records != expected_records:
        raise ValueError("seed evidence record count differs from the profile")
    expected = {
        dimension: {
            label: records * int(weight) // 10_000 for label, weight in distribution.items()
        }
        for dimension, distribution in contract.document["distributions"].items()
    }
    if evidence["observed_distributions"] != expected:
        raise ValueError("seed evidence distributions differ from the pinned corpus")
    projection = expected["projection_state"]
    expected_snapshots = {
        "retained_active": projection["retained_active"],
        "retained_previous": (projection["retained_active"] + projection["retained_previous"]),
    }
    if evidence["snapshot_counts"] != expected_snapshots:
        raise ValueError("seed evidence snapshot counts differ from projection states")


def _validate_artifact_manifest(
    result: Mapping[str, Any],
    artifact_directory: Path,
) -> None:
    manifest_path = _validated_artifact_path(artifact_directory, "artifact-manifest.json")
    reproducibility = result["reproducibility"]
    assert isinstance(reproducibility, Mapping)
    actual_manifest_digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    if actual_manifest_digest != reproducibility["artifact_manifest_sha256"]:
        raise ValueError("benchmark artifact manifest digest differs from result metadata")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != "1.0.0"
        or not isinstance(manifest.get("files"), list)
    ):
        raise ValueError("benchmark artifact manifest has an invalid shape")
    entries = manifest["files"]
    paths: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "bytes"}:
            raise ValueError("benchmark artifact manifest contains an invalid file entry")
        relative_path = str(entry["path"])
        if relative_path in paths:
            raise ValueError(f"benchmark artifact manifest repeats {relative_path}")
        paths.append(relative_path)
        path = _validated_artifact_path(artifact_directory, relative_path)
        if entry["bytes"] != path.stat().st_size:
            raise ValueError(f"benchmark artifact size differs for {relative_path}")
        if entry["sha256"] != hashlib.sha256(path.read_bytes()).hexdigest():
            raise ValueError(f"benchmark artifact digest differs for {relative_path}")

    plan_paths = {str(plan["path"]) for plan in result["query_plans"]}
    required_paths = {
        "environment.json",
        "query-seed.json",
        "resource-evidence.json",
        *plan_paths,
    }
    missing = sorted(required_paths - set(paths))
    if missing:
        raise ValueError(f"benchmark artifact manifest omits required files: {missing}")

    environment = json.loads((artifact_directory / "environment.json").read_text())
    if environment != result["environment"]:
        raise ValueError("environment artifact differs from embedded result metadata")
    query_seed = json.loads((artifact_directory / "query-seed.json").read_text())
    if query_seed.get("seed") != result["query_seed"]:
        raise ValueError("query-seed artifact differs from embedded result metadata")


def postgresql_gate_missed(
    result: Mapping[str, Any],
    contract: BenchmarkContract,
) -> bool:
    """Return whether measured PostgreSQL latency misses a pinned cold/warm gate."""

    gates = contract.document["gates"]["postgresql"]
    measurements = result["measurements"]
    assert isinstance(measurements, list)
    for measurement in measurements:
        if (
            measurement["boundary"] != "postgresql"
            or measurement["workload"] != "first_page_search"
        ):
            continue
        cache_state = str(measurement["cache_state"])
        latency = measurement["latency_ms"]
        assert isinstance(latency, Mapping)
        for percentile_name in ("p95", "p99"):
            gate_name = f"{cache_state}_{percentile_name}_ms"
            if gate_name in gates and float(latency[percentile_name]) > float(gates[gate_name]):
                return True
    return False


def validate_result_bundle(
    result: Mapping[str, Any],
    contract: BenchmarkContract,
    artifact_directory: Path,
) -> None:
    """Validate schema, bundle integrity, evidence coverage, and pass consistency."""

    _validate_result_schema(result)
    _validate_identity(result, contract)
    _validate_measurement_coverage(result, contract)
    _validate_execution(result, contract)
    _validate_seed_evidence(result, contract)
    _validate_query_plans(result, artifact_directory)
    _validate_artifact_manifest(result, artifact_directory)
    _validate_gate_evaluation(result, contract)
