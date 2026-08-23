from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from benchmarks.performance.contract import canonical_json_bytes, load_contract
from benchmarks.performance.extraction import evaluate_extraction
from benchmarks.performance.result_validation import (
    postgresql_gate_missed,
    validate_result_bundle,
)


def _measurement(boundary: str, cache_state: str, workload: str) -> dict[str, Any]:
    return {
        "boundary": boundary,
        "cache_state": cache_state,
        "workload": workload,
        "requests": 10,
        "latency_ms": {"p50": 1, "p95": 2, "p99": 3},
        "error_rate": 0,
        "timeout_rate": 0,
        "judged_relevance": 1,
        "error_codes": {},
    }


def _apportion(items: list[dict[str, Any]], count: int) -> dict[str, int]:
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


def _evidence(value: int | float) -> dict[str, Any]:
    return {
        "value": value,
        "source": "test-observer",
        "observed_at": "2026-08-23T00:00:00Z",
    }


def _write_manifest(directory: Path, paths: list[Path]) -> str:
    entries = [
        {
            "path": str(path.relative_to(directory)),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
        }
        for path in sorted(paths)
    ]
    path = directory / "artifact-manifest.json"
    path.write_bytes(canonical_json_bytes({"schema_version": "1.0.0", "files": entries}))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _result(contract: Any, artifact_directory: Path) -> dict[str, Any]:
    artifact_directory.mkdir(parents=True, exist_ok=True)
    plans = []
    artifact_paths: list[Path] = []
    for cache_state in ("cold", "warm"):
        relative_path = f"plans/first-page-{cache_state}.json"
        path = artifact_directory / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([{"Execution Time": 1}]))
        artifact_paths.append(path)
        plans.append(
            {
                "query_id": "first_page",
                "cache_state": cache_state,
                "path": relative_path,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )

    measurements = [
        _measurement("postgresql", cache_state, "first_page_search")
        for cache_state in ("cold", "warm")
    ]
    measurements.extend(
        _measurement(boundary, cache_state, "anonymous_read")
        for boundary in ("fastapi", "same_origin_proxy", "edge_browser")
        for cache_state in ("cold", "warm")
    )
    measurements.extend(
        _measurement("postgresql", "warm", workload["id"])
        for workload in contract.document["traffic_mix"]
    )
    environment = {
        "hardware": {
            "machine": "test",
            "cpu_count": 4,
            "memory_bytes": 1024,
            "platform": "test",
        },
        "database": {
            "postgresql_version": "16",
            "configuration": {},
            "connection_ceiling": 100,
        },
        "software": {
            "opennosh_version": "0.27.0.0",
            "git_sha": "0123456789abcdef",
            "python_version": "3.11",
            "httpx_version": "0.28",
        },
        "cache_preparation": contract.document["measurement_boundaries"],
    }
    resources = {
        "memory_high_water_bytes": {
            role: _evidence(1024)
            for role in ("postgresql", "fastapi", "same_origin_proxy", "edge_browser")
        },
        "connections_peak": 10,
        "index_size_bytes": 2048,
        "index_build_ms": _evidence(5),
        "job_age_p95_ms": _evidence(6),
        "projection_lag_p95_ms": _evidence(7),
    }
    for name, document in (
        ("environment.json", environment),
        (
            "query-seed.json",
            {"seed": contract.seed, "query_mix": contract.document["query_mix"]},
        ),
        (
            "resource-evidence.json",
            {
                "memory_high_water_bytes": resources["memory_high_water_bytes"],
                "index_build_ms": resources["index_build_ms"],
                "job_age_p95_ms": resources["job_age_p95_ms"],
                "projection_lag_p95_ms": resources["projection_lag_p95_ms"],
            },
        ),
    ):
        path = artifact_directory / name
        path.write_bytes(canonical_json_bytes(document))
        artifact_paths.append(path)

    target = int(contract.profile("launch-reference")["interactions_per_boundary_cache"])
    mixed_target = 100
    query_counts = _apportion(contract.document["query_mix"], target)
    traffic_counts = _apportion(contract.document["traffic_mix"], mixed_target)
    cells = [
        {
            "boundary": boundary,
            "cache_state": cache_state,
            "target_interactions": target,
            "completed_interactions": target,
            "mixed_target_interactions": mixed_target,
            "mixed_completed_interactions": mixed_target,
            "query_counts": query_counts,
            "traffic_counts": traffic_counts,
            "measurement_elapsed_ms": 100,
            "mixed_elapsed_ms": 100,
            "cell_elapsed_ms": 100,
            "achieved_interactions_per_second": 7200,
            "connection_observation_samples": 2,
        }
        for boundary in ("fastapi", "same_origin_proxy", "edge_browser")
        for cache_state in ("cold", "warm")
    ]
    manifest_sha256 = _write_manifest(artifact_directory, artifact_paths)
    return {
        "schema_version": "1.0.0",
        "run_id": "20260823T000000Z-launch-reference-0123456789ab",
        "contract": {
            "id": contract.document["contract_id"],
            "schema_version": contract.document["schema_version"],
            "sha256": contract.sha256,
        },
        "profile": "launch-reference",
        "started_at": "2026-08-23T00:00:00Z",
        "finished_at": "2026-08-23T00:01:00Z",
        "environment": environment,
        "execution": {
            "mode": "fixed_interactions",
            "profile_interactions_per_cell": target,
            "configured_interactions_per_cell": target,
            "mixed_interactions_per_cell": mixed_target,
            "diagnostic_override": False,
            "cells": cells,
        },
        "query_seed": contract.seed,
        "measurements": measurements,
        "resources": resources,
        "query_plans": plans,
        "gate_evaluation": {"passed": True, "failures": []},
        "reproducibility": {
            "corpus_sha256": "a" * 64,
            "command": ["benchmark", "--database-url", "[REDACTED]"],
            "artifact_manifest_sha256": manifest_sha256,
        },
    }


def test_valid_result_bundle_covers_contract_and_artifacts(tmp_path: Path) -> None:
    contract = load_contract()
    validate_result_bundle(_result(contract, tmp_path), contract, tmp_path)


def test_result_bundle_rejects_missing_measurement_cell(tmp_path: Path) -> None:
    contract = load_contract()
    result = _result(contract, tmp_path)
    result["measurements"] = result["measurements"][1:]
    with pytest.raises(ValueError, match="missing required measurement cells"):
        validate_result_bundle(result, contract, tmp_path)


def test_result_bundle_rejects_missing_or_changed_plan(tmp_path: Path) -> None:
    contract = load_contract()
    result = _result(contract, tmp_path)
    (tmp_path / result["query_plans"][0]["path"]).write_text("changed")
    with pytest.raises(ValueError, match="query plan digest differs"):
        validate_result_bundle(result, contract, tmp_path)


def test_result_bundle_rejects_inconsistent_passed_flag(tmp_path: Path) -> None:
    contract = load_contract()
    result = _result(contract, tmp_path)
    result["gate_evaluation"] = {
        "passed": True,
        "failures": ["fastapi/warm error rate exceeded"],
    }
    with pytest.raises(ValueError, match="passed flag"):
        validate_result_bundle(result, contract, tmp_path)


def test_result_bundle_rejects_capacity_gate_omitted_from_result(tmp_path: Path) -> None:
    contract = load_contract()
    result = _result(contract, tmp_path)
    result["resources"]["connections_peak"] = 90
    with pytest.raises(ValueError, match="differs from measured evidence"):
        validate_result_bundle(result, contract, tmp_path)


def test_result_bundle_rejects_manifest_digest_or_omission(tmp_path: Path) -> None:
    contract = load_contract()
    result = _result(contract, tmp_path)
    result["reproducibility"]["artifact_manifest_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="manifest digest differs"):
        validate_result_bundle(result, contract, tmp_path)

    result = _result(contract, tmp_path)
    manifest_path = tmp_path / "artifact-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"] = [
        entry for entry in manifest["files"] if entry["path"] != "resource-evidence.json"
    ]
    manifest_path.write_bytes(canonical_json_bytes(manifest))
    result["reproducibility"]["artifact_manifest_sha256"] = hashlib.sha256(
        manifest_path.read_bytes()
    ).hexdigest()
    with pytest.raises(ValueError, match="omits required files"):
        validate_result_bundle(result, contract, tmp_path)


def test_result_bundle_rejects_plan_path_outside_bundle(tmp_path: Path) -> None:
    contract = load_contract()
    result = _result(contract, tmp_path)
    result["query_plans"][0]["path"] = "../outside.json"
    with pytest.raises(ValueError, match="escapes its bundle"):
        validate_result_bundle(result, contract, tmp_path)


def test_result_bundle_rejects_incomplete_execution(tmp_path: Path) -> None:
    contract = load_contract()
    result = _result(contract, tmp_path)
    result["execution"]["cells"][0]["completed_interactions"] -= 1
    with pytest.raises(ValueError, match="interactions are incomplete"):
        validate_result_bundle(result, contract, tmp_path)


def test_postgresql_miss_is_derived_from_measurements(tmp_path: Path) -> None:
    contract = load_contract()
    result = _result(contract, tmp_path)
    assert not postgresql_gate_missed(result, contract)
    cold = next(
        item
        for item in result["measurements"]
        if item["boundary"] == "postgresql" and item["cache_state"] == "cold"
    )
    cold["latency_ms"]["p95"] = 251
    assert postgresql_gate_missed(result, contract)


def test_two_complete_matching_runs_trigger_extraction(tmp_path: Path) -> None:
    contract = load_contract()
    directories = [tmp_path / "first", tmp_path / "second"]
    for index, directory in enumerate(directories):
        result = _result(contract, directory)
        result["run_id"] = f"2026082{index + 2}T000000Z-launch-reference-0123456789ab"
        result["finished_at"] = f"2026-08-2{index + 2}T00:01:00Z"
        cold = next(
            item
            for item in result["measurements"]
            if item["boundary"] == "postgresql" and item["cache_state"] == "cold"
        )
        cold["latency_ms"]["p95"] = 251
        result["gate_evaluation"] = {
            "passed": False,
            "failures": ["postgresql/cold p95 251.000ms > 250.000ms"],
        }
        (directory / "result.json").write_bytes(canonical_json_bytes(result))

    evaluation = evaluate_extraction(directories)
    assert evaluation["triggered"] is True
    assert evaluation["reason"] == "consecutive_postgresql_gate_misses"
    assert evaluation["consecutive_postgresql_misses"] == 2


def test_extraction_rejects_mismatched_corpus(tmp_path: Path) -> None:
    contract = load_contract()
    directories = [tmp_path / "first", tmp_path / "second"]
    for index, directory in enumerate(directories):
        result = _result(contract, directory)
        result["run_id"] = f"run-{index}"
        result["finished_at"] = f"2026-08-2{index + 2}T00:01:00Z"
        if index:
            result["reproducibility"]["corpus_sha256"] = "c" * 64
        (directory / "result.json").write_bytes(canonical_json_bytes(result))
    with pytest.raises(ValueError, match="same corpus digest"):
        evaluate_extraction(directories)


def test_load_contract_rejects_documents_outside_versioned_schema(tmp_path: Path) -> None:
    source = Path("benchmarks/performance/contract.v1.json")
    document = json.loads(source.read_text())
    document["unreviewed_field"] = True
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="schema violation"):
        load_contract(path)
