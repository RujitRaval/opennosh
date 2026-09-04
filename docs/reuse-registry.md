# Voluntary reuse registry

opennosh 0.92 adds disabled-by-default steward verification, public reads, and proof-backed
dependency edges to the ownership and audit foundation for the T34.8 reuse registry. It does not
activate production or public impact claims.

## Trust boundary

- A declaration belongs to an existing authenticated person. The registry does not create another
  account, organization, or food identity system.
- Organization and project keys are normalized only for duplicate control. Display labels remain
  owner-provided text.
- Project URLs must be HTTPS, contain no credentials, and are stored as untrusted text. The API
  process never resolves or fetches them.
- Optional geography is voluntary and broad: uppercase ISO 3166-1 alpha-2 country codes or
  three-digit UN M49 macroregions only.
- Every mutation appends an immutable event. Database triggers reject event updates and deletes.
- Cross-owner reads return the same not-found response as missing declarations.
- Verification reuses the existing `steward` grant under the reserved
  `opennosh-reuse-registry` scope. Revoked, recused, and self-reviewing stewards cannot decide.
- Evidence URLs are never fetched by the API. A verification records a UTC observation time,
  lowercase SHA-256 content digest, accessibility state, public HTTPS source, and public reason.
- Dependencies are explicit review facts, never inferred from declaration text, imports, traffic,
  accounts, or organization identity. Each fact pins an existing signed release, pack, artifact
  digest, and one of `runtime`, `data`, `research`, or `derived`.

## Lifecycle

```text
create -> community_declared
community_declared -> verification_pending | withdrawn
verification_pending -> verified | community_declared (edit, changes requested, or rejected)
                       | withdrawn
verified -> community_declared (edit) | withdrawn
withdrawn -> community_declared (restore)
```

Editing a pending or verified declaration returns it to `community_declared`; the owner must submit
the corrected revision again. Verification accepts only accessible evidence observed in UTC no
more than 30 days earlier and never accepts future observations. Unavailable, inaccessible, stale,
or malformed proof cannot produce a verified state.

## API contract

The authenticated routes live below `/api/v1/reuse/declarations`. Mutations require the normal
session and CSRF protections, a UUID `Idempotency-Key`, and—except for creation—an integer
`If-Match` equal to the current revision. Responses are `no-store` and expose the current revision
as `ETag`.

The routes return 404 while `REUSE_REGISTRY_MUTATIONS_ENABLED=false`. The remaining T34.8 flags
also default to false. Invalid transitions, stale revisions, duplicate owner/project identities,
and idempotency payload mismatches return 409.

Registry stewards use `/api/v1/governance/reuse/reviews`. The fixed queue contains at most 100
oldest pending declarations and omits the steward's own records. Review mutations require fresh
authentication, CSRF, `Idempotency-Key`, and `If-Match`; decisions append to the same immutable
event history as owner actions.

Public list and detail reads live at `/api/v1/public/reuse` and remain 404 until
`REUSE_PUBLIC_ENABLED=true` with verification enabled. The collection has no filters or caller-set
limit, uses deterministic ordering, and returns at most 100 records. Pending records are labeled
`unverified`, owner declarations are `community_declared`, and evidence-backed records are
`verified`. Withdrawn declarations are absent while their audit rows remain retained. Public
responses expose no owner or steward identifiers and use bounded shared-cache policy.

### Dependency projection

A steward may attach at most 100 sorted, unique dependency facts to the verification request. The
API resolves the exact four-part release through the existing cryptographically verified public
artifact reader and accepts a fact only when that signed manifest contains the named pack with the
exact lowercase SHA-256 download digest. Missing releases, packs, digest mismatches, malformed
facts, or unavailable artifact proof cannot create a verified dependency.

`GET /api/v1/public/reuse/dependencies` is available under the same `REUSE_PUBLIC_ENABLED` gate.
It accepts no query parameters, returns at most 100 edges in deterministic source/project order,
and carries only declaration ID, project label, pack ID, release ID, artifact digest, dependency
kind, the exact `verified` label, and the UTC evidence observation date. It revalidates every edge
against signed artifacts before publishing and uses a response-derived SHA-256 ETag.

An edge is joined to the exact verification event and current declaration revision. Editing,
resubmitting, correction, rejection, or withdrawal therefore removes the old edge from public
reads without deleting declaration audit history. Artifact proof loss fails the whole dependency
read with a bounded retry response instead of publishing an unproved edge or silently presenting
an incomplete graph.

## Rollback

Set `REUSE_VERIFICATION_ENABLED=false` to stop reviews and `REUSE_PUBLIC_ENABLED=false` to remove
public reads. Set `REUSE_REGISTRY_MUTATIONS_ENABLED=false` to stop the owner registry. Preserve the
additive tables, dependency projection, and immutable audit rows. Downgrade the dependency
migration only when `reuse_dependencies` is empty; otherwise retain it while the public gate is
off. Downgrade the foundation migration only when no post-migration registry data exists.
