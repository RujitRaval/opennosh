# Federation cross-pack search

Status: implemented, production disabled

Release: 0.75.0.0

Parent: GitHub issue #146, delivery slice 4

## Decision

Food search may consume the latest complete active federation projection, but PostgreSQL remains a
rebuildable read model rather than a trust root. Search copies exact projected variants into its
existing retained snapshots and binds each snapshot and cursor to the projection checkpoint,
release-set digest, and selected pack IDs.

```text
active verified projection checkpoint
             |
             v
 exact release rows + provenance + nutrients
             |
             v
 retained search snapshot (checkpoint + release-set digest + pack selection)
             |
             v
 signed cursor (search fingerprint + release-set digest + deterministic position)
```

## Equivalence and conflict semantics

The projection computes an equivalence key only when a record has all four deterministic identity
inputs: normalized name, normalized category, exact explicit source URI, and exact source license.
A record without that evidence receives an isolated record-scoped group. Search does not infer
equivalence from fuzzy names, locale, pack ownership, nutrient similarity, or ranking.

Every projected row remains an independently addressable immutable variant. Search reports the
equivalence-group ID, variant ID, variant count, and whether variants in that group have distinct
canonical nutrient digests. It never chooses a winner, merges fields, or averages conflicting
nutrient values. Pack, pack version, release version, release digest, license, source URI,
contributor, and provenance travel with each result and detail response.

## Snapshot and cursor behavior

The first request observes one active checkpoint and atomically builds or reuses a retained search
snapshot for that exact checkpoint and sorted pack selection. A new projection activation creates a
different cache identity. Existing signed cursors continue against their retained snapshot and set
`release_set.stale=true`; they never cross into the new release set mid-pagination. A changed query,
locale, source, pack selection, feature state, ranking version, page size, or cursor release digest
requires a safe first-page restart.

Exact federation detail identifiers combine the verified-release UUID and source record ID. This
prevents one pack update from silently changing a previously returned variant.

## Activation and rollback boundary

`FEDERATION_SEARCH_ENABLED` defaults to false and is explicitly false for both the Render API and
publication worker. Production-claims readiness and natural readiness include the switch and block
if it is enabled. The search route fails closed for explicit federation or pack-filter requests
while disabled; ordinary USDA/community search remains unchanged.

Enable search only after separate approval of an exact deployed commit and readiness evidence, and
only when ingestion/projection operation has its own authorization. Roll back by setting search
false first. Retained snapshots and verified projection facts are not deleted; the migration refuses
to discard columns while federation search identities remain. This slice does not enroll maintainers,
ingest or project releases, install packs, enable public discovery, or authorize publication claims.
