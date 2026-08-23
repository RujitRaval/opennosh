"""Evaluate dedicated-search extraction from complete benchmark artifact bundles."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from benchmarks.performance.contract import (
    DEFAULT_CONTRACT_PATH,
    canonical_json_bytes,
    load_contract,
)
from benchmarks.performance.result_validation import (
    postgresql_gate_missed,
    validate_result_bundle,
)


def evaluate_extraction(
    artifact_directories: list[Path],
    *,
    contract_path: Path = DEFAULT_CONTRACT_PATH,
) -> dict[str, Any]:
    if not artifact_directories:
        raise ValueError("provide at least one benchmark artifact directory")
    contract = load_contract(contract_path)
    results: list[tuple[Path, dict[str, Any]]] = []
    for directory in artifact_directories:
        resolved = directory.resolve()
        document = json.loads((resolved / "result.json").read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError(f"benchmark result must be an object: {resolved}")
        validate_result_bundle(document, contract, resolved)
        results.append((resolved, document))

    profile = str(results[0][1]["profile"])
    corpus_sha256 = str(results[0][1]["reproducibility"]["corpus_sha256"])
    run_ids: set[str] = set()
    previous_finished: datetime | None = None
    streak = 0
    runs: list[dict[str, Any]] = []
    immediate_threat = False
    for directory, result in results:
        if result["profile"] != profile:
            raise ValueError("extraction evidence must use the same benchmark profile")
        if result["reproducibility"]["corpus_sha256"] != corpus_sha256:
            raise ValueError("extraction evidence must use the same corpus digest")
        run_id = str(result["run_id"])
        if run_id in run_ids:
            raise ValueError("extraction evidence cannot repeat a benchmark run")
        run_ids.add(run_id)
        finished_at = datetime.fromisoformat(str(result["finished_at"]).replace("Z", "+00:00"))
        if previous_finished is not None and finished_at <= previous_finished:
            raise ValueError("extraction evidence must be ordered chronologically")
        previous_finished = finished_at
        missed = postgresql_gate_missed(result, contract)
        streak = streak + 1 if missed else 0
        failures = [str(item) for item in result["gate_evaluation"]["failures"]]
        write_threat = "database connection utilization exceeded" in failures
        immediate_threat = immediate_threat or write_threat
        runs.append(
            {
                "run_id": run_id,
                "artifact_directory": str(directory),
                "postgresql_gate_missed": missed,
                "write_capacity_threat": write_threat,
                "consecutive_postgresql_misses": streak,
            }
        )

    required = int(contract.document["extraction_policy"]["postgresql_gate_misses_required"])
    triggered = immediate_threat or streak >= required
    reason = (
        "write_capacity_threat"
        if immediate_threat
        else "consecutive_postgresql_gate_misses"
        if streak >= required
        else "evidence_threshold_not_met"
    )
    return {
        "schema_version": "1.0.0",
        "contract": {
            "id": contract.document["contract_id"],
            "sha256": contract.sha256,
        },
        "profile": profile,
        "corpus_sha256": corpus_sha256,
        "runs": runs,
        "postgresql_misses_required": required,
        "consecutive_postgresql_misses": streak,
        "triggered": triggered,
        "reason": reason,
        "decision": contract.document["extraction_policy"]["decision"],
    }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("artifact_directories", nargs="+", type=Path)
    value.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT_PATH)
    value.add_argument("--output", type=Path)
    return value


def main() -> int:
    arguments = parser().parse_args()
    evaluation = evaluate_extraction(
        arguments.artifact_directories,
        contract_path=arguments.contract,
    )
    encoded = canonical_json_bytes(evaluation)
    if arguments.output is None:
        print(encoded.decode(), end="")
    else:
        arguments.output.write_bytes(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
