# Federation pack installation

Status: accepted for T34.5e; production activation is not authorized.

## Goal

Self-hosters can select exact verified pack releases and search them from local PostgreSQL after
the signed artifacts have been fetched and verified. Installation never changes the federation
registry's immutable release chronology and never requires a hosted search service.

## State and transaction boundary

`federation_pack_installation_events` is an append-only, generation-numbered event log per
repository/pack scope. `install`, `update`, and `rollback` bind an exact verified-release ID;
`remove` binds no release. A database trigger rejects updates and deletes. Every command holds a
scope advisory lock and the projection lock, appends its event, materializes the complete current
installed set, and appends the activation pointer last in one transaction.

An installed checkpoint may contain zero releases. This is required when the final pack is
removed: activating the empty checkpoint prevents old rows from remaining discoverable. Registry
checkpoints remain non-empty. Checkpoint identity includes its `registry` or `installed` mode so
the same release set cannot blur the two policies.

Exact command replays are idempotent. Update accepts only a release with later governed receipt
chronology; rollback accepts only an older verified release and does not redefine which release is
latest in the registry. Quarantined releases cannot be selected. If an installed release is later
quarantined, the active projection becomes stale and new snapshots omit it until an operator rolls
back, updates, removes, or reconciles the installed set.

## Operator surface

The administration CLI exposes `install-pack`, `update-pack`, and `rollback-pack` with an exact
statement digest; `remove-pack` with an exact repository and pack ID; and
`reconcile-installations`. Every mutation requires an active human steward for the configured
pack, returns a versioned JSON status, uses documented nonzero exit codes, and writes a redacted
audit event.

## Benchmark and extraction decision

The versioned representative performance contract pins the federation PostgreSQL gate to the
10,000-record, 40-pack, 120-release launch profile. It includes exact and prefix names,
misspellings, non-Latin scripts, duplicate clusters, license conflicts, pack filters, and
worst-case cursor pagination under the existing 250 ms cold and 100 ms warm p95 gates. PostgreSQL
remains the selected engine. The existing extraction policy still requires two reproducible misses
under the same contract and profile before a dedicated engine may be proposed.

## Rollout and rollback

`FEDERATION_INSTALLATION_ENABLED` and `FEDERATION_PUBLIC_DISCOVERY_ENABLED` default to false and
are explicitly false for the API and publication worker. Production-claims and natural-readiness
reports block if either is true. Deployment of this slice does not authorize installation,
discovery, ingestion, projection, search, enrollment, or publication claims.

Rollback disables public search and installation first. Preserve all installation events,
verified releases, checkpoints, and activations. The migration refuses downgrade when installation
facts or installed checkpoints exist; recover through a reviewed forward change instead of
deleting trust history.
