# T4 PgQueuer spike decision

Status: accepted for the first opennosh job-delivery adapter
Pinned adapter: `pgqueuer[asyncpg]==1.3.2`
Runtime tested: Python 3.12, PostgreSQL 16
License: MIT

## Boundary

PgQueuer wakes workers. It does not decide or prove publication.

Queue payloads contain only a schema version, lane, job type, publication-intent ID,
idempotency key, and trace identifiers. Reviewed decisions, approved record data, forge
targets, workflow state, durable acknowledgements, and accepted events remain in the
opennosh publication ledger.

The approval path writes the publication intent and queue row through the same checked-out
SQLAlchemy/asyncpg transaction. A commit preserves both; a rollback preserves neither.
Queue completion is never interpreted as publication completion.

## Spike results

| Required proof | Result | Automated evidence |
| --- | --- | --- |
| Python/PostgreSQL compatibility | Pass | Locked install plus PostgreSQL 16 integration suite |
| Transactional enqueue | Pass | Commit and forced-rollback assertions cover ledger and queue rows |
| Duplicate delivery | Pass | Intent idempotency and active-job deduplication produce one durable intent/wakeup |
| Crash recovery/stale lease | Pass | A stale picked job is reclaimed by a different queue-manager ID |
| Retry timing | Pass | Future `execute_after` jobs remain ineligible until due |
| Priority fairness | Pass | Higher-priority eligible work is claimed first |
| Unknown job safety | Pass | A future entrypoint remains unclaimed by the current publication worker |
| Graceful shutdown | Pass | Real worker verifies schema, stops claiming, drains, and closes its bounded pool |
| Migration compatibility | Pass | Explicit `0012 -> 0013 -> 0012` migration and clean Alembic metadata check |
| Namespacing | Pass | All adapter tables, enum, indexes, trigger, function, and channel use `opennosh_` |
| Health/metrics | Pass | Adapter health reports schema readiness plus queued and eligible lane counts |
| Adapter replacement | Pass | The runtime-checkable `JobQueue` port accepts a PgQueuer-independent implementation |
| Supported license | Pass | The pinned release is MIT licensed |

## Operational decision

Adopt PgQueuer 1.3.2 behind the opennosh `JobQueue` port.

opennosh owns the migration instead of running PgQueuer's CLI in production. The adapter
objects live in the existing `public` schema with an `opennosh_` prefix so Render's
reviewed role grants do not need to expand. Alembic deliberately excludes these
adapter-owned objects from ORM autogeneration while retaining their explicit upgrade and
downgrade.

The publication process uses the T9 capacity manifest for pool size, acquisition timeout,
statement timeout, worker concurrency, application name, and replica activation. Its
six-connection pool permits at most five concurrent publication database sections so queue
coordination always retains one connection. T10 installs the deterministic planner, bounded
effect executor, reducer, and PgQueuer wake-up handler. The default production replica count
remains zero until T2, T3, and T5 supply the governed forge, evidence, and signed-receipt
adapters required to complete the protocol.

Migration `20260825_0014` intentionally refuses a database containing any legacy
`publication_steps` rows because those rows do not carry a canonical T10 destination and ordinal.
The publication worker has never been activated in the default topology, so the table is expected
to be empty. Operators upgrading a non-default deployment must verify and resolve any rows under
the old schema before running Alembic; the migration fails closed instead of inventing effect
destinations or accepting ambiguous progress.

## Sources

- [PgQueuer package metadata](https://pypi.org/project/pgqueuer/)
- [PgQueuer source and license](https://github.com/janbjorge/pgqueuer)
- [PgQueuer documentation](https://pgqueuer.readthedocs.io/)
