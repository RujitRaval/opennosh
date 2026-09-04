# Voluntary reuse registry

opennosh 0.89 establishes the disabled-by-default ownership and audit foundation for the public
reuse registry described by T34.8. It does not activate public impact claims.

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

## Lifecycle

```text
create -> community_declared
community_declared -> verification_pending | withdrawn
verification_pending -> community_declared (edit) | withdrawn
withdrawn -> community_declared (restore)
```

Verification is intentionally reserved for the next release slice. Editing a pending or future
verified declaration returns it to `community_declared` so stale evidence cannot retain a verified
label.

## API contract

The authenticated routes live below `/api/v1/reuse/declarations`. Mutations require the normal
session and CSRF protections, a UUID `Idempotency-Key`, and—except for creation—an integer
`If-Match` equal to the current revision. Responses are `no-store` and expose the current revision
as `ETag`.

The routes return 404 while `REUSE_REGISTRY_MUTATIONS_ENABLED=false`. The remaining T34.8 flags
also default to false. Invalid transitions, stale revisions, duplicate owner/project identities,
and idempotency payload mismatches return 409.

## Rollback

Set `REUSE_REGISTRY_MUTATIONS_ENABLED=false` to stop all registry access. Preserve the additive
tables and immutable audit rows. Downgrade the migration only when no post-migration registry data
exists.
