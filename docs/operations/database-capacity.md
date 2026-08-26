# Database connection capacity

The versioned deployment contract is
[`config/database-capacity.v1.json`](../../config/database-capacity.v1.json). It is the single
source of truth for PostgreSQL's connection ceiling, reserved operational headroom, replica counts,
pool sizes, acquisition deadlines, statement deadlines, worker concurrency, and maximum in-flight
database sections.

The required invariant is:

```text
sum(role replicas x role pool size) + reserved headroom <= PostgreSQL connection ceiling
```

The default local and Render deployment commits 12 application connections and reserves 20
connections for migrations, administration, monitoring, recovery, and failover.
71 connections remain uncommitted. Every SQLAlchemy application pool sets `max_overflow=0`; no role
can borrow reserved headroom.

## Startup order

Compose runs three distinct checkpoints:

1. `capacity-preflight` validates the complete manifest, verifies the deployed role counts and live PostgreSQL `max_connections`, then exits.
2. `migrate` runs Alembic once using the reserved migration identity.
3. `api` starts the web role only after the migration job succeeds.

Web and worker commands never run migrations. Invalid totals, omitted roles, nonzero overflow, and
job pools larger than their reservation fail before the application starts.

Run the same preflight natively with:

```bash
make database-capacity-check
```

## Role boundaries

The packaged application exposes independent commands for web, publication, evidence, projection,
reconciliation, and scheduling. Each role declares only its owned queue lanes and adapters. The
default manifest activates the real web role and assigns zero replicas to future workers. A disabled
worker fails closed; enabling a replica before its queue driver is installed also fails instead of
running an inert process. The publication role now has a real PgQueuer driver and reserves one of
its six pooled connections for queue coordination by limiting concurrent database sections to five.
Its replica count remains zero until the governed forge, evidence, and signed-receipt adapters land.

Production deployments must give each activated role its own least-privilege database URL, such as
`WEB_DATABASE_URL` or `PUBLICATION_DATABASE_URL`. The migration and administration jobs use
`MIGRATION_DATABASE_URL` and `ADMINISTRATION_DATABASE_URL`. The shared `DATABASE_URL` fallback is
development-only.

## Saturation and telemetry

A web request waits only for its role's configured acquisition deadline. If the pool remains full,
the API returns a typed `503 database_capacity_exhausted` problem with `Retry-After: 1` and safe
retry guidance. It never creates overflow connections.

Role-attributed pool metrics are available on the internal API at
`/internal/metrics/database` when the trusted proxy token is supplied. The snapshot reports active,
idle, waiting, timed-out, acquisition-count, average-acquisition-latency, and
maximum-acquisition-latency values with deployment and role labels. Do not expose the API container's
loopback port or trusted proxy token publicly.

## Scaling change

Any replica, pool, or concurrency change requires all of the following in the same reviewed change:

1. Update the versioned manifest and its `manifest_version`.
2. Run `make database-capacity-check`.
3. Run the representative mixed-workload benchmark for the affected scale profile.
4. Verify overload recovery, connection peak, acquisition latency, and reserved recovery headroom.
5. Update the deployment topology passed to preflight and deploy it before changing replica counts.

PgBouncer may be introduced after measurement, but it does not replace role budgets, bounded
acquisition, or application backpressure.
