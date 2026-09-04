# Privacy-safe impact metrics

opennosh impact is a released aggregate, not a live analytics dashboard. T34.8.3 defines the metric
dictionary and persistence boundary while leaving `IMPACT_AGGREGATION_ENABLED` and
`IMPACT_PUBLIC_ENABLED` off in every environment template.

## Public contract

`GET /api/v1/public/impact` returns one fixed snapshot. It accepts no date, region, contributor, or
other filters. When the feature is disabled it returns a deterministic `unavailable` response with
reason `disabled`; when enabled without a valid stored snapshot it returns `proof_unavailable`.
Both states are `no-store`. Released `zero` and `live` snapshots are briefly cacheable and bind an
observation time and source checkpoint to a canonical SHA-256 digest.

The global metrics are verified adopters, community declarations, accepted contributions, pack
installs, API reads, and artifact downloads. Verified adopters are distinct normalized organizations
with a current verified declaration and accessible, fresh public evidence. Community declarations
count only current `community_declared` records, remain separate, and never become verified merely
by appearing in an aggregate.

## Privacy boundary

Country and macroregion cells use only a voluntary declaration region or a proof-bound pack locale.
The aggregator never infers location from IP addresses, headers, accounts, trackers, or language.
Each input fact carries at most one mutually exclusive region cell, and a cell is released only when
it contains at least ten distinct underlying actors or adopters. Operational read, install, and
download counters remain global unless a future reviewed metric definition establishes an equally
non-identifying proof source.

Actor IDs, declaration IDs, accepted-event IDs, and normalized organization keys may exist only in
the in-memory aggregation input. They are discarded after deduplication and thresholding. The
`impact_snapshots` table stores only the public snapshot JSON, checkpoint, observation time, and
digest, and an immutable database trigger rejects update or deletion.

The API deliberately omits arbitrary time ranges, suppressed partition totals, suppression counts,
raw identifiers, contributor dimensions, and fine-grained event timestamps. This prevents repeated
queries from recovering a hidden cohort by subtraction.

## Metric governance

The normative dictionary is `config/impact-metrics.v1.json`, validated against
`schemas/impact-metrics.schema.json` by `make impact-metrics-check`. Definition changes require a
new metric definition version and migration; existing snapshots remain append-only for auditability.
