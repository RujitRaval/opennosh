# API and web contracts

opennosh publishes one canonical OpenAPI document and generates TypeScript transport types
from it. This keeps the API and website honest without coupling UI components to generated
files.

## Contract layers

- API success responses use named Pydantic models. The food-search envelope carries
  `schema_version: "2.0"` for its cursor and snapshot fields; the other public food envelopes
  remain on `schema_version: "1.0"`.
- Expected application failures use RFC 9457-compatible `application/problem+json` with a
  stable problem code, schema version, request reference, and typed recovery extensions. The
  `/healthz` probe is the deliberate exception: its `503` response remains the typed operational
  health-state JSON consumed by deployment monitors.
- HTTP exception details are public API copy. Route authors must use reviewed, user-safe text;
  unexpected exceptions always receive a neutral detail and never expose exception strings.
- `web/lib/generated` is reproducible output. Do not edit it by hand.
- Only `web/lib/api` and its facade may import generated transport types. Adapters map those
  types into the stable handwritten models consumed by React components.
- Browser network failures have no HTTP status. Unknown or malformed problem documents become
  safe unexpected outcomes and retain a request reference when one is available.

## Regeneration

Run:

```sh
make contracts-generate
```

The command exports the canonical OpenAPI JSON, runs the pinned generator, and writes a manifest
containing the contract version, generator version, and input SHA-256 digest.

Run all contract gates with:

```sh
make contracts-check
```

CI rejects dirty regeneration, generated imports outside the transport boundary, and breaking
changes without an OpenAPI contract major-version increase.

## Compatibility fixtures

Golden fixtures under `web/tests/fixtures/contracts` cover the current and N-1 food-search
success contracts plus the previous legacy `{"detail": "..."}` problem shape. The web adapter
maps a v1 offset response into null cursor metadata while v2 exposes `next_cursor`,
`snapshot_id`, and `snapshot_expires_at`. Keep N and N-1 fixtures when a contract is versioned
so rolling API and website deployments remain compatible.

## Food-search cursor contract

The first page omits `cursor`. A response with `has_more: true` includes an opaque signed
`next_cursor` that must be replayed with the same normalized query, locale, source filter, and
page size. The token binds cursor and ranking versions, a retained projection snapshot, a SHA-256
fingerprint of the normalized search inputs, the last deterministic rank/tie position, page size,
and expiry. It never contains raw search text.

The current signing key signs new tokens while the current and previous keys verify them. Invalid,
altered, malformed, and oversized tokens return `search_cursor_invalid` with HTTP 400. Expired
snapshots, mismatched inputs, changed ranking policy, and retired keys return
`search_cursor_restart` with HTTP 409 and a `restart_search` action pointing to the current first
page. An unsupported signed cursor schema or ranking version also requires restart; a missing version
is invalid. The API does not silently cross projection snapshots within a pagination journey. Its
production entrypoint disables raw access logging so query and cursor parameters are not logged.

## Public commons snapshot contract

`GET /api/v1/public/commons-snapshot` returns schema version `1`. It is a rebuildable read model,
not a trust root: release proof carries the signed manifest digest and publication-receipt digest.
The activity window includes the total accepted count and at most the four newest event rows. Hero,
activity, freshness, and footer consumers map the generated transport type into the handwritten
`web/lib/api/domain/public-commons.ts` model through the adapter boundary.

The latest pointer and release manifest are JSON signed envelopes with exactly four top-level
fields: `schema_version`, `key_id`, `payload`, and `signature`. The signature is an unpadded base64url Ed25519 signature over UTF-8 canonical JSON for
`payload` using sorted keys and compact separators. Verifier configuration uses comma-separated
`key-id:unpadded-base64url-public-key` entries. The API receives public keys only; private signing
keys remain in the offline publication boundary.

The pointer payload binds `release_version`, a constrained
`release-<four-part-version>.json` filename, and the SHA-256 digest of the complete signed manifest
bytes. The release payload binds publication time and receipt digest, verified record count,
projection completeness, bounded accepted events, and the optional most-recent verified record used
for the quiet state. Event IDs are unique, event timestamps cannot exceed the publication time, and
source commits are lowercase hexadecimal identifiers. Latest pointers are capped at 16 KiB, signed
release manifests at 8 MiB and 10,000 events, serialized public snapshots at 24 KiB, and stored
projection reads at 32 KiB. The five-minute snapshot bucket bounds the rolling window. Cache and
snapshot identity bind the schema, source release digest, full accepted-event checkpoint, activity
cutoff, and bucket, so changing hidden rows cannot preserve an old validator.

The API records the highest accepted release version, manifest digest, publication time, and
canonical complete-projection digest in `PUBLIC_COMMONS_CHECKPOINT_PATH`. A lower signed version, a
different manifest for an already trusted version, a publication-time rollback, or changed stored
snapshot content fails closed even after restart. The checkpoint must be on durable writable
storage; the signed artifact mount remains read-only. The rebuildable projection is independently
persisted at `PUBLIC_COMMONS_PROJECTION_PATH`. The state paths must be distinct and cannot overlap
the latest pointer, release directory, or projection lock. The projection records its source
release, event checkpoint, cutoff, build time, exact pointer-file revision, and complete public
snapshot. The checkpoint retains the immediately prior trusted projection digest as a bounded
journal entry, so a crash after checkpoint publication but before projection replacement still
serves the prior complete snapshot as stale and self-heals on the next refresh.

The HTTP request path performs only one bounded projection read. It serves the current projection
when it reconciles with the trusted checkpoint loaded at process startup, pointer revision, and
five-minute bucket; otherwise it serves the trusted prior projection as stale or a typed unavailable
response. It never reads, parses, sorts, or verifies the signed source manifest. Normal requests use
the in-memory checkpoint; if another worker atomically publishes a new projection, the checkpoint
file revision triggers one bounded checkpoint reload before that projection is accepted. A
background materializer, running every `PUBLIC_COMMONS_REFRESH_SECONDS`, reloads the checkpoint and
handles publication changes and bucket rollover under a process-safe lock, then publishes the whole
projection with fsync plus atomic replacement. Stale and unavailable responses are never retained
in the memory fast path, so another worker's completed projection is immediately observable.

Snapshot states are `live`, `quiet`, `stale`, `partial`, `illustrative`, and `unavailable`. Verified
states require release proof and a count. Illustrative and unavailable states cannot claim either.
A projection lag is partial, a later verification failure is stale, and first-run absence or invalid
artifacts are unavailable. A stale response keeps one previously verified count and event set; it
never mixes fresh and old components. The web adapter rejects malformed state, count, proof,
activity-window, or reason combinations and falls back without a number or fabricated activity. It
uses whole-document five-minute edge revalidation and does not poll or stream activity. After an
atomic rebuild, the API calls the authenticated `PUBLIC_COMMONS_REVALIDATION_URL`; the web route
invalidates the `public-commons` cache tag. The callback uses a dedicated scoped
`PUBLIC_COMMONS_REVALIDATION_TOKEN`, an exact fixed path, and the
`PUBLIC_COMMONS_REVALIDATION_ALLOWED_HOSTS` destination allowlist. The five-minute TTL remains the
bounded fallback when that callback is temporarily unavailable.

## Contribution draft contract

The authenticated contribution write model lives under `/api/v1/contribution-drafts`. Create,
patch, and submit require CSRF protection; reads require the owner session. Another owner receives
the same not-found response as a missing draft.

`POST /api/v1/contribution-drafts` accepts an optional device `client_draft_id`. Repeating that
handoff for the same owner returns the existing draft. `PATCH
/api/v1/contribution-drafts/{draft_id}` accepts an expected positive `draft_version`, one
`operation_id`, an optional requested stage, and 1–25 typed field patches. Autosave patches also
carry each field's base value and base version. Replaying an operation is idempotent. A stale
expected version may merge only when every patched field still equals its normalized base value;
a same-field change returns conflict instead of overwriting newer work. Operation records are
retained for eight days, longer than the seven-day client retry window, and older records are
pruned per draft. PATCH mutations are rate-limited per draft (120 per minute) and across each owner
(240 per minute) by default, bounding randomized-draft abuse as well as one hot draft.
Configure these bounds with `CONTRIBUTION_PATCH_RATE_LIMIT_ATTEMPTS`,
`CONTRIBUTION_PATCH_RATE_LIMIT_WINDOW_SECONDS`,
`CONTRIBUTION_PATCH_ACCOUNT_RATE_LIMIT_ATTEMPTS`, and
`CONTRIBUTION_OPERATION_RETENTION_SECONDS`; operation retention cannot be set below seven days.

Eligible fields are written to a schema-versioned, 64 KiB device draft before the UI announces
`Saved on this device`. Remote drafts coalesce the newest value per field, wait 750 ms after the
last edit but no more than five seconds, and keep at most one mutation in flight. An unknown network
outcome retains the exact operation ID and payload for retry. `Synced` appears only after a
matching-or-newer capability acknowledgement; offline, conflict, session expiry, storage failure,
queue expiry, and unknown-schema states keep local work visible without claiming a server save.
Writer-qualified storage revisions merge simultaneous different-field edits from multiple tabs.
A same-field conflict retains its original base and cannot retry or submit until that field is
explicitly edited. Malformed persisted queues become `repair_required` instead of being sent.
Autosave instrumentation emits numeric counts, payload bytes, timings, ratios, and queue age only;
it never emits field names or values. The PostgreSQL integration gate measures 30 representative
PATCH acknowledgements and fails when p95 reaches 500 ms.

`GET /api/v1/contribution-drafts/{draft_id}?requested_stage=...` and every successful mutation
return one schema-version-1 capability document: workflow and draft versions, review state,
completed and accessible stages, field-addressed blockers, next and resolved safe stages, repair
reason, saved time, normalized fields, at most five exact-name duplicate candidates, and an
optional receipt. Unknown or inaccessible stage requests resolve to the nearest safe stage rather
than authorizing a forged URL.

`POST /api/v1/contribution-drafts/{draft_id}/submit` accepts the expected draft version and an
idempotency key. The server rechecks duplicates and every stage before moving the draft to
`in_review`. The receipt says `received_for_review` and includes a submission ID, timestamps,
public attribution, and stable status path. It never claims approval, acceptance, or publication.
