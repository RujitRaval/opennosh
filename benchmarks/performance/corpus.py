"""Deterministic streaming corpus generator for the benchmark contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, TextIO

from benchmarks.performance.contract import (
    DEFAULT_CONTRACT_PATH,
    BenchmarkContract,
    canonical_json_bytes,
    load_contract,
)

LANDMARKS: dict[int, tuple[str, str, str | None, str, str]] = {
    0: ("benchmark-exact", "Benchmark exact apple", None, "en-US", "Latin"),
    1: ("benchmark-blueberry", "Blueberry benchmark food", None, "en-US", "Latin"),
    2: ("benchmark-quinoa", "Quinoa benchmark food", None, "en-US", "Latin"),
    3: ("benchmark-strawberry", "Strawberry benchmark food", None, "en-US", "Latin"),
    4: ("benchmark-tofu", "Tofu benchmark food", "豆腐 ベンチマーク食品", "ja", "Japanese"),
    5: (
        "benchmark-regional-pulse",
        "Regional pulse benchmark food",
        "Legumbre regional de referencia",
        "es-MX",
        "Latin",
    ),
}

LOCAL_NAMES = {
    "en-US|Latin": "Benchmark food",
    "es-MX|Latin": "Alimento de referencia",
    "fr-FR|Latin": "Aliment de référence",
    "ar|Arabic": "غذاء معياري",
    "hi|Devanagari": "मानक भोजन",
    "ja|Japanese": "基準食品",
    "zh-Hans|Han": "基准食品",
}


@dataclass(frozen=True)
class CorpusRecord:
    ordinal: int
    source: str
    source_id: str
    pack_id: str | None
    release_id: str
    locale: str
    script: str
    name: str
    name_local: str | None
    name_length: str
    variant: str
    duplicate_cluster: str
    duplicate_cluster_id: str
    missing_field: str
    license: str
    evidence: str
    projection_state: str
    release_age: str
    category: str
    provenance: str
    nutrients: dict[str, float]

    def json_bytes(self) -> bytes:
        return canonical_json_bytes(asdict(self))


def _stable_parameters(seed: int, dimension: str, count: int) -> tuple[int, int]:
    digest = hashlib.sha256(f"{seed}:{dimension}".encode()).digest()
    multiplier = int.from_bytes(digest[:8], "big") % max(count, 1)
    multiplier |= 1
    while count > 1 and math.gcd(multiplier, count) != 1:
        multiplier += 2
    offset = int.from_bytes(digest[8:16], "big") % max(count, 1)
    return multiplier, offset


def _pick(
    distribution: Mapping[str, int],
    *,
    ordinal: int,
    count: int,
    seed: int,
    dimension: str,
) -> str:
    multiplier, offset = _stable_parameters(seed, dimension, count)
    slot = (ordinal * multiplier + offset) % count
    point = slot * 10_000 // count
    cumulative = 0
    for label, weight in distribution.items():
        cumulative += int(weight)
        if point < cumulative:
            return label
    raise AssertionError(f"invalid distribution for {dimension}")


def _sized_name(base: str, category: str, ordinal: int, *, maximum_length: int) -> str:
    target = min(
        {"short": 18, "medium": 72, "long": 220, "maximum": 490}[category],
        maximum_length,
    )
    suffix = f" {ordinal:07d}"
    repeated = f"{base} " * ((target // (len(base) + 1)) + 2)
    return (repeated[: max(target - len(suffix), 1)].rstrip() + suffix)[:maximum_length]


def _landmark_dimension_overrides(
    contract: BenchmarkContract,
    record_count: int,
) -> dict[str, dict[int, str]]:
    """Move landmark labels without changing any pinned distribution total."""
    distributions: dict[str, dict[str, int]] = contract.document["distributions"]
    required: dict[str, dict[int, str]] = {
        "source": {ordinal: "community" for ordinal in LANDMARKS},
        "locale_script": {
            ordinal: f"{landmark[3]}|{landmark[4]}" for ordinal, landmark in LANDMARKS.items()
        },
        "projection_state": {ordinal: "retained_active" for ordinal in LANDMARKS},
    }
    overrides: dict[str, dict[int, str]] = defaultdict(dict)
    for dimension, forced in required.items():
        original = {
            ordinal: _pick(
                distributions[dimension],
                ordinal=ordinal,
                count=record_count,
                seed=contract.seed,
                dimension=dimension,
            )
            for ordinal in range(record_count)
        }
        reserved = set(forced)
        for ordinal, desired in forced.items():
            current = overrides[dimension].get(ordinal, original[ordinal])
            if current == desired:
                continue
            partner = next(
                candidate
                for candidate in range(len(LANDMARKS), record_count)
                if candidate not in reserved
                and overrides[dimension].get(candidate, original[candidate]) == desired
            )
            overrides[dimension][ordinal] = desired
            overrides[dimension][partner] = current
            reserved.add(partner)
    return {dimension: dict(values) for dimension, values in overrides.items()}


def _evidence_overrides(
    contract: BenchmarkContract,
    record_count: int,
) -> dict[int, str]:
    distributions: dict[str, dict[str, int]] = contract.document["distributions"]
    missing = {
        ordinal: _pick(
            distributions["missing_field"],
            ordinal=ordinal,
            count=record_count,
            seed=contract.seed,
            dimension="missing_field",
        )
        for ordinal in range(record_count)
    }
    evidence = {
        ordinal: _pick(
            distributions["evidence"],
            ordinal=ordinal,
            count=record_count,
            seed=contract.seed,
            dimension="evidence",
        )
        for ordinal in range(record_count)
    }
    overrides: dict[int, str] = {}
    reserved: set[int] = set()
    for ordinal in range(record_count):
        if missing[ordinal] != "evidence" or evidence[ordinal] == "none":
            continue
        partner = next(
            candidate
            for candidate in range(record_count)
            if candidate not in reserved
            and missing[candidate] != "evidence"
            and overrides.get(candidate, evidence[candidate]) == "none"
        )
        overrides[ordinal] = "none"
        overrides[partner] = evidence[ordinal]
        reserved.add(partner)
    return overrides


def _cluster_ids(contract: BenchmarkContract, record_count: int) -> dict[int, str]:
    distribution = contract.document["distributions"]["duplicate_cluster"]
    ranks: defaultdict[str, int] = defaultdict(int)
    result: dict[int, str] = {}
    for ordinal in range(record_count):
        cluster = _pick(
            distribution,
            ordinal=ordinal,
            count=record_count,
            seed=contract.seed,
            dimension="duplicate_cluster",
        )
        divisor = {"singleton": 1, "pair": 2, "dense": 10}[cluster]
        result[ordinal] = f"cluster-{cluster}-{ranks[cluster] // divisor:07d}"
        ranks[cluster] += 1
    return result


def generate_records(
    contract: BenchmarkContract,
    profile_id: str,
    *,
    count: int | None = None,
) -> Iterator[CorpusRecord]:
    profile = contract.profile(profile_id)
    record_count = int(profile["records"]) if count is None else count
    if record_count < 100:
        raise ValueError("record count must be at least 100 for representative smoke data")
    distributions: dict[str, dict[str, int]] = contract.document["distributions"]
    seed = contract.seed
    packs = int(profile["packs"])
    releases = int(profile["releases"])
    landmark_overrides = _landmark_dimension_overrides(contract, record_count)
    evidence_overrides = _evidence_overrides(contract, record_count)
    cluster_ids = _cluster_ids(contract, record_count)

    for ordinal in range(record_count):
        picked = {
            dimension: _pick(
                distribution,
                ordinal=ordinal,
                count=record_count,
                seed=seed,
                dimension=dimension,
            )
            for dimension, distribution in distributions.items()
        }
        for dimension, values in landmark_overrides.items():
            if ordinal in values:
                picked[dimension] = values[ordinal]
        source = picked["source"]
        locale, script = picked["locale_script"].split("|", 1)
        source_id = f"benchmark-{source}-{ordinal:07d}"
        local_name: str | None = LOCAL_NAMES[picked["locale_script"]]
        variant = picked["variant"]
        base_name = (
            "Regional pulse benchmark food"
            if variant == "conflicting"
            else f"Benchmark food {picked['duplicate_cluster']}"
        )
        name = _sized_name(
            base_name,
            picked["name_length"],
            ordinal,
            maximum_length=255 if source == "community" else 500,
        )
        if ordinal in LANDMARKS:
            source_id, name, local_name, locale, script = LANDMARKS[ordinal]
        if picked["missing_field"] == "name_local":
            local_name = None
        nutrients = (
            {}
            if picked["missing_field"] == "nutrient"
            else {"energy_kcal": float(50 + ordinal % 500), "protein_g": float(ordinal % 40)}
        )
        evidence = evidence_overrides.get(ordinal, picked["evidence"])
        cluster = picked["duplicate_cluster"]
        cluster_id = cluster_ids[ordinal]
        if cluster != "singleton" and ordinal not in LANDMARKS:
            base_name = f"Benchmark duplicate {cluster_id}"
            name = _sized_name(
                base_name,
                picked["name_length"],
                int(cluster_id.rsplit("-", 1)[-1]),
                maximum_length=255 if source == "community" else 500,
            )
        yield CorpusRecord(
            ordinal=ordinal,
            source=source,
            source_id=source_id,
            pack_id=(f"benchmark-pack-{ordinal % packs:05d}" if source == "community" else None),
            release_id=f"benchmark-release-{ordinal % releases:06d}",
            locale=locale,
            script=script,
            name=name,
            name_local=local_name,
            name_length=picked["name_length"],
            variant=variant,
            duplicate_cluster=cluster,
            duplicate_cluster_id=cluster_id,
            missing_field=picked["missing_field"],
            license="CC0-1.0" if source == "community" else "CC0",
            evidence=evidence,
            projection_state=picked["projection_state"],
            release_age=picked["release_age"],
            category=f"benchmark-{ordinal % 24:02d}",
            provenance="own_measurement" if source == "community" else "government_database",
            nutrients=nutrients,
        )


def write_corpus(
    records: Iterator[CorpusRecord],
    output: TextIO,
) -> tuple[int, str, dict[str, dict[str, int]]]:
    digest = hashlib.sha256()
    summary: defaultdict[str, Counter[str]] = defaultdict(Counter)
    count = 0
    for record in records:
        encoded = record.json_bytes()
        output.write(encoded.decode())
        digest.update(encoded)
        count += 1
        for field in (
            "source",
            "name_length",
            "variant",
            "duplicate_cluster",
            "missing_field",
            "license",
            "evidence",
            "projection_state",
            "release_age",
        ):
            summary[field][str(getattr(record, field))] += 1
        summary["locale_script"][f"{record.locale}|{record.script}"] += 1
    return (
        count,
        digest.hexdigest(),
        {
            dimension: dict(sorted(counter.items()))
            for dimension, counter in sorted(summary.items())
        },
    )


def _open_output(path: str) -> tuple[TextIO, bool]:
    if path == "-":
        return sys.stdout, False
    return Path(path).open("w", encoding="utf-8", newline="\n"), True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT_PATH)
    parser.add_argument("--profile", choices=("launch-reference", "10x", "100x"), required=True)
    parser.add_argument("--count", type=int, help="smaller deterministic prefix for smoke tests")
    parser.add_argument("--output", default="-", help="NDJSON output path or '-' for stdout")
    parser.add_argument("--metadata", type=Path, help="write corpus digest and observed counts")
    arguments = parser.parse_args()
    contract = load_contract(arguments.contract)
    output, should_close = _open_output(arguments.output)
    try:
        count, digest, distributions = write_corpus(
            generate_records(contract, arguments.profile, count=arguments.count), output
        )
    finally:
        if should_close:
            output.close()
    metadata: dict[str, Any] = {
        "schema_version": "1.0.0",
        "contract_id": contract.document["contract_id"],
        "contract_sha256": contract.sha256,
        "profile": arguments.profile,
        "seed": contract.seed,
        "records": count,
        "corpus_sha256": digest,
        "observed_distributions": distributions,
    }
    if arguments.metadata is not None:
        arguments.metadata.write_bytes(canonical_json_bytes(metadata))
    elif arguments.output != "-":
        print(json.dumps(metadata, sort_keys=True), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
