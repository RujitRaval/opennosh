# Federation verified ingestion and projection

Status: implemented, production disabled

Release: 0.74.0.0

Parent: GitHub issue #146, delivery slice 3

## Decision

Git, signed public-release metadata, and governed publication receipts remain authoritative.
PostgreSQL stores immutable verification facts and rebuildable projection checkpoints; it does not
become a second canonical food store or trust source.

```text
stored maintainer-signed release
        + signed canonical public manifest
        + exact pack artifact bytes
                         |
                         v
 signature + receipt binding + manifest signature + hash/size
 + safe archive + schema + scope/version + CC0 checks
                         |
              verified release fact
                         |
 latest eligible fact per active scope, excluding quarantine
                         |
 deterministic release-set checkpoint + materialized food rows
                         |
             append activation fact last
```

## Trust boundaries

- The existing federation release ledger supplies the exact signed statement, role key, governed
  receipt binding, accepted event, scope, release version, and chronology. Ingestion re-verifies the
  stored signature with the historical role key before it trusts any artifact bytes.
- The signed public manifest must be canonical JSON, match the release ledger digest and release
  version, verify against an explicitly configured manifest key, and contain exactly one matching
  pack descriptor.
- The pack must match that descriptor byte-for-byte and is inspected before extraction. Absolute,
  parent-traversing, duplicate, encrypted, symlink, unsupported-compression, oversized, and
  unexpected paths fail closed.
- The existing food-pack loader remains the sole schema and nutritional validation path. The
  normalized records must retain the exact pack identity/version and `CC0-1.0` declaration.
- Verification failures append a redacted terminal quarantine fact in a successful database
  transaction, then return the typed failure. No raw content or operator reason is stored in audit
  payloads; only bounded identifiers, failure codes, and reason digests are retained.

## Projection and failure semantics

Projection selection uses governed receipt chronology rather than parsing release labels. It picks
the latest verified, non-quarantined release for each active allowlisted scope. Release-set identity
is the SHA-256 digest of canonical sorted release metadata, and record-set identity is bound into
each member.

The checkpoint, membership rows, normalized food rows, audit facts, and activation fact are written
inside one transaction protected by a transaction-scoped advisory lock. The activation row is
added only after all candidate rows are staged. A crash, constraint failure, invalid record, empty
candidate set, or lost transaction exposes no partial checkpoint. Replaying an identical release
set reuses the existing checkpoint and activation time.

Quarantine never mutates verified data. A later projection can fall back to the prior verified
release in that scope; if no eligible release remains, the build fails and the last activation stays
current. Append-only triggers protect all verification and projection tables from update or delete.

## Activation boundary

`FEDERATION_INGESTION_ENABLED` and `FEDERATION_PROJECTION_ENABLED` default to false and are explicit
false values in the Render publication worker. Readiness reports bind those values and block when
either is enabled. This release adds no production projection worker or search consumer, so it does
not authorize indexing, installation, public discovery, broader enrollment, or publication claims.
