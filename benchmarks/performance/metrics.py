"""Statistics and gate evaluation for versioned benchmark artifacts."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


def percentile(values: Iterable[float], percentile_value: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("cannot calculate a percentile of an empty sample")
    if not 0 <= percentile_value <= 100:
        raise ValueError("percentile must be between 0 and 100")
    rank = (len(ordered) - 1) * percentile_value / 100
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    fraction = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


@dataclass(frozen=True)
class Sample:
    latency_ms: float
    error: bool = False
    timeout: bool = False
    relevant: bool = True
    error_code: str | None = None


def summarize_samples(
    *, boundary: str, cache_state: str, workload: str, samples: list[Sample]
) -> dict[str, object]:
    if not samples:
        raise ValueError("a benchmark measurement requires at least one sample")
    latencies = [sample.latency_ms for sample in samples]
    return {
        "boundary": boundary,
        "cache_state": cache_state,
        "workload": workload,
        "requests": len(samples),
        "latency_ms": {
            "p50": round(percentile(latencies, 50), 3),
            "p95": round(percentile(latencies, 95), 3),
            "p99": round(percentile(latencies, 99), 3),
        },
        "error_rate": sum(sample.error for sample in samples) / len(samples),
        "timeout_rate": sum(sample.timeout for sample in samples) / len(samples),
        "judged_relevance": sum(sample.relevant for sample in samples) / len(samples),
        "error_codes": dict(
            sorted(Counter(sample.error_code for sample in samples if sample.error_code).items())
        ),
    }


def evaluate_gates(
    measurements: list[dict[str, Any]],
    gates: dict[str, Any],
    *,
    postgresql_miss_streak: int,
) -> dict[str, Any]:
    failures: list[str] = []
    quality = gates["quality"]
    assert isinstance(quality, dict)
    for measurement in measurements:
        boundary = str(measurement["boundary"])
        cache_state = str(measurement["cache_state"])
        latency = measurement["latency_ms"]
        assert isinstance(latency, dict)
        boundary_gates = gates.get(boundary, {})
        assert isinstance(boundary_gates, dict)
        for percentile_name in ("p95", "p99"):
            gate_name = f"{cache_state}_{percentile_name}_ms"
            search_workload = measurement["workload"] in {
                "anonymous_read",
                "first_page_search",
            }
            if search_workload and gate_name in boundary_gates:
                actual = float(latency[percentile_name])
                limit = float(boundary_gates[gate_name])
                if actual > limit:
                    failures.append(
                        f"{boundary}/{cache_state} {percentile_name} {actual:.3f}ms > {limit:.3f}ms"
                    )
        if float(measurement["error_rate"]) > float(quality["max_error_rate"]):
            failures.append(f"{boundary}/{cache_state} error rate exceeded")
        if float(measurement["timeout_rate"]) > float(quality["max_timeout_rate"]):
            failures.append(f"{boundary}/{cache_state} timeout rate exceeded")
        if float(measurement["judged_relevance"]) < float(quality["min_judged_relevance"]):
            failures.append(f"{boundary}/{cache_state} judged relevance missed")
    return {
        "passed": not failures,
        "failures": failures,
        "postgresql_miss_streak": postgresql_miss_streak,
    }
