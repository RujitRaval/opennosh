from __future__ import annotations

from collections import Counter, defaultdict

from benchmarks.performance.contract import load_contract
from benchmarks.performance.corpus import generate_records


def test_launch_corpus_matches_every_pinned_distribution_exactly() -> None:
    contract = load_contract()
    records = list(generate_records(contract, "launch-reference"))
    expected = {
        dimension: {
            label: len(records) * int(weight) // 10_000 for label, weight in distribution.items()
        }
        for dimension, distribution in contract.document["distributions"].items()
    }
    observed = {
        "source": Counter(record.source for record in records),
        "locale_script": Counter(f"{record.locale}|{record.script}" for record in records),
        "name_length": Counter(record.name_length for record in records),
        "variant": Counter(record.variant for record in records),
        "duplicate_cluster": Counter(record.duplicate_cluster for record in records),
        "missing_field": Counter(record.missing_field for record in records),
        "license": Counter(record.license for record in records),
        "evidence": Counter(record.evidence for record in records),
        "projection_state": Counter(record.projection_state for record in records),
        "release_age": Counter(record.release_age for record in records),
    }
    assert {key: dict(value) for key, value in observed.items()} == expected


def test_duplicate_clusters_and_missing_evidence_shape_real_records() -> None:
    records = list(generate_records(load_contract(), "launch-reference"))
    clusters: defaultdict[str, list[str]] = defaultdict(list)
    for record in records:
        clusters[record.duplicate_cluster_id].append(record.name)
        if record.missing_field == "evidence":
            assert record.evidence == "none"

    sizes = Counter(len(names) for names in clusters.values())
    assert sizes == {1: 7_600, 2: 900, 10: 60}
    assert any(len(set(names)) == 1 for names in clusters.values() if len(names) > 1)
