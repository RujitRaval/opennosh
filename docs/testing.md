# Testing opennosh

opennosh separates fast deterministic tests from PostgreSQL integration and browser acceptance while
running all of them as required pull-request checks.

## API tests

Run the complete API suite with:

```shell
uv run pytest api/tests
```

PostgreSQL tests require an isolated migrated database:

```shell
INTEGRATION_DATABASE_URL=postgresql+asyncpg://opennosh:opennosh@127.0.0.1:5432/opennosh_test \
  uv run pytest api/tests
```

GitHub Actions supplies PostgreSQL 16 and `INTEGRATION_DATABASE_URL`, so skipped integration tests in
a developer shell still run in the required API check.

## Deterministic workflow testkit

`api/tests/workflow_testkit` provides injected clocks, identifiers, scheduler decisions, named
failpoints, scripted adapter observations, persistent external state, a contract-faithful persistent
job queue, and consistent PostgreSQL-plus-queue checkpoints. Trust-path tests advance the injected
clock and use explicit events or injected timeout scopes; they do not use sleeps as synchronization.

The publication scenario generator reads the production protocol registry. Its normal CI matrix
covers all ten registered steps at six boundaries: before effect, after effect, before verification,
after verification, before reducer commit, and after reducer commit. Two additional scenarios crash
before and after final accepted-event persistence. Every recovery run recreates the worker, preserves
external state, expires leases deterministically, replays typed queue work, and checks the global
transition, idempotency, evidence, durable-copy, receipt-lineage, and truthful-publication invariants.

When extending a workflow:

1. Add the production protocol or adapter contract first.
2. Add its persistent contract fake to the shared testkit.
3. Generate fault cases from the production registry instead of copying a step list into tests.
4. Preserve database, queue, and fake external state across worker recreation.
5. Assert the global trust invariants after recovery and duplicate delivery.

The shared testkit fakes implement the production `PublicationEffectAdapter`, `JobQueue`, evidence,
and signed-receipt contracts. T2's governed-forge contracts have focused adapter fakes plus real
PostgreSQL lifecycle tests for approval, intervention, protected checks, attestation, and merged-tree
verification. T5 additionally verifies receipt signature and storage adapters plus database
reconstruction from the same canonical envelope used by production; the testkit deliberately does
not invent test-only interfaces that application code cannot implement.

## Risk-tiered trust gates

The versioned gate inventory is `config/trust-gates.v1.json`. Validate it locally with:

```shell
make trust-gates-check
```

For the exact local coverage gates used by pull requests, run:

```shell
npm --prefix web run test:coverage

mkdir -p test-results
PYTHONPATH=api uv run coverage run --branch --source=opennosh_api -m pytest api/tests
uv run coverage json -o test-results/python-coverage.json
uv run python scripts/check_changed_coverage.py \
  --coverage-json test-results/python-coverage.json \
  --base origin/main --head HEAD
```

Repository administrators apply or verify the matching main-branch required status checks with
`make trust-branch-protection-apply` and `make trust-branch-protection-check`.

Every pull request classifies the changed paths, runs the complete deterministic transition and
rescue contract, enforces 90% coverage on changed executable API lines, and refuses repository
coverage regression below the measured baselines. Vitest covers production components and
libraries; Next.js route and server modules are covered by the required UI, localization, visual,
and real-vertical browser lanes.

A release must additionally pass package installation, browser roles, upgrade/rollback and receipt
reconstruction, and supported self-host smoke tests before publication. The weekly scheduled
workflow exercises real PostgreSQL forge/artifact/signer/recovery integrations, representative
search and mixed-load gates, and the non-intercepted browser-to-signed-receipt journey.

A failing visual or real-vertical primary attempt remains failed. Diagnostic capture may rerender
or replay only under `failure()`; it can never replace the primary verdict. Temporary quarantine
records require a GitHub issue, owner, reason, and expiry in
`config/trust-gate-exceptions.v1.json`. Trust-protocol and security-policy gates cannot be
quarantined.

The non-intercepted vertical journey is deterministic test evidence, not a claim that a natural
production contribution occurred. T34.4's live proof uses a separately approved real contributor
and independent steward, then verifies the canonical database lineage and public artifacts with
`opennosh commons natural-publication-proof`. Production proof data is never seeded or replayed.
