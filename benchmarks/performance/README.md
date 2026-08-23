# Representative performance benchmark

This directory is the versioned evidence contract for deciding whether PostgreSQL remains the
default search projection. It pins the corpus, exact interaction counts, concurrent traffic, cache
preparation, measurement boundaries, pass/fail gates, and artifact format. Results are comparable
only when their contract digest, profile, corpus digest, environment, and cache state match.

Validate the pinned contract and both artifact schemas before running a benchmark:

```bash
make benchmark-contract-check
```

## Profiles

| Profile | Records | Packs | Releases | Concurrency | Interactions per boundary/cache cell |
| --- | ---: | ---: | ---: | ---: | ---: |
| `launch-reference` | 10,000 | 40 | 120 | 12 | 720 |
| `10x` | 100,000 | 400 | 1,200 | 60 | 10,800 |
| `100x` | 1,000,000 | 4,000 | 12,000 | 240 | 72,000 |

Fixed interaction counts make runs reproducible without pretending a fast local run sustained load
for a wall-clock duration. Every artifact records elapsed time and achieved throughput for each of
the six HTTP/browser cells. Changing a scale, distribution, weight, boundary, or gate requires a
new reviewed contract version; never edit old artifacts to fit a newer contract.

## Generate a corpus

The generator streams canonical NDJSON, so the 100x corpus is not retained in memory. The same
contract, profile, and seed produce byte-identical output and exact pinned distribution totals.

```bash
PYTHONPATH=api:. uv run python -m benchmarks.performance.corpus \
  --profile launch-reference \
  --output /tmp/opennosh-launch.ndjson \
  --metadata /tmp/opennosh-launch.metadata.json
```

For a corpus-only run, use
`PROFILE=launch-reference OUTPUT=/tmp/opennosh-launch.ndjson make benchmark-corpus`; without a
`--metadata` path, the generator prints the metadata JSON to standard error.

Use `--count` only for representative generator smoke tests. A shortened corpus cannot pass a
profile gate.

## Resource evidence

The harness measures PostgreSQL connections and index size directly. Memory high-water marks,
publication job age, and projection lag span separate processes, so the orchestrator must provide
observed evidence rather than allowing the harness process to invent substitutes. A pre-seeded run
also supplies its measured index-build evidence; seeded runs replace that field with the seeder's
actual projection-build timer.

```json
{
  "memory_high_water_bytes": {
    "postgresql": {"value": 0, "source": "replace with observer", "observed_at": "2026-08-23T00:00:00Z"},
    "fastapi": {"value": 0, "source": "replace with observer", "observed_at": "2026-08-23T00:00:00Z"},
    "same_origin_proxy": {"value": 0, "source": "replace with observer", "observed_at": "2026-08-23T00:00:00Z"},
    "edge_browser": {"value": 0, "source": "replace with observer", "observed_at": "2026-08-23T00:00:00Z"}
  },
  "index_build_ms": {"value": 0, "source": "projection observer", "observed_at": "2026-08-23T00:00:00Z"},
  "job_age_p95_ms": {"value": 0, "source": "job observer", "observed_at": "2026-08-23T00:00:00Z"},
  "projection_lag_p95_ms": {"value": 0, "source": "projection observer", "observed_at": "2026-08-23T00:00:00Z"}
}
```

Zeroes above are shape examples, not release evidence. Sources and timestamps must identify real
observations from the benchmark environment.

## Run the contract

Use an isolated database whose name contains `benchmark`. `--seed-database` truncates canonical
food and retained-search tables before loading the pinned corpus; the destructive guard has no
escape hatch for any differently named database.

Configure the deployment's food-search rate limit above the profile interaction count. Provide
separate URLs even when boundaries resolve to the same local service. Set
`BENCHMARK_DATABASE_URL` through your normal local secret-management path.

```bash
PYTHONPATH=api:. uv run python -m benchmarks.performance.harness \
  --profile launch-reference \
  --database-url "$BENCHMARK_DATABASE_URL" \
  --seed-database \
  --boundary fastapi=http://127.0.0.1:8000 \
  --boundary same_origin_proxy=http://127.0.0.1:3000 \
  --boundary edge_browser=https://benchmark.opennosh.example \
  --resource-evidence /tmp/opennosh-resource-evidence.json \
  --artifact-directory benchmark-results/launch-2026-08-23
```

Use `make benchmark-run BENCHMARK_ARGS='...'` to invoke the same harness through the repository
workflow, passing the documented arguments above in `BENCHMARK_ARGS`.

For a pre-seeded database, omit `--seed-database` and pass the generator metadata digest with
`--corpus-sha256`. `--requests` creates a bounded diagnostic; semantic validation requires its
diagnostic gate failure, so it can never masquerade as release evidence.

For every FastAPI, same-origin proxy, and browser cold/warm cell, the harness starts an exact mixed
traffic schedule behind the same synchronization barrier:

- anonymous search executes the production snapshot-bound SQL;
- tracker traffic reads the tracker table;
- publication and ten-record pack ingestion execute current database write shapes;
- projection rebuild copies the canonical food set into a candidate snapshot.

Write actors roll back after measurement. Their purpose is representative capacity pressure, not
publication correctness. The database must be disposable.

## Cache and boundary semantics

PostgreSQL cold means a new connection, discarded plans, and the first measured JSON
`EXPLAIN (ANALYZE, BUFFERS)`; warm executes the pinned query before measurement. FastAPI and proxy
cold runs create new connection pools with `Cache-Control: no-cache`; warm runs pre-execute the
complete query mix. Edge/browser runs use Playwright: cold creates a new empty browser context and
warm reuses a context after a complete query-mix pass.

## Artifact bundle

Each valid run writes:

- `result.json`, validated by JSON Schema and semantic cross-file checks;
- `environment.json`, including hardware, PostgreSQL settings, connection ceiling, and software;
- `resource-evidence.json`, the canonical external observations used by the result;
- `query-seed.json`, containing the exact weighted query definitions;
- cold and warm `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` plans, following the
  [query-plan artifact guidance](plans/README.md);
- `artifact-manifest.json`, with every required file's byte size and SHA-256 digest.

Semantic validation rejects missing boundary/cache/workload cells, incomplete or duplicated
execution cells, incorrect weighted counts, altered plans, escaping paths, inconsistent gate
flags, missing manifest entries, invalid seed distributions, and snapshot-count mismatches.
Credentials and URL query values are removed from the retained reproduction command.

## Search extraction gate

Individual runs never accept or publish a caller-supplied miss streak. Evaluate complete artifact
bundles in chronological order:

```bash
PYTHONPATH=api:. uv run python -m benchmarks.performance.extraction \
  benchmark-results/run-1 benchmark-results/run-2
```

The equivalent Make shortcut is
`make benchmark-extraction ARTIFACT_DIRS='benchmark-results/run-1 benchmark-results/run-2'`.

The evaluator validates every bundle, requires the same contract, profile, and corpus, derives
PostgreSQL misses from measured cold/warm latency, and rejects repeated or out-of-order runs. A
dedicated search projection is triggered only after two consecutive misses or an observed
write-capacity threat. Canonical data remains in PostgreSQL.
