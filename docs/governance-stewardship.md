# Accountable stewardship contract

T34.3 adds the disabled review surface that connects an exact contribution version to a responsible
pack steward, a public-safe reason trail, and the existing protected publication path. It does not
activate evidence intake, grant a steward role, or change production publication claims.

## Durable review model

Each `governance_review_cases` row stores the exact draft version and an immutable copy of the fields
submitted for that version. Ordered `governance_review_events` record assignment, pauses, decisions,
responses, disputes, appeals, reopening, and closure. Private steward notes have their own table and
are never projected by the HTTP contract. Optimistic case, dispute, and appeal revisions reject a
stale browser action; per-case idempotency hashes make a retried mutation replay the first result.

The visible queue has no secret priority score. It sorts unacknowledged cases first, breached
`next_review_at` dates second, then submission time and stable case ID. A claim publishes the
responsible steward and acknowledgement time. A pause requires a public-safe bounded reason and a
next-review date within 30 days. Recusal records a durable conflict and releases the case.

Changes requests and rejections are immutable governance decisions. A contributor response creates
a new exact draft version and review case, closes the superseded case, and does not copy evidence
forward. Approval therefore remains blocked until evidence for the new version has its required
durable acknowledgements. Approval alone delegates in the same transaction to the existing
`approve_contribution` service, which creates exactly one decision, publication intent, and queue
wake-up. No other review action can enqueue publication.

Disputes contain only a bounded category, public-safe reason, requested remedy, decision/version
binding, responsible actors, and timestamps. An appeal targets one resolved dispute and must be
resolved by a different active pack steward. Self-review, inactive or revoked roles, prior recusal,
cross-pack access, stale versions, and stale revisions fail closed.

## HTTP and browser boundary

Authenticated reads and CSRF-protected mutations live below `/api/v1/governance/`. Every response is
`no-store`; every mutation also requires `Idempotency-Key` and an expected revision. The generated
browser client stays behind `web/lib/api`, and the same-origin proxy forwards CSRF and idempotency
headers. The steward queue and case routes are `/governance` and `/governance/cases/{case_id}`.

Browser state may contain the submitted field snapshot, public event reasons, typed states, actor
IDs, exact hashes that the API deliberately projects, and metadata-safe source facts. It must not
contain evidence bytes, filenames, object keys, storage references, presigned URLs, provider
revisions, credentials, account email, network identifiers, or private notes. Until an evidence
provider is separately activated, the comparison surface explicitly shows metadata-only evidence.

## Disabled deployment and readiness

The committed defaults are:

```text
GOVERNANCE_STEWARD_UI_ENABLED=false
GOVERNANCE_MUTATIONS_ENABLED=false
GOVERNANCE_PUBLIC_DECISIONS_ENABLED=false
OPENNOSH_GOVERNANCE_STEWARD_UI_ENABLED=false
EVIDENCE_UPLOADS_ENABLED=false
EVIDENCE_SANITIZATION_ENABLED=false
PUBLICATION_CLAIMS_ENABLED=false
PUBLICATION_CONTINUOUS_CLAIMS_ENABLED=false
```

Disabled governance API routes return the same generic `404` before request validation, lookup,
authorization, or database work. The server-rendered web gate returns the normal not-found page.
Existing internal governance and publication protections remain active.

A later activation report must bind the exact deployed commit, migration revision, all four surface
flags, named pack IDs and steward actor IDs, role grant/revocation state, fresh-auth maximum age,
CSRF and idempotency policy, database capacity, generated contract digest, browser canary result,
and rollback owner. Canonicalize that non-secret JSON with sorted keys and compact separators,
exclude only its digest field, and record its SHA-256. Approval must name that exact digest; approval
of this implementation, a disabled deployment, evidence activation, or a publication readiness
digest is not governance activation approval.

Activation order is web read surface, named steward access, mutations, then any separately reviewed
public decision projection. Run desktop and mobile queue/detail/decision/response/dispute/appeal
canaries and observe API/database health for five minutes after each boundary. Evidence uploads and
publication claim settings remain unchanged.

## Rollback

Set `GOVERNANCE_MUTATIONS_ENABLED=false` first, then disable the API and web UI flags. Confirm every
governance route returns generic `404`, existing public and Tracker routes remain healthy, and the
publication worker remains at its previously approved claim state. Do not delete or rewrite review
cases, field snapshots, events, decisions, disputes, appeals, contribution versions, evidence,
publication intents, Git history, releases, or receipts. The additive schema remains in place so the
previous application can run during the rollback window.
