# Signed publication receipt operations

T5 makes the signed receipt—not the operational PostgreSQL row—the canonical proof that a record
was published. The receipt binds the approved payload and decision to the verified merge, evidence
manifests and their class-specific durable acknowledgements, release, registry result, immutable
artifact snapshots, publisher identity, and idempotency hash.
The publication worker may set `PUBLISHED` only after the same signed envelope is readable from both
the receipt registry and an independent immutable artifact store.

## Trust roots and storage

Receipt signatures use Ed25519 with a versioned key ID. Runtime verification loads an explicit
public-key ring; unknown or invalid keys are quarantined and never accepted through fallback logic.
The signing private key belongs in managed secret storage and must not be committed to the repository
or copied into receipt storage.

Both receipt destinations implement immutable create-or-compare semantics. Rewriting an existing
object key with different bytes is a conflict. Operators must preserve the registry and artifact
store as independent failure domains and retain published receipt objects permanently.

## Reconciliation after database loss

Run reconciliation only against a migrated database and the trusted key ring:

1. Enumerate both receipt destinations.
2. Leave a receipt pending when either destination has not acknowledged it.
3. Require byte-identical envelopes and a valid trusted signature.
4. Restore receipts in prior-receipt order.
5. Recreate the receipt projection, accepted event, durable acknowledgements, and publication ledger
   in one database transaction.
6. Replay reconciliation until every verified object reports already current.

Reconciliation never calls forge, signer, registry-publish, or artifact-copy side effects. A missing
publication intent does not prevent rebuilding the append-only receipt and accepted-event history;
when the matching intent exists, its full ten-step ledger is restored as verified.

## Failure handling

- One missing destination: keep the candidate pending and repair or await that destination.
- Different bytes between destinations: quarantine the candidate and investigate storage integrity.
- Invalid signature, unknown key, malformed object, or database binding conflict: quarantine it; do
  not edit the receipt or mark the record published manually.
- Missing prior receipt for a correction or revocation: keep it pending until lineage is available.
- Duplicate replay: expect a no-op only when every stored field matches exactly.

A correction or revocation is a new governed publication with a new signed receipt and commit. It
links the prior receipt digest; no published receipt is updated or deleted.

## Verification

The required PostgreSQL scenarios are in
`api/tests/publication/test_reconciliation_integration.py`. They cover pre-publication restoration,
late destination acknowledgement, concurrent replay, repair of missing projections, append-only
enforcement, correction, revocation, and cross-destination tampering. Run the complete API suite with an isolated PostgreSQL 16 database as documented in
`docs/testing.md`.
