# Evidence durability contract

opennosh treats manual entry and OCR as ways to enter data, not as evidence. Every reviewed claim
binds one typed evidence manifest to the exact contribution draft version. A steward cannot approve
the contribution until that manifest has the durable acknowledgements required by its class.

## Evidence classes

| Class | Canonical proof | Required durable acknowledgement | Public state |
|---|---|---|---|
| Sanitized media | SHA-256 digest, safe image format, source description, rights acknowledgement, redaction state, private storage reference | Independently durable immutable sanitized copy with the same digest | `evidence_preserved` |
| Versioned public dataset | Dataset, release, record, publisher, license, URI, and canonical record digest | Signed manifest; also a durable record snapshot when archival is permitted | `source_verified` |
| Public document | Canonical URI, publisher, title, observation time and digest, license, and rights state | Archived bytes with the observed digest when permitted; otherwise the immutable citation manifest | `reference_preserved` or `reference_only` |
| Maintainer attestation | Authority, scope, signed statement, signature, time, license, and supporting reference | Immutable signed attestation manifest | `attested` |

Dataset and attestation signatures use Ed25519 and are verified against principal-bound keys in
`EVIDENCE_VERIFYING_KEYS` before the worker emits a signed acknowledgement. An absent, untrusted,
or invalid signature fails closed.

`reference_only` and `attested` are deliberately weaker labels. Neither may be rendered as if
opennosh retained primary source bytes.

## Trust flow

```text
Contribution draft version
        │
        ▼
Typed immutable manifest ── SHA-256 ──▶ PostgreSQL manifest identity
        │
        ▼
Evidence preservation worker
        │  no database connection is held during storage I/O
        ▼
Content-addressed immutable store ── independent read + digest verification
        │
        ▼
Class-specific durable acknowledgement(s)
        │
        ├── incomplete / mismatched / missing ──▶ approval blocked
        └── policy satisfied ──▶ exact public verification state
```

The domain policy is in `api/opennosh_api/evidence/contracts.py` and
`api/opennosh_api/evidence/policy.py`. Storage implementations conform to the narrow
`EvidenceStore` port in `api/opennosh_api/evidence/storage.py`: immutable put followed by an
independent observation that reports destination, object key, digest, reference, and size. The
local adapter is for development and self-hosted installations. Hosted production must inject an
independently durable versioned or object-locked implementation; the local filesystem adapter is
not a production RPO-zero claim.

The evidence worker reserves one connection from its role pool for PgQueuer coordination and
heartbeats; preservation handlers may use only the remainder.

Authenticated clients normally include a complete typed manifest in
`POST /api/v1/contribution-drafts/{draft_id}/submit`. Submission, exact-version manifest binding,
and the preservation wake-up commit in one transaction, so review cannot begin with an orphaned
evidence handoff. `PUT /api/v1/contribution-drafts/{draft_id}/evidence` is the idempotent repair
path for an already submitted exact version, and `GET` on the same route returns its public state.
Retries with identical proof are idempotent; reuse of a submission key with different evidence
fails visibly. The manifest license must match the source license reviewed in the draft.

The worker retries transient failures within a bounded policy. Once that budget is exhausted, it
records a safe typed failure code and time, and the status endpoint reports failure instead of
claiming that preservation is still pending. A terminal failure remains approval-blocking.

## Activation and configuration

The `evidence` process role defaults to zero replicas. Development and self-hosted operators may
run `opennosh-evidence-worker` with `EVIDENCE_DATABASE_URL`,
`EVIDENCE_PRIVATE_SOURCE_DIRECTORY`, `EVIDENCE_IMMUTABLE_DIRECTORY`, and
`EVIDENCE_VERIFYING_KEYS`. The two directories are required together, must be distinct, and must
not be used to make a hosted-production durability claim.

T34.1 adds a provider-neutral hosted intake foundation but does not activate it. With
`EVIDENCE_UPLOADS_ENABLED=false` (the committed default), all three upload-session routes return the
same generic `404` before object-store or queue I/O. The database can represent only the reviewed
`initiated -> uploaded|expired|failed` transitions; sanitization and attachment remain unavailable.

When a later reviewed deployment enables intake, the API may receive only the quarantine
create/observe credential. It issues a conditional, declaration-bound upload URL and a separate
32-byte completion capability for at most 600 seconds. PostgreSQL stores only hashes, an opaque
`quarantine/{uuid}` key, bounded declarations, and independently read-back size/digest metadata.
It never stores bytes, filenames, EXIF, presigned URLs, or raw capabilities. Identical create
retries return the safe session without recovering either one-time secret.

Quarantine, sanitized-source, and immutable-destination stores must use distinct buckets and
credentials in production. Provider provisioning must enforce a maximum 24-hour lifecycle for raw
quarantine objects. The future evidence worker is the only role allowed to receive quarantine
read/delete, sanitized read/write, immutable read/write, its database role, and verifying keys.
Migration, publication, projection, reconciliation, scheduler, and public web runtimes strip all
evidence authority. The Render Blueprint still declares no evidence worker, evidence capacity
remains zero, and public navigation remains unchanged.

Activation requires the T34.2 safe image rewrite and metadata-removal worker, reviewed provider
residency/CORS/lifecycle controls, isolated production credentials, worker capacity and health
proof, end-to-end digest verification, rollback rehearsal, and a separate explicit production
approval. Activation also requires per-account and per-draft issuance/completion rate limits, a
bounded outstanding-session quota, and a process-wide observation-concurrency bound; the disabled
T34.1 surface intentionally does not claim those controls. OCR is a later boundary. Until those
gates pass, the public contribution journey keeps review handoff visibly closed instead of creating
submissions that cannot satisfy the evidence gate. See
[T34](https://github.com/RujitRaval/opennosh/issues/134) for the remaining sequence.

## Replay, tamper, and removal behavior

- Replaying the same manifest or acknowledgement is idempotent.
- Reusing a draft version, object key, acknowledgement kind, or destination with different proof
  raises a conflict instead of overwriting history.
- Any evidence ID, class, manifest digest, or source-byte digest mismatch fails closed.
- Missing source bytes never create a durable acknowledgement.
- Exhausted preservation failures become visible terminal states rather than permanent `pending`
  claims.
- Rights-restricted documents preserve a citation manifest and observed digest without claiming an
  archived copy.
- Governed removal requires an active steward for the draft's pack and preserves the original
  manifest, acknowledgements, prior public state, actor, time, and reason. The visible state becomes
  `tombstoned`; new acknowledgements are rejected. Removal cannot race an active merge authority.

The migrations add `evidence_manifests`, `evidence_durable_acknowledgements`,
`evidence_removal_tombstones`, and disabled `evidence_upload_sessions`. These rows preserve
workflow and audit metadata only. Evidence bytes remain outside PostgreSQL and Git.
