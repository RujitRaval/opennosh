# Evidence-backed public operations

opennosh 0.93 defines a disabled-by-default public status and incident contract. It is a redacted
projection of explicit monitor and recovery facts, not a view of internal telemetry and not an
inference from the absence of alerts.

## Fixed component inventory

`config/public-status.v1.json` names exactly eight public components and a freshness window for
each: public API, contribution intake, artifact downloads, evidence processing, publication, reuse
registry, food search, and the private tracker. `schemas/public-status.schema.json` and
`make public-status-check` prevent unreviewed component or freshness drift. The canonical manifest
digest is returned with every public status response.

Monitor adapters record only a component ID, one of `operational`, `degraded`, `outage`, or
`maintenance`, whether the observation completed successfully, a UTC observation time, a lowercase
SHA-256 evidence digest, and a bounded sorted set of affected four-part releases. The observation
table is append-only.

`GET /api/v1/public/status` accepts no filters. `operational` is possible only when the newest
configured monitor observation is successful and no older than that component's declared window.
Missing evidence yields `unknown` with `missing_evidence`; stale evidence yields `unknown` with
`stale_evidence`; future, malformed, or unsuccessful operational evidence yields `unknown` with
`malformed_evidence`. A fresh explicit degraded, outage, or maintenance observation preserves that
state. No open-incident heuristic can upgrade a component to operational.

## Incident lifecycle

An incident begins in `investigating`. The permitted append-only transitions are:

```text
investigating -> identified | monitoring | resolved
identified    -> monitoring | resolved
monitoring    -> identified | resolved
resolved      -> terminal
```

Every event repeats the complete safe public snapshot: summary, sorted affected component IDs,
sorted affected four-part releases, guidance, state, and UTC event time. A resolved event is invalid
without verified recovery evidence containing only its UTC observation time and SHA-256 content
digest. Restarting a process or seeing no new alert is not recovery evidence.

`GET /api/v1/public/incidents` accepts no filters and returns at most 100 incidents in deterministic
newest-first order. Each incident exposes only its public title, latest safe event, opening/update/
resolution times, and recovery proof. It never exposes credentials, provider resource IDs,
hostnames, IP addresses, log excerpts, actor IDs, or private topology. Database failure or malformed
stored evidence fails the whole read with a retryable `503`; the API never returns a partial or
silently rewritten history.

## Rollout and rollback

`PUBLIC_STATUS_ENABLED=false` keeps both endpoints unavailable. The flag remains false in
application defaults, Compose, CI, and Render. Enabling it requires the valid freshness manifest and
schema head. Roll back by disabling the flag while preserving observations, incidents, and events.
Downgrade migration `20260904_0036` only when all three new tables are empty.

`PUBLICATION_CLAIMS_ENABLED=false`, `PUBLICATION_CONTINUOUS_CLAIMS_ENABLED=false`, one publication
replica, and claim concurrency one remain unchanged.
