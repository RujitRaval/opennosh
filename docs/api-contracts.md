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

## Developer compatibility manifest

`config/developer-compatibility.v1.json` is the machine-readable distribution boundary for the
hosted origin, self-hosted origins, generated clients, and every operation that may enter a public
SDK. Its canonical SHA-256 excludes only the digest field itself. The matching JSON Schema lives at
`schemas/developer-compatibility.schema.json`.

The initial preview pins OpenAPI 2.x plus the retained 1.x compatibility family, the npm three-part
version, Python/CLI four-part artifact versions, MCP/embed protocol 1.0.0, nullable deprecation
dates, sixteen anonymous developer operations, exact response byte limits, and
the no-credentials/no-redirect/no-retry endpoint policy. MCP and embed artifacts are previews while
their production discovery remains explicitly disabled. A new public GET operation under
`/api/v1/public/`, or a change to either developer food operation, cannot land until it appears in
this reviewed manifest and the generated SDK.

`tests/fixtures/developer-compatibility.v1.json` exercises all sixteen current response shapes plus the
retained food reads against a digest- and commit-pinned OpenAPI 1.0.0 snapshot, along with RFC 9457,
rate-limit, stale verified, unavailable-proof, and incompatible-version cases. The npm package now
exports the policy-compliant `OpenNoshClient` wrapper for Node.js 20+ and modern browsers. Generated
path schemas, response media types, problem contracts, and transport types stay reproducible, while
the handwritten wrapper owns target validation, redirect refusal, credentials, response limits,
deadlines, cancellation, cache validators, and typed failures.

The Python wheel exposes the matching preview contract from `opennosh_api.sdk`. Both
`OpenNoshClient` and `AsyncOpenNoshClient` reuse the API's Pydantic public response models and cover
the same sixteen anonymous reads. The retained `FoodSearchResponseV1` model accepts the pinned OpenAPI
1.x search envelope without weakening the current 2.x model. Response wrappers also retain release
version, verified/stale state, stale age, and warning headers for HTML and ZIP reads where those
facts are not carried in the body. Python sends the versioned SDK identifier header, never reads ambient
proxy or credential state, refuses redirects, performs no automatic retry, and maps RFC 9457 or
transport failures to `OpenNoshProblem`. The generated Python operation-policy module is checked
against the same compatibility manifest and OpenAPI snapshot as the JavaScript SDK.

The public-operations pair is deliberately filterless. `/api/v1/public/status` returns the fixed
component inventory and projects unknown from missing, stale, future, malformed, or unsuccessful
operational evidence. `/api/v1/public/incidents` returns only bounded safe incident snapshots and
requires digest-bound recovery evidence for resolution. Both reject partial database results and
exclude infrastructure identifiers, logs, credentials, and private topology.

The application CLI wraps only that supported SDK for `opennosh public ...` reads. Its target
precedence is `--target`, then `OPENNOSH_TARGET`, then `hosted`; JSON output is compact and
key-sorted with one newline. Exit `0` is success, `2` is invalid input or local pack validation,
`3` is a public proof/compatibility failure, and `4` is a network, rate-limit, or upstream failure.
`opennosh packs validate` accepts a bounded local normalized JSON document or a bounded source ZIP,
rejects traversal, links, encryption, duplicate names, unsupported compression, oversized members,
and unexpected paths, and invokes the canonical food-pack validator without extracting the ZIP.
These SDK and CLI surfaces remain preview; they do not activate embeds, federation discovery,
missions, or production claims.

The wheel's `opennosh-mcp` entrypoint serves MCP over stdio and delegates every remote read to
`AsyncOpenNoshClient`. Its endpoint is selected once at startup using the same hosted/self-hosted
origin policy. The allowlist contains five proof-preserving public reads and one local in-memory
pack validator; it contains no write, operator, credential, URL-selection, filesystem, or shell
tool. Every tool returns one `schema_version: "1.0"` object whose `state` distinguishes verified,
stale verified, unavailable, valid, and invalid outcomes. A typed `problem` appears only when one
exists. The process logs method, status, latency, and counts but never arguments or response bodies.
MCP artifact status is preview at protocol 1.0.0, while discovery remains disabled.

The embed 1.0 preview renders only proof-bearing latest-food and exact-release provenance cards.
Its server fetch is credential-free, redirect-refusing, bounded, and fail-closed; the browser page
uses no cookies, storage, telemetry, or third-party requests. Its sole message is a bounded,
versioned resize object sent to the browser-supplied parent origin. Embed production discovery
remains disabled independently from route availability.

The JavaScript and Python starter applications run the same search, verified-detail, and attribution
journey through the shipped SDKs. Package gates install the npm tarball and wheel into empty
temporary directories, prove imports resolve from those installations, and run both starters
against the hosted and self-hosted contract shape. Developer trial reports use
`schemas/developer-integration-trial.schema.json`; the repository refuses a `stable` developer
compatibility status until two distinct independent operators have supplied accepted reports.

Run the gate with:

```sh
make developer-compatibility-check
```

After an intentional manifest edit, refresh its digest once with
`python scripts/check_developer_compatibility.py --write-digest`, inspect the diff, and run the gate
again without `--write-digest`. A compatibility manifest is evidence, not an activation: package,
MCP, and embed promotion still require their own reviewed release and production approval.

## Compatibility fixtures

Golden fixtures under `web/tests/fixtures/contracts` cover the current and N-1 food-search
success contracts plus the previous legacy `{"detail": "..."}` problem shape. The web adapter
maps a v1 offset response into null cursor metadata while v2 exposes `next_cursor`,
`snapshot_id`, and `snapshot_expires_at`. Keep N and N-1 fixtures when a contract is versioned
so rolling API and website deployments remain compatible.

## Account lifecycle contract

`POST /api/v1/auth/register` creates a session and returns a one-time `recovery_code` beside the
authenticated user and CSRF token. The browser must require explicit acknowledgement before normal
Tracker use; the server does not retain a revealable copy. `POST /api/v1/auth/recover` accepts the
email, current recovery code, and a new password, then rotates the code, revokes existing sessions,
creates a replacement session, and returns the new code once. Both plaintext-code responses use
`Cache-Control: no-store`.

`GET /api/v1/auth/session-state` returns `200` for both signed-in and signed-out browsers. Its
`authenticated` flag and nullable `user` avoid using an expected `401` as startup control flow.
Authenticated user responses include `onboarding_completed`, `recovery_configured`, and
`preferred_units` (`metric` or `us`).

Account mutations require the session CSRF token. `PUT /api/v1/auth/account/password` confirms the
current password and revokes other sessions; `POST /api/v1/auth/account/recovery-code` confirms the
password and invalidates the previous code; `PATCH /api/v1/auth/account/settings` changes only the
provided onboarding or unit preference; and `DELETE /api/v1/auth/account` confirms the password and
permanently removes the account and owner-private Tracker rows. Public contribution history is a
separate Commons record and is not rewritten by private account deletion.

`GET /api/v1/targets/resolve-optional` has the same owner, date, day-type, and safety semantics as
`/targets/resolve`, but returns `200` with JSON `null` when no schedule applies. Tracker startup uses
this form so an ordinary account without targets does not create a failed browser request.

## Food-search cursor contract

The first page omits `cursor`. A response with `has_more: true` includes an opaque signed
`next_cursor` that must be replayed with the same normalized query, locale, source filter, and
page size. Federated searches additionally bind the sorted repeated `pack` filter and exact active
release-set digest. The token binds cursor and ranking versions, a retained projection snapshot, a
SHA-256 fingerprint of the normalized search inputs, the last deterministic rank/tie position,
page size, release set, and expiry. It never contains raw search text.

The current signing key signs new tokens while the current and previous keys verify them. Invalid,
altered, malformed, and oversized tokens return `search_cursor_invalid` with HTTP 400. Expired
snapshots, mismatched inputs, changed ranking policy, and retired keys return
`search_cursor_restart` with HTTP 409 and a `restart_search` action pointing to the current first
page. An unsupported signed cursor schema or ranking version also requires restart; a missing version
is invalid. The API does not silently cross projection snapshots within a pagination journey. Its
production entrypoint disables raw access logging so query and cursor parameters are not logged.

When `FEDERATION_SEARCH_ENABLED=true`, an explicit `source=federation` reads only the latest complete
active verified projection. An optional repeated `pack` query also opts into that projection while
selecting canonical pack IDs; results expose
the exact pack version, verified release version and digest, license, provenance, immutable variant
ID, deterministic equivalence-group ID, variant count, and conflict state. Equivalent records are
grouped only when their normalized name/category and exact explicit source URI/license match.
Nutrient disagreements remain separate variants and are never averaged. The response `release_set`
object identifies its checkpoint and digest, selected packs, and whether a retained cursor snapshot
has become stale relative to the current activation. The switch defaults to false; explicit
federation or pack-filter requests fail closed while disabled.

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

## Commons mission activity contract

`GET /api/v1/public/missions/activity` is a read-only, disabled-by-default view over active mission
progress checkpoints. It derives regions from the immutable approved pack manifest whose digest and
repository are bound to each accepted event's signed receipt, never from mutable search rows or
contributor identity or location. The schema-version-`1.0` response is `unavailable`, `zero`, or
`live`; unavailable responses carry only `disabled` or `proof_unavailable`.

Every published region is an ISO-style two-letter country code or a three-digit BCP 47
macroregion with at least ten underlying verified accepted events. The endpoint has no filters and
exposes no total, suppressed count, timestamps, rankings, streaks, or contributor dimensions. This
prevents a client from recovering a hidden small cohort by subtracting one response from another.
Stale checkpoints, missing immutable locale proof, more than 100 moderated missions, more than
10,000 current records, or more than 20,000 lineage events fail the entire response closed. Reads
use one repeatable-read snapshot with a bounded database deadline. Only live or honest-zero
responses are cacheable for 60 seconds.

## Immutable public artifact contract

`GET /api/v1/public/foods/{source}/{source_id}` resolves `latest/v1.json`; an optional `version`
query pins one release. Exact immutable routes live under `/api/v1/public/releases/{release}` for
food JSON, provenance HTML, the signed manifest envelope, and pack downloads. Food responses use
the schema-version-`1.0` `PublicFoodRecordResponse`, keeping the validated `FoodDetail` beside the
resolved release version, publication time, `verified` or `stale` state, stale age, and exact
immutable URLs.

The latest pointer and release manifest are canonical schema-version-`1` Ed25519 envelopes verified
by `PUBLIC_COMMONS_VERIFYING_KEYS`. The pointer is capped at 16 KiB, expires after no more than 24
hours from its signed `issued_at`, and binds the exact manifest key, size, media type, and SHA-256
digest. Pointers created before online renewal omit `issued_at` and remain compatible by using the
manifest publication time. A refresh may advance only `issued_at` and `expires_at`: the release
version and complete manifest descriptor must remain byte-for-byte equivalent, and the durable
checkpoint rejects an expiry rollback for the same release. The manifest is capped at 8 MiB and
lists sorted, unique food and pack identities. Every record, provenance, and pack object key
contains its content digest; reads verify both the declared byte length and digest before any bytes
are returned.

Each manifest names one canonical signed publication receipt. The receipt is independently verified
with `PUBLICATION_RECEIPT_VERIFYING_KEYS`; its release version must match, its publication time must
be at or after the signed release time, and its signed-release plus copied-artifact proofs must bind
the exact manifest digest. Immutable objects
are written before the receipt and manifest, and `latest/v1.json` moves last. A lower version or a
different manifest for an already trusted version cannot replace the durable checkpoint. If latest
is missing, corrupt, expired, rolled back, or equivocated, only the checkpointed verified release
may be served, with its stale age and Warning 110. There is no fallback to unverified bytes.

Record JSON is capped at 512 KiB, provenance at 2 MiB, receipts at 256 KiB, and ZIP pack downloads at 64
MiB. Provenance responses use a restrictive content-security policy; the HTTPS origin adapter does
not follow redirects and bounds streamed bytes even when `Content-Length` is missing. These routes
do not acquire a PostgreSQL session. Search, accounts, drafts, review, and contribution remain on
the dynamic database-backed APIs and report their own degraded states.

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

`POST /api/v1/contribution-drafts/{draft_id}/submit` accepts the expected draft version, an
idempotency key, and a complete discriminated `evidence_manifest`. The server rechecks duplicates,
every stage, evidence class, source URI, and source license before atomically binding the exact
submitted version, creating its preservation wake-up, and moving the draft to `in_review`.
Missing or untrusted byte-backed evidence fails closed. The idempotency key is bound to the
canonical request; reusing it with different evidence fails visibly. The receipt says
`received_for_review` and includes a submission ID, timestamps, public attribution, and stable
status path. It never claims approval, acceptance, or publication.

`PUT /api/v1/contribution-drafts/{draft_id}/evidence` is the authenticated, CSRF-protected
idempotent repair path for a complete manifest bound to an exact owned draft version. `GET` on the
same path returns the evidence ID and class, exact draft version, honest public state, and mutually
consistent `preservation_pending`, `preservation_failed`, and `preservation_failure_code` fields.
An exhausted worker retry records a safe terminal code rather than remaining pending forever.
Missing, incomplete, failed, stale, or tombstoned evidence cannot pass steward approval.

Hosted packaging-label intake is a separate, disabled-by-default capability. An authenticated,
CSRF-protected client creates a declaration-bound session at
`POST /api/v1/contribution-drafts/{draft_id}/evidence-uploads`, uploads directly with the returned
one-time HTTPS `PUT` instruction, and presents the separate completion capability to
`POST .../{upload_id}/complete`. `GET .../{upload_id}` exposes only the safe state, declared and
observed metadata, exact draft version, typed failure, and transition times. Once the evidence
worker reports `sanitized`, `POST .../{upload_id}/attach` accepts the source description, rights
acknowledgement, and redaction state and creates a server-authored sanitized-media manifest. The
upload URL and completion capability are never returned again. Exact retries are idempotent;
changed declarations, draft versions, attachment facts, object revisions, or owners fail closed.
All routes are indistinguishable generic `404`s while either production gate is disabled.
