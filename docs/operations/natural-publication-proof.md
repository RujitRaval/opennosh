# Natural browser-to-receipt publication proof

T34.4 adds a read-only verifier for one real contribution. It binds a redacted browser capture to
the exact draft version, review case, independent approval, publication intent, ten verified
publication steps, signed receipt, accepted event, latest record, immutable record, provenance, and
signed release manifest. It does not create or approve a contribution and it cannot enable a
production feature.

The implementation deploys disabled. A code review, merged pull request, healthy disabled deploy,
or readiness report is not permission to activate evidence, governance, public reads, or claims.
Never create a synthetic, seeded, staff-authored, imported, or replayed production contribution to
satisfy this proof.

## Disabled release state

The committed Blueprint must retain these values:

```text
EVIDENCE_UPLOADS_ENABLED=false
EVIDENCE_SANITIZATION_ENABLED=false
GOVERNANCE_STEWARD_UI_ENABLED=false
GOVERNANCE_MUTATIONS_ENABLED=false
GOVERNANCE_PUBLIC_DECISIONS_ENABLED=false
OPENNOSH_GOVERNANCE_STEWARD_UI_ENABLED=false
PUBLIC_ARTIFACT_READS_ENABLED=false
PUBLICATION_CLAIMS_ENABLED=false
PUBLICATION_CONTINUOUS_CLAIMS_ENABLED=false
PUBLICATION_PREACTIVATION_SMOKE_ENABLED=false
PUBLICATION_ACTIVATION_IDS absent
PUBLICATION_CLAIM_CONCURRENCY=1
LATEST_REFRESH_ENABLED=true
```

`config/database-capacity.v1.json` must keep evidence replicas at zero and publication replicas at
one. The Blueprint remains API, publication worker, web, and PostgreSQL only. The T34.4 activation
contract describes the later target but does not provision the evidence worker or its credentials.

After the disabled deploy is live and the publication worker has been healthy for five minutes, run
this only in the `opennosh-publication` Render shell:

```shell
python deploy/render_runtime.py natural-publication-readiness
```

The command opens PostgreSQL through `opennosh_publication` in read-only mode and nests the T33.6
production-claims readiness digest. A passing report requires the exact deployed commit, one
publication replica, concurrency one, zero evidence replicas, an idle publication queue, and every
T34 surface and claim flag false. Its `readiness_sha256` covers the complete canonical report except
that digest field. Any changed observation requires a new report and approval.

## Separate activation boundary

Activation requires a later deployment change that provisions three independent evidence
authorities and one evidence worker, plus named active stewards for the target pack. Review provider
retention, CORS, conditional writes, object immutability, malware scanning, regional residency,
cost, secret rotation, fresh authentication, and the exact browser build before requesting approval.

The only valid approval names the fresh digest and exact target:

```text
I approve T34.4 natural-publication activation for readiness digest <sha256>,
one natural contribution, continuous publication, concurrency 1, observation 1800 seconds.
```

No earlier approval, T33.6 digest, issue approval, or PR approval substitutes for this message.
Activate in this order: evidence worker health; evidence upload and sanitization; governance read
surface and named-steward access; governance mutations; public artifact reads; then continuous
publication claims with no activation IDs and concurrency one. Stop if any prior boundary is not
healthy.

## Browser capture and independent review

Use an ordinary contributor who independently chose to submit a real food record. Retain a redacted
capture outside the repository showing ordered browser-visible states only: draft, evidence
quarantine/sanitized/preserved, submitted, in review, approved but not published, publication
pending, and published. Remove names, email, cookies, request headers, object keys, upload URLs,
image bytes, private notes, and network identifiers. Record only its SHA-256 in the proof request.

The steward must be a different active person, assigned to the pack, not recused, and not the
contributor. Approval must bind the exact submitted draft version and preserved evidence. Do not use
an operator CLI, fixture, database write, seed, import, or queue selection to manufacture the flow.

## Proof request and verification

After the record is public, disable both publication claim switches, remove activation IDs, wait
until active, picked, and claimable publication work are all zero, and retain latest refresh. Create
a mode-`0600` JSON request on the Render shell:

```json
{
  "schema_version": "1.0",
  "draft_id": "00000000-0000-4000-8000-000000000000",
  "draft_version": 1,
  "review_case_id": "00000000-0000-4000-8000-000000000000",
  "decision_id": "00000000-0000-4000-8000-000000000000",
  "publication_intent_id": "00000000-0000-4000-8000-000000000000",
  "pack_id": "example-pack",
  "record_id": "example-record",
  "browser_capture_sha256": "0000000000000000000000000000000000000000000000000000000000000000"
}
```

The UUIDs are operator input and never appear in the output. Run:

```shell
chmod 600 /tmp/opennosh-natural-proof.json
python deploy/render_runtime.py natural-publication-proof \
  --request-file /tmp/opennosh-natural-proof.json
```

The verifier uses a repeatable-read, read-only transaction. It proves exact-one lineage and all ten
acknowledged effects, then independently reads and verifies the public latest record, immutable
record, provenance, signed manifest, and signed receipt. Exit `0` means `status=verified`; exit `2`
means a safe trust failure; exit `4` means an invalid request file; exit `5` means configuration,
database, or public-artifact verification could not complete. Save only the JSON report. Delete the
request file after the retained report digest is checked.

## Canary and stop conditions

Observe the activated services for 30 minutes. Sample queue, evidence worker, API, web, public
origin, release identity, and browser state at least every five minutes. Require one publication,
one receipt, one accepted event, ten verified steps, no duplicate external effect, no retry storm,
no worker replacement, and no stale/latest split.

Rollback immediately for a signature or digest mismatch, off-origin redirect, missing immutable
artifact, stale/latest identity split, duplicate intent/receipt/event/effect, cross-pack or
self-review binding, evidence loss, private-data exposure, queue growth across three samples,
provider error, restart loop, or any report status other than `verified`.

## Rollback

Disable governance mutations first. Disable publication claims and continuous claims, remove
activation IDs, and retain latest refresh. Then disable evidence uploads/sanitization, public reads,
and the remaining governance surfaces; scale evidence capacity to zero. A successful rollback has
fresh healthy API/web/publication instances, zero active/picked/claimable publication work, the last
verified release still readable, and all disabled values restored within five minutes.

Never delete or rewrite contribution drafts, evidence manifests or acknowledgements, review cases
or events, decisions, disputes, appeals, publication intents or steps, receipts, accepted events,
Git history, releases, public artifacts, or immutable evidence.
