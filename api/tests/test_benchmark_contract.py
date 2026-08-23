from __future__ import annotations

import json

from jsonschema import Draft202012Validator, FormatChecker

from benchmarks.performance.contract import CONTRACT_DIRECTORY, load_contract


def test_performance_contract_matches_its_versioned_json_schema() -> None:
    contract = load_contract()
    schema = json.loads((CONTRACT_DIRECTORY / "contract.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(contract.document)


def test_result_artifact_schema_is_valid_and_requires_every_measurement_class() -> None:
    schema = json.loads((CONTRACT_DIRECTORY / "result.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    required_resources = set(schema["properties"]["resources"]["required"])
    assert required_resources == {
        "memory_high_water_bytes",
        "connections_peak",
        "index_size_bytes",
        "index_build_ms",
        "job_age_p95_ms",
        "projection_lag_p95_ms",
    }
    latency_required = set(schema["$defs"]["measurement"]["properties"]["latency_ms"]["required"])
    assert latency_required == {"p50", "p95", "p99"}
