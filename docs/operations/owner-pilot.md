# Owner-operated contribution pilot

An explicitly authorized pack owner may submit and approve their own food record.
Both actions use their actual account. This is **owner approval**, never independent
review. The independent natural-publication proof continues to reject self-review.

## Budget and evidence

The pilot uses the existing API, PostgreSQL database, and publication worker.
Public-document citations with `reference-only` source rights are copied as at most
8 KiB of canonical metadata per contribution version. PostgreSQL reads back the
stored bytes and records their verified digest in the same transaction. Repeated
attachments reuse the original acknowledgement. Source pages and images are not
uploaded, downloaded by this path, archived, or malware scanned. No new service,
storage provider, scanner, worker replica, or paid plan is required.

Reference-only citations can omit an observed source-byte digest. A missing digest
means no source-byte verification is claimed. Byte-backed archives still require
both a storage reference and an observed digest; photo-upload and scanner feature
gates remain disabled until those separate capabilities are configured.

## Authorize the actual owner

The current Render Blueprint enables the governance API, mutations, public decisions,
web steward interface, and signed public artifact reads. It sets
`GOVERNANCE_STEWARD_UI_ENABLED`, `GOVERNANCE_MUTATIONS_ENABLED`,
`GOVERNANCE_PUBLIC_DECISIONS_ENABLED`, `OPENNOSH_GOVERNANCE_STEWARD_UI_ENABLED`,
and `PUBLIC_ARTIFACT_READS_ENABLED` to `true`. Both publication-claim flags remain
`false`, `PUBLICATION_ACTIVATION_IDS` stays absent, and upload/sanitization flags
remain `false`. New deployments must complete readiness checks before activation.
Obtain the actual account UUID from the authenticated
account; never create a second identity to satisfy review checks. Record an ordinary
active steward grant for the exact pack through the existing audited governance
service. Then, with the migration database role:

```sh
python -m opennosh_api.governance.owner_admin \
  --actor-id ACTUAL_ACCOUNT_UUID --pack-id PACK_ID \
  --authorized-by AUTHORIZING_ACCOUNT_UUID \
  --reason 'Owner-operated pilot explicitly authorized by the project owner'
```

Supply `MIGRATION_DATABASE_URL` through the process environment, never an argument
or checked-in file. The web and publication roles can read owner authorizations but
cannot grant, alter, revoke, or delete them. Revocation uses the same command with
`--revoke` and a specific audit reason. A revoked grant cannot be rewritten.
Existing role revocation, recusal, pause, evidence, protected-check, exact-head, and
signature requirements still apply. A committed merge authorization retains its
historical meaning after later revocation.

## Submit and approve

1. Sign in using the actual owner account and complete any required recovery setup.
2. Start a contribution. Choose **Public document**, enter the public source URL,
   and confirm permission to preserve the reference.
3. Enter factual nutrition, portion, duplicate-review, and attribution details.
   Keep per-100-g nutrition separate from an actual serving's nutrition.
4. Choose **Reference only** in provenance. At final review, enter the source
   publisher and confirm that you reviewed the public source. Submission preserves
   the citation before opening its review case.
5. Open the case in the steward queue. The owner notice explains that both actions
   belong to the same account. Claim the case and approve the exact candidate pack
   files and current base commit, with a factual review reason.
6. Leave the long-running worker's claim flags disabled. In the Render publication
   worker shell, run the bounded owner command with the same actor and pack:

   ```sh
   python deploy/render_runtime.py owner-publication \
     --actor-id ACTUAL_ACCOUNT_UUID \
     --pack-id PACK_ID
   ```

   The command reads at most two candidates and continues only when exactly one
   nonterminal publication has a complete same-actor owner lineage. It enables
   claims only inside that process for the selected immutable intent, runs the
   existing evidence, exact-head, protected-check, attestation, signing, receipt,
   and pointer checks, prints a redacted terminal report, and exits. It never
   changes the Render environment or scans unrelated queued records.

   `--timeout-seconds` defaults to 900 and bounds selection, startup, and polling;
   the worker then performs its bounded shutdown drain. A timeout exits nonzero
   without deleting the intent or its history. The direct application command is
   `opennosh commons run-owner-publication --actor-id ACTOR_UUID --pack-id PACK_ID --json`;
   the Render wrapper supplies its existing scoped credentials.

The public decision exposes `approval_mode: owner` and both actor IDs. Its signed
publication receipt uses `approving_actor_scope: pack:PACK_ID:owner`, preserving the
existing receipt format. Existing independent receipts keep their original scope.
Verify the signature, decision/draft lineage, merged pack data, public artifacts,
and final Commons state before calling the live contribution published.

If the command returns `blocked`, `failed`, `publish_blocked`, or `quarantined`,
keep the original history and inspect the case and publication state for the governance or provider cause.
If it reports zero or multiple candidates, inspect the owner queue and identify
one exact record before retrying; never relax the selector or enable continuous
claims to clear a backlog.

## Run one small owner mission

Owner missions use the same account and active pack grant. Keep all five mission flags disabled
while preparing and running the one-off. After the selected records have published receipts, run
the read-only readiness command from the Render API service shell:

```sh
python deploy/render_runtime.py owner-mission-readiness \
  --actor-id ACTUAL_ACCOUNT_UUID \
  --mission-key indian-sweets-owner-pilot-2026-09 \
  --pack-id indian-sweets \
  --record-id PUBLISHED_RECORD_ID
```

The report binds the account by hash, mission key, pack, exact draft versions, accepted events,
receipt digests, disabled flag state, one-off operations, and planned public exposure into one
SHA-256 digest. Obtain explicit authorization for that digest. Then run the matching command:

```sh
python deploy/render_runtime.py owner-mission \
  --actor-id ACTUAL_ACCOUNT_UUID \
  --mission-key indian-sweets-owner-pilot-2026-09 \
  --pack-id indian-sweets \
  --record-id PUBLISHED_RECORD_ID \
  --approved-readiness-digest APPROVED_SHA256
```

The command refuses a stale or changed digest. It deterministically proposes and owner-approves the
mission, binds only the exact owner-authored published draft versions, rebuilds from reconciled
accepted events and signed receipt records, atomically activates the checkpoint, and exits. Safe
retries reuse the same lifecycle facts and checkpoint. It does not change Render settings, create a
service, or consume a separate database role.

After the report shows `approval_mode=owner` and `accepted_count=acceptance_target`, expose only the
mission catalog: set `MISSION_PUBLIC_ENABLED=true` on `opennosh-api` in the Blueprint and add
`commons-missions` to the web navigation list through a reviewed deployment. Keep mutation,
long-running projection, activity-map, and mission-pack-release flags false. Verify the public
mission response against the report's mission ID, count, and event-set digest.

Rollback through the Blueprint by restoring `MISSION_PUBLIC_ENABLED=false` and removing
`commons-missions` from public navigation. The mission facts, bindings, checkpoints, accepted
events, releases, and receipts remain append-only. A successful rollback hides the navigation and
returns the disabled empty mission response while the API, publication worker, and Commons snapshot
remain healthy.

The new migration deliberately refuses rollback when owner decisions or citation
copies exist. Do not delete this history to force a downgrade; apply a forward fix.
