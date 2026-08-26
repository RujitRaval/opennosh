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
