from __future__ import annotations

import hashlib
import json
import unittest

from benchmarks.performance.contract import load_contract
from benchmarks.performance.corpus import generate_records
from benchmarks.performance.metrics import Sample, evaluate_gates, percentile, summarize_samples


class PerformanceContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = load_contract()

    def test_profiles_are_exact_capacity_multiples(self) -> None:
        profiles = self.contract.document["profiles"]
        self.assertEqual(
            [(profile["id"], profile["records"]) for profile in profiles],
            [("launch-reference", 10_000), ("10x", 100_000), ("100x", 1_000_000)],
        )

    def test_generator_is_byte_deterministic(self) -> None:
        first = b"".join(
            record.json_bytes()
            for record in generate_records(self.contract, "launch-reference", count=100)
        )
        second = b"".join(
            record.json_bytes()
            for record in generate_records(self.contract, "launch-reference", count=100)
        )
        self.assertEqual(first, second)
        self.assertEqual(hashlib.sha256(first).hexdigest(), hashlib.sha256(second).hexdigest())

    def test_generator_contains_all_pinned_search_landmarks(self) -> None:
        records = list(generate_records(self.contract, "launch-reference", count=100))
        serialized = json.dumps(
            [record.__dict__ for record in records], ensure_ascii=False, sort_keys=True
        )
        for landmark in (
            "benchmark-exact",
            "Blueberry",
            "Quinoa",
            "Strawberry",
            "豆腐",
            "Regional pulse",
        ):
            self.assertIn(landmark, serialized)

    def test_every_generated_dimension_is_represented(self) -> None:
        records = list(generate_records(self.contract, "launch-reference", count=10_000))
        expected = self.contract.document["distributions"]
        observed = {
            "source": {record.source for record in records},
            "locale_script": {f"{record.locale}|{record.script}" for record in records},
            "name_length": {record.name_length for record in records},
            "variant": {record.variant for record in records},
            "duplicate_cluster": {record.duplicate_cluster for record in records},
            "missing_field": {record.missing_field for record in records},
            "license": {record.license for record in records},
            "evidence": {record.evidence for record in records},
            "projection_state": {record.projection_state for record in records},
            "release_age": {record.release_age for record in records},
        }
        for dimension, labels in observed.items():
            self.assertEqual(labels, set(expected[dimension]), dimension)

    def test_percentiles_interpolate_and_measurements_never_use_averages(self) -> None:
        self.assertEqual(percentile([10, 20, 30, 40], 50), 25)
        measurement = summarize_samples(
            boundary="fastapi",
            cache_state="warm",
            workload="anonymous_read",
            samples=[Sample(10), Sample(20), Sample(30, relevant=False), Sample(40, error=True)],
        )
        self.assertEqual(set(measurement["latency_ms"]), {"p50", "p95", "p99"})
        self.assertEqual(measurement["error_rate"], 0.25)
        self.assertEqual(measurement["judged_relevance"], 0.75)

    def test_gate_evaluation_requires_latency_quality_and_two_misses_for_extraction(self) -> None:
        measurement = summarize_samples(
            boundary="postgresql",
            cache_state="warm",
            workload="first_page_search",
            samples=[Sample(150)],
        )
        evaluation = evaluate_gates(
            [measurement], self.contract.document["gates"], postgresql_miss_streak=1
        )
        self.assertFalse(evaluation["passed"])
        self.assertEqual(evaluation["postgresql_miss_streak"], 1)
        self.assertEqual(
            self.contract.document["extraction_policy"]["postgresql_gate_misses_required"], 2
        )


if __name__ == "__main__":
    unittest.main()
