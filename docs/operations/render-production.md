# Render production operations

## Topology and cost boundary

`render.yaml` is the production source of truth. It creates four Ohio services/resources:

- `opennosh-web`: Starter public Docker web service;
- `opennosh-api`: Starter private Docker service;
- `opennosh-publication`: Starter private background worker; and
- `opennosh-db`: Basic-256mb PostgreSQL 16 with 5 GB storage and no public ingress.

T32 also attaches one 1 GB `opennosh-public-artifact-state` disk to `opennosh-api` for the
anti-rollback checkpoint and verified release cache. T33.3 adds the approved USD 7 per month
publication worker, bringing the expected baseline to approximately USD 27.25 per month before
unusual bandwidth or build usage. The worker deliberately reuses the API Docker image; its boto3
R2 client brings a roughly 16 MB compressed botocore wheel into both images in exchange for one
build path and identical runtime packaging. Revisit that trade only if image transfer or startup
budgets regress. Preview environments are disabled so pull requests cannot create additional paid
resources. Changing a plan, region, disk size, replica
count, preview policy, or database exposure requires a reviewed pull request and renewed cost
review. The disk intentionally trades zero-downtime API deploys and horizontal API scaling for the
smallest independent durable checkpoint in this bounded release.

## Provisioning

1. Merge the reviewed Blueprint change only after repository CI passes.
2. In Render, create a Blueprint from `https://github.com/RujitRaval/opennosh` and `render.yaml`.
3. Confirm exactly the four resources above, the `ohio` region, one instance per service, the approved
   plans, and one 1 GB API disk before accepting the charge.
4. Let Render generate the database-role passwords, proxy token, and cursor-signing secret. Never
   copy their values into Git, chat, shell history, or this runbook.
5. Wait for `opennosh-api` pre-deploy to create the `opennosh_migration` and `opennosh_web` roles,
   verify the 100-connection ceiling, run Alembic once, refresh runtime grants, and idempotently
   load the four bundled community starter packs through the migration credential.
6. Verify `opennosh-web` through its temporary `onrender.com` hostname before changing Cloudflare.

The owner database URL exists only in the Render wrapper environment. Before the API starts, the
wrapper derives the bounded web-role URL and removes the owner URL, migration password, and raw
generated secrets from the application process environment. The API service is private; browsers
reach it only through the authenticated Next.js proxy.

The initial hosted release intentionally leaves the signed Commons artifact paths and public
artifact origin unset. Public snapshot consumers therefore use their explicit unavailable/quiet
states, while food pages stay on the PostgreSQL read path until an offline-signed release and
durable HTTPS artifact origin are provisioned. `PUBLIC_ARTIFACT_READS_ENABLED=false` is the explicit
web dark-launch default. Never enable a filesystem path or a development verification key in
production.

The web service enables `OPENNOSH_PUBLIC_NAV_FEATURES=explorer-search`, so `/en/explore` searches
the live PostgreSQL starter catalogue and shows each result's pack, source, contributor, and license.
This operational search is not a signed Commons release. Keep `PUBLIC_ARTIFACT_READS_ENABLED=false`
until the separately verified immutable-artifact activation below is complete. Strength entry is
also held closed with `OPENNOSH_TRACKER_STRENGTH_ENTRY_ENABLED=false` until the attributed exercise
catalogue has been loaded and checked.

### T32 bounded artifact read-plane activation

Provision an independently durable Cloudflare R2 origin before enabling the web switch. The Blueprint
mounts `/var/lib/opennosh/public-artifacts` on the approved 1 GB disk and preconfigures separate
checkpoint and verified-cache paths. Configure the private API with `PUBLIC_ARTIFACT_BASE_URL`,
approved `PUBLIC_COMMONS_VERIFYING_KEYS`, and approved
`PUBLICATION_RECEIPT_VERIFYING_KEYS`. The API process receives public verification keys only.
`PUBLIC_ARTIFACT_READS_ENABLED=false` remains the explicit web default until the full ceremony
below passes. T32 does not configure live forge Apps or publication workers; that remains T33.
The pointer expires after 23 hours; without T33 refresh automation, the verified release remains
available but correctly reports a stale latest alias.

#### 1. Offline signing ceremony

Generate independent Ed25519 manifest and receipt keys on an encrypted operator-controlled volume.
Store each private key as one unpadded base64url-encoded 32-byte seed in a mode-`0600` file. Never
place either private key in GitHub, Render, Cloudflare, chat, shell history, the release directory, or
an online environment. Keep a separate encrypted recovery copy. The builder rejects group-readable
keys, reserved development/acceptance key IDs, known nonproduction public keys, and a shared key used
for both roles.

From the exact reviewed source commit, build and immediately re-verify the complete four-pack release:

```bash
opennosh commons build-starter-release \
  --packs-root packs \
  --output /secure/operator-volume/opennosh-0.56.0.0 \
  --release-version 0.56.0.0 \
  --published-at 2026-08-27T02:00:00+00:00 \
  --source-commit "$(git rev-parse HEAD)" \
  --manifest-key-id opennosh-manifest-2026-01 \
  --manifest-private-key /secure/offline-keys/manifest.key \
  --receipt-key-id opennosh-receipt-2026-01 \
  --receipt-private-key /secure/offline-keys/receipt.key \
  --decision-reference https://github.com/RujitRaval/opennosh/issues/97 \
  --approving-actor github:RujitRaval \
  --json
```

Review `inventory.json`. It is non-secret and records the source commit, public verification keys,
food/pack/object counts, byte sizes, and every object digest. Record the emitted
`inventory_sha256` separately from the release directory; it is the independent trust anchor for every
later verification. On a second trusted machine if available, re-run:

```bash
opennosh commons verify-starter-release /secure/operator-volume/opennosh-0.56.0.0 \
  --inventory-sha256 REVIEWED_INVENTORY_SHA256 \
  --json
```

#### 2. R2 origin and immutable-first upload

Create `opennosh-public-commons` and a separate disposable
`opennosh-public-commons-drill` R2 bucket. Expose the production bucket through a dedicated HTTPS
custom domain or R2 public development URL; the API needs anonymous reads only and receives no R2
write credential. Keep the web switch off.

Use an already installed and explicitly approved Wrangler executable. The publisher verifies the
local release first, reuses only byte-identical existing immutable objects, rejects conflicts,
uploads and re-reads every missing immutable object, and replaces `latest/v1.json` last:

```bash
opennosh commons publish-starter-release /secure/operator-volume/opennosh-0.56.0.0 \
  --inventory-sha256 REVIEWED_INVENTORY_SHA256 \
  --bucket opennosh-public-commons \
  --origin-url https://commons-artifacts.opennosh.org \
  --wrangler /absolute/path/to/wrangler \
  --json
```

The immutable objects receive a one-year immutable cache policy. The latest pointer receives
`must-revalidate`. Never repair a conflict by overwriting an immutable key; stop and investigate.

#### 3. Render configuration and cache warm

Apply the Blueprint and confirm exactly one 1 GB disk is attached to `opennosh-api`. In the private
API environment, set `PUBLIC_ARTIFACT_BASE_URL` to the reviewed R2 HTTPS origin,
`PUBLIC_COMMONS_VERIFYING_KEYS` to
`<manifest_key_id>:<manifest_verifying_key>`, and
`PUBLICATION_RECEIPT_VERIFYING_KEYS` to
`{"<receipt_key_id>":"<receipt_verifying_key>"}`, using only values from `inventory.json` after its SHA-256 matches the separately recorded trust anchor.
Deploy the API while the web flag remains false.

Warm every exact artifact through the deployed API, then resolve latest once so the disk holds the
complete verified release and anti-rollback checkpoint:

```bash
opennosh commons warm-live-release /secure/operator-volume/opennosh-0.56.0.0 \
  --inventory-sha256 REVIEWED_INVENTORY_SHA256 \
  --api-origin https://opennosh.org \
  --concurrency 8 \
  --json
```

#### 4. Required failure drills

Use the disposable drill bucket to confirm a modified record, manifest, receipt, or pointer never
returns unverified data and a lower/equivocating latest pointer is refused. Never tamper with the
production bucket. After the production cache is warm, temporarily deploy the private API with a
valid HTTPS origin that is deliberately unavailable. Confirm a latest record survives the restart
from the checkpoint and verified cache with `x-opennosh-release-state: stale`, while pinned record,
provenance, manifest, and pack routes remain `200`. Restore the reviewed R2 origin and repeat the
live warmer. Perform this drill during an operator window because the attached disk disables
zero-downtime API deploys.

Verify the latest and one pinned release while PostgreSQL access is paused:

```text
GET /api/v1/public/foods/community/{source_id}
GET /api/v1/public/releases/{release}/foods/community/{source_id}
GET /api/v1/public/releases/{release}/foods/community/{source_id}/provenance
GET /api/v1/public/releases/{release}/manifest
GET /api/v1/public/releases/{release}/packs/{pack_id}/{pack_version}/download
```

Each exact-version response must be `200`, immutable, independent of PostgreSQL, and carry the
expected release header.

#### 5. Activation and rollback

Only after the local verifier, R2 re-read, live cache warm, tamper, rollback/equivocation, complete
origin outage, restart, and PostgreSQL-independence checks pass may
`PUBLIC_ARTIFACT_READS_ENABLED=true` be set on `opennosh-web`. Deploy the web service, verify one
search-to-record journey plus pinned provenance and pack download, and monitor API/browser errors.

Rollback is one web-only change: set `PUBLIC_ARTIFACT_READS_ENABLED=false` and redeploy
`opennosh-web`. Do not delete, rewrite, or roll back immutable R2 objects or the API checkpoint.
Investigate before any new release or pointer is published.

### T33.3 online latest-pointer renewal

T33.3 keeps the already verified release and every immutable artifact unchanged. One isolated
background worker validates the complete current release, signs a replacement `latest/v1.json`
after 20 hours, writes only that mutable pointer, and requires an exact R2 read-back. The write
uses the verified current R2 ETag as an `If-Match` precondition, so stale origins and concurrent
publishers cannot replace a newer pointer. The pointer lifetime is 23 hours and the worker checks
immediately at startup and then hourly. Immutable manifests and receipts are verified directly
through authenticated R2 reads, bypassing the cacheable public origin. Seven single-attempt R2
operations have a 2.5-second absolute elapsed deadline, and the sole public-origin pointer read has
an absolute 2-second deadline. SDK work runs on isolated daemon operations so a stuck SDK thread
cannot hold process shutdown open. Their 19.5-second network budget fits
inside Render's 30-second shutdown window. Any validation,
signing, upload, or read-back failure terminates the worker so Render restarts it and exposes the
failure. Contribution claims, forge access, governance attestation, and database access remain
disabled until later T33 slices.

#### 1. Create an independent online signing identity

Generate a new Ed25519 seed for the online manifest signer. It must not reuse either offline private
key, a development key, or a reserved key ID. Store the unpadded base64url 32-byte seed only in the
Render secret group `opennosh-online-manifest-signer`. That group contains exactly:

```text
ONLINE_MANIFEST_SIGNING_KEY_ID=<reviewed-online-key-id>
ONLINE_MANIFEST_SIGNING_KEY=<secret-seed>
PUBLIC_COMMONS_VERIFYING_KEYS=<offline-id>:<offline-public-key>,<online-id>:<online-public-key>
PUBLICATION_RECEIPT_VERIFYING_KEYS={"<receipt-id>":"<offline-receipt-public-key>"}
```

Add the online public key, but never its private seed, to the API's existing
`PUBLIC_COMMONS_VERIFYING_KEYS` value before activating renewal. Keep the offline manifest and
receipt keys in their encrypted operator-controlled storage; online renewal does not replace the
offline release ceremony.

#### 2. Create a bucket-scoped R2 writer

Create a Cloudflare R2 API token limited to object read and write access for only
`opennosh-public-commons`. Store it in the Render secret group `opennosh-r2-writer`, containing
exactly:

```text
R2_ACCOUNT_ID=<account-id>
R2_BUCKET=opennosh-public-commons
R2_ACCESS_KEY_ID=<access-key-id>
R2_SECRET_ACCESS_KEY=<secret-access-key>
```

Link both new groups only to `opennosh-publication`. The API and web services must receive neither
private signing material nor R2 write credentials. Keep `opennosh-publication-forge` and
`opennosh-governance-attester` unlinked during T33.3.

#### 3. Activate and prove refresh-only behavior

Apply the reviewed Blueprint only after both secret groups exist, the API trusts the online public
key, and repository checks pass. Confirm the worker starts with
`PUBLICATION_CLAIMS_ENABLED=false` and `LATEST_REFRESH_ENABLED=true`. Its sanitized runtime
environment must not contain a database URL, database passwords, forge credentials, governance
credentials, or sibling-service secrets.

Capture `latest/v1.json` before and after the first eligible renewal. Verify its Ed25519 signature
and confirm all of the following:

```text
release_version        unchanged
manifest key/digest    unchanged
manifest size/type     unchanged
issued_at              advanced
expires_at             advanced by the configured 23-hour lifetime
```

Re-fetch the manifest, receipt, one record, one provenance page, and one pack through the live API.
They must resolve to the same immutable release and remain independently verified. Check the
worker logs for one successful read-back and no repeated publication loop.

#### 4. Failure handling and rollback

An invalid current pointer, changed manifest, untrusted online key, or unavailable origin stops
renewal before a write. A failure after R2 accepts the pointer, including a missing or mismatched
read-back, stops the worker without claiming success; inspect and verify the live pointer before
restarting. Restore the credential or origin and let Render restart the worker; never overwrite or
delete immutable objects.

To stop automation, disable or scale the publication worker to zero. Because the current pointer
will then expire within 23 hours, schedule a reviewed offline pointer ceremony before stopping it
for longer than that window. Disabling renewal does not authorize rolling back the checkpoint,
rewriting R2 history, or enabling contribution publication.

#### 5. Rotate the online manifest key

Add the replacement public key to both the worker and API verifier rings before changing the online
signing key ID or seed. Deploy and verify that trust-only change first, then update the worker's
signing identity and confirm one new pointer is accepted by the live API. Retain the prior public key
for at least 24 hours and until the live pointer and durable checkpoint both use the replacement key.
Remove only the retired public key in a later reviewed deployment. Online receipt-key rotation and
offline root-key rotation are separate ceremonies and remain outside T33.3.

## T33.4 staged contribution activation

The first T33.4 slice adds the bounded runtime and shutdown contract without activating live
contribution claims. Keep the Blueprint at:

```text
PUBLICATION_CLAIMS_ENABLED=false
PUBLICATION_PREACTIVATION_SMOKE_ENABLED=false
LATEST_REFRESH_ENABLED=true
```

Keep the claims-time groups unlinked while this flag is false. The reviewed activation Blueprint
will link each group only to `opennosh-publication`:

| Render group | Exact worker-only values |
|---|---|
| `opennosh-publication-forge` | `GITHUB_FORGE_REPOSITORY_ID`, `GITHUB_FORGE_APP_ID`, `GITHUB_FORGE_INSTALLATION_ID`, `GITHUB_FORGE_PRIVATE_KEY` |
| `opennosh-governance-attester` | `GITHUB_ATTESTER_APP_ID`, `GITHUB_ATTESTER_INSTALLATION_ID`, `GITHUB_ATTESTER_PRIVATE_KEY` |
| `opennosh-online-receipt-signer` | `ONLINE_RECEIPT_SIGNING_KEY_ID`, `ONLINE_RECEIPT_SIGNING_KEY`, `PUBLICATION_RECEIPT_VERIFYING_KEYS` |
| `opennosh-r2-writer` | `R2_ACCOUNT_ID`, `R2_BUCKET`, `PUBLICATION_ARTIFACT_BUCKET`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` |

The Forge and attester App IDs, installation IDs, and RSA public-key fingerprints must all differ.
The receipt Ed25519 public key must differ from the online manifest key and every offline
root/recovery key. `PUBLICATION_ARTIFACT_BUCKET` must equal `R2_BUCKET`. A missing, malformed,
aliased, or cross-role value aborts settings validation before a queue pool is created. The API
bootstrap strips these values even if a provider accidentally injects them.

The production registry uses separate immutable staging keys for signing and publication. Release
signatures are self-verified under `signatures/releases/v1/` before the canonical release is
published. Receipt signatures are read back under `signatures/receipts/v1/`; only the following
registry step writes `receipts/v1/{publication_id}.json`, and the durability step writes the
independent digest-addressed copy under `durability/receipts/`.

Canonical production material is re-read from the protected merged Git tree, never from mutable
request data. The bounded source writes merge proof to
`durability/git/{merged_commit}.json`, evidence proof to
`durability/evidence/{sha256}.json`, content-addressed record, provenance, and pack objects,
the signed release to `releases/v1/release-{version}.json`, and the independent release proof to
`durability/releases/{sha256}.json`. The first automatic release is additive: an existing pack
ID or community-food slug fails closed until replacement semantics have their own reviewed
protocol.

Before arming an activation ID or deriving a publication database URL, run the zero-claim
production preactivation ceremony. The Blueprint supplies the database owner reference and the
generated publication-role password to the Render wrapper, but refresh-only mode strips both
before starting the worker. Claims mode alone derives and retains the bounded
`PUBLICATION_DATABASE_URL`:

1. Link the Forge, attester, receipt-signer, and R2 groups only to
   `opennosh-publication`.
2. Keep `PUBLICATION_CLAIMS_ENABLED=false`, remove
   `PUBLICATION_ACTIVATION_IDS`, and confirm the worker has no publication database URL.
3. Set `PUBLICATION_PREACTIVATION_SMOKE_ENABLED=true` and deploy only the worker.
4. Require the log `Zero-claim publication preactivation smoke passed` with
   `adapter_count=10`, all canonical step names, and `claims_enabled=false`.
5. Set `PUBLICATION_PREACTIVATION_SMOKE_ENABLED=false` and redeploy before arming a live
   contribution.

The smoke parses and cross-checks every isolated production credential and constructs the exact
ten-adapter registry. It never opens PostgreSQL, claims a queue row, calls GitHub, signs a payload,
or writes R2.

The final `copy_receipt` adapter is also the only contribution pointer activator. It first
requires the independent durable receipt copy to read back as verified. It then re-reads and
cryptographically verifies the canonical receipt registry entry and signed release manifest,
including their release version, publication ID, ordered release/receipt times, manifest digest,
and `copy_release` proof binding. Any missing material is retryable; invalid signatures,
non-canonical payloads, or binding conflicts quarantine the publication without pointer I/O.

Only after those checks may the adapter sign a pointer with a lifetime of at most 24 hours and
replace `latest/v1.json` using the current R2 ETag. A same-release CAS winner is an idempotent
success. A valid newer pointer terminally supersedes the older publication after its durable
receipt is proved, preventing stale work from spinning or rolling the Commons backward. An
untrusted pointer, an invalid lifetime, or the same release version bound to a different manifest
fails closed. A lost write response is reconciled by the next signed-pointer observation.

Claims may be enabled only after the production registry supplies every canonical
`PublicationStepName` adapter. The worker validates that exact ten-step registry before opening
its PostgreSQL pool. Render must then provide one canonical UUID through
`PUBLICATION_ACTIVATION_IDS` and keep latest refresh enabled:

```text
PUBLICATION_CLAIMS_ENABLED=true
PUBLICATION_ACTIVATION_IDS=<one-publication-intent-uuid>
LATEST_REFRESH_ENABLED=true
```

The activation ID is applied inside PgQueuer's PostgreSQL dequeue statement to both new and
stale-job recovery paths. Unrelated queued publication rows remain unclaimed. Claims and pointer
refresh run as sibling tasks: either loop failing cancels and closes the other; SIGTERM stops new
claims, drains leased work within the existing 30-second deadline, closes both resources, and
leaves immutable artifacts untouched.

Before the claims loop starts, the worker locks only the configured publication intent and checks
its active typed wake-ups. It preserves a valid queued or picked wake-up, or creates one
revision-bound wake-up when none exists; it never scans or changes unrelated publication work.
Startup fails closed for an unknown or terminal intent, an unbound or future-revision wake-up, or a
conflicting deduplication key. Require the redacted
`Publication activation wake-up ready` log with its outcome, intent state, workflow revision,
active-job count, and eligibility result before treating claims as armed. The log deliberately
omits the activation UUID.

Do not add a second ID, disable refresh while claims are enabled, or use the activation variable
as a general queue filter. Roll back by setting claims to `false`, removing the activation ID,
and leaving refresh enabled. This stops new queue claims without deleting immutable artifacts or
rewinding `latest`. Before the first live contribution, capture the activation UUID, current
pointer digest and ETag, deploy with exactly the three values above, then require one verified
durable receipt and a newer, correctly bound public pointer before clearing the activation ID.

If the selected intent becomes terminal before merge authorization, keep claims disabled and leave
that intent unchanged. A governed resubmission is allowed only for `blocked`, `failed`,
`publish_blocked`, or `quarantined` history with no intervention and no committed merge
authorization. From an authorized operator environment, review the current durable evidence and a
fresh exact `main` commit, then run:

```bash
opennosh commons resubmit-publication \
  --prior-publication-intent-id TERMINAL_PUBLICATION_INTENT_UUID \
  --steward-actor-id REVIEWED_HUMAN_ACTOR_UUID \
  --expected-base-commit REVIEWED_FRESH_MAIN_COMMIT \
  --reason "Retry unchanged reviewed material from fresh main" \
  --json
```

The command must preserve the terminal intent, create exactly one lineage-bound successor decision
and pending intent, rebind the same verified payload and evidence to the fresh base, and enqueue one
activation wake-up. An identical retry is idempotent; a conflicting second successor fails closed.
Active intervention, committed merge authorization, publication pause, missing steward authority,
self-review, or recusal also fails closed. Save the redacted JSON receipt and confirm the predecessor
is still terminal before proceeding. Keep `PUBLICATION_CLAIMS_ENABLED=false` and
`PUBLICATION_ACTIVATION_IDS` unset until the new intent UUID has been independently reviewed and is
the sole value selected through the activation ceremony above. Never select the terminal UUID.

### T33.4a controlled first contribution intake

The first live contribution is one reviewed USDA FoodData Central Foundation record: FDC 1105314,
`Bananas, ripe and slightly ripe, raw`. USDA is the attributed source, not an opennosh signer. Its
public document is preserved as immutable citation evidence and remains `reference_only`; never
describe the contribution as `source_verified`. The resulting `common-fruits` pack is CC0-1.0 and
contains exactly one food record.

Run this ceremony from a clean checkout of the reviewed release after its migration has been
deployed. Keep publication claims disabled and federation unconfigured throughout intake:

```text
PUBLICATION_CLAIMS_ENABLED=false
PUBLICATION_ACTIVATION_IDS=<unset>
LATEST_REFRESH_ENABLED=true
```

1. Download the canonical USDA JSON response for FDC 1105314 over HTTPS to an operator-controlled
   regular file. Do not paste provider responses or credentials into chat or shell history.
2. Create the deterministic, mode-`0600` review package. Re-running against byte-identical USDA
   JSON must return the same package; a different source digest or existing output fails closed:

   ```bash
   opennosh commons prepare-usda-first-contribution \
     --source-json /secure/operator-volume/usda-fdc-1105314.json \
     --output /secure/operator-volume/usda-fdc-1105314.opennosh.json \
     --json
   ```

3. Inspect the source digest, package digest, deterministic IDs, evidence manifest, and exact
   three-file `common-fruits` change set. Independently confirm 97 kcal, 0.74 g protein, 0.29 g
   fat, and 23.0 g carbohydrate per 100 g, publication date 2020-04-01, and CC0-1.0 attribution.
4. Select an existing active human opennosh account as the first `common-fruits` steward. Record
   that person's actor UUID and the exact reviewed 40- or 64-character lowercase base commit. A
   disabled service identity cannot approve, and the USDA source identity cannot review itself.
5. Provision one temporary database administration URL and one bucket-scoped R2 read/write token
   only in the operator environment, using the `FIRST_CONTRIBUTION_` prefix. The database URL must
   use the capacity-governed administration role. Never link these values to the API, web service,
   or long-running publication worker. Set exactly:

   ```text
   FIRST_CONTRIBUTION_ADMINISTRATION_DATABASE_URL=<temporary-administration-url>
   FIRST_CONTRIBUTION_DATABASE_CAPACITY_MANIFEST_PATH=<reviewed-capacity-manifest>
   FIRST_CONTRIBUTION_REVIEWED_BASE_COMMIT=<independently-reviewed-fresh-main>
   FIRST_CONTRIBUTION_REVIEWED_PACKAGE_DIGEST=<independently-recorded-package-sha256>
   FIRST_CONTRIBUTION_R2_ACCOUNT_ID=<account-id>
   FIRST_CONTRIBUTION_R2_BUCKET=opennosh-public-commons
   FIRST_CONTRIBUTION_R2_ACCESS_KEY_ID=<temporary-bucket-access-key>
   FIRST_CONTRIBUTION_R2_SECRET_ACCESS_KEY=<temporary-bucket-secret>
   ```

   Record the package digest separately from the package file. The command rejects a different
   package digest, `--expected-base-commit`, or bucket before it builds database or R2 clients.
6. Commit once, with explicit first-steward bootstrap authority:

   ```bash
   opennosh commons commit-usda-first-contribution \
     --package /secure/operator-volume/usda-fdc-1105314.opennosh.json \
     --steward-actor-id REVIEWED_HUMAN_ACTOR_UUID \
     --expected-base-commit REVIEWED_BASE_COMMIT \
     --reason "Approve the reviewed USDA FDC 1105314 common-fruits seed" \
     --bootstrap-steward \
     --json
   ```

7. Save only the redacted receipt. Confirm one disabled USDA service principal, one
   `reference_only` evidence acknowledgement, one steward assignment, one governance decision,
   one `publication_pending` draft, one publication intent, and one queue wakeup. Re-running the
   same command must return the same receipt without another R2 write or database row.
8. Independently confirm that the emitted publication-intent UUID is the only candidate for the
   later activation allowlist, while `PUBLICATION_CLAIMS_ENABLED=false` and
   `PUBLICATION_ACTIVATION_IDS` remains unset. No GitHub branch, signed release, pointer update, or
   federation enrollment belongs to this ceremony.
9. Revoke the temporary database and R2 credentials, securely remove their exact local files and
   the downloaded provider response, and retain the non-secret package and redacted receipt with
   the operational proof.

Abort before the database commit if the USDA fields, digests, exact files, steward, or base commit
do not match the review. If R2 accepts the immutable citation but the command loses its response,
re-run the identical package: it reconciles by read-back. If the database transaction fails, leave
the immutable citation in place and retry only after diagnosing the error. Never delete or
overwrite that object, manually edit partial rows, enable claims to test the intake, or bootstrap a
second `common-fruits` steward. Stopping claims is the rollback boundary; database history is
forward-fixed through a separately reviewed change.

### T33.4b first invitation-only federation enrollment

Run this one-maintainer ceremony only after the governed `common-fruits` release and its signed
publication receipt are live. Keep `PUBLICATION_CLAIMS_ENABLED=false` and
`PUBLICATION_ACTIVATION_IDS` unset throughout. The public API has no federation mutation routes;
every state change uses the isolated administration CLI and is written to the append-only audit
ledger.

1. Pin one external GitHub identity, the canonical repository ID and name, `common-fruits`, the
   inviter's existing human actor UUID, and the Forge GitHub App verifier. Supply the administration
   database URL and App private key only to the temporary operator environment:

   ```text
   FEDERATION_ADMINISTRATION_DATABASE_URL=<temporary-administration-url>
   FEDERATION_DATABASE_CAPACITY_MANIFEST_PATH=<reviewed-capacity-manifest>
   FEDERATION_ALLOWED_GITHUB_ACCOUNT_ID=<immutable-external-account-id>
   FEDERATION_ALLOWED_GITHUB_LOGIN=<external-login>
   FEDERATION_ALLOWED_REPOSITORY_ID=<immutable-repository-id>
   FEDERATION_ALLOWED_REPOSITORY=<owner/repository>
   FEDERATION_ALLOWED_PACK_ID=common-fruits
   FEDERATION_ALLOWED_PUBLIC_ORIGIN=https://opennosh.org
   FEDERATION_INVITER_ACTOR_ID=<existing-human-actor-uuid>
   FEDERATION_GITHUB_APP_ID=<forge-app-id>
   FEDERATION_GITHUB_APP_PRIVATE_KEY=<temporary-in-memory-pem>
   ```

2. Generate the maintainer's Ed25519 role key offline. Keep the private key outside Render and
   opennosh; provide only its public key to the operator. Create the single expiring invitation with
   `opennosh federation invite`, capture its token directly to a mode-`0600` file, and never place
   the token in logs, chat, command arguments, or the database. A second invitation must fail.
3. Run `opennosh federation verify --token-file ... --public-key-file ...`. Verification must bind
   the immutable GitHub account ID and login, repository ID and name, exact pack, App installation,
   repository visibility to that installation, and external maintainer write control. The token is
   single-use and its reuse must fail.
4. Inspect the redacted `verified` status, then run `opennosh federation activate` with the reviewed
   actor and reason. Confirm exactly one active maintainer and one unretired role key.
5. The external maintainer signs a domain-separated federation release statement for the already
   governed live release. The statement must bind the maintainer, repository, pack, publication
   UUID, release version, signed manifest digest, publication-receipt digest, HTTPS manifest URL,
   issue time, and current key ID. Run `opennosh federation publish-release --release-file ...` and
   confirm its statement digest is recorded once and binds the canonical receipt ledger.
6. Generate an independent replacement Ed25519 key offline and run
   `opennosh federation rotate-key`. Resubmit the original signed statement and require exit code 3
   with `release_key_retired_or_untrusted`; the rejected operator attempt must appear in the audit
   ledger without key material or invitation tokens.
7. Run `opennosh federation quarantine` with the reviewed reason. Confirm future scoped claims fail
   closed as `publish_blocked`, while the previously verified release, manifest, receipt, and latest
   pointer remain publicly readable and unchanged.
8. Save a redacted proof containing the immutable public IDs, maintainer UUID, state timestamps,
   key fingerprints, statement/manifest/receipt digests, audit event counts, retired-key rejection,
   quarantine result, claims-disabled state, and live HTTP checks. Revoke the temporary database and
   GitHub App credentials, and securely remove the exact token, private-key, and statement files.

Abort on any scope mismatch, provider outage, insufficient repository permission, signature or
receipt mismatch, duplicate invitation/release, unexpected active maintainer, claims-enabled state,
or public-read regression. Do not edit federation rows manually, reuse an invitation, reactivate a
quarantined maintainer, or delete the last verified public release. Remediation is a reviewed
forward change; a distinct enrollment requires a later federation phase.

### T33.5 live federation failure-drill matrix

Run this matrix only after the T33.4 enrollment is quarantined, its temporary credentials are
revoked, and the last verified `common-fruits` release is publicly readable. Keep
`PUBLICATION_CLAIMS_ENABLED=false`, remove `PUBLICATION_ACTIVATION_IDS`, and keep
`LATEST_REFRESH_ENABLED=true` until the complete report validates and a separate activation is
approved. Generate the reviewed plan before touching a provider:

```bash
opennosh federation drill-plan --json
```

Capture one baseline containing the production commit, release version, publication UUID,
manifest and receipt SHA-256 digests, online pointer key ID, and `https://opennosh.org`. Verify and
digest these seven redacted HTTP responses: API health, latest pointer, exact manifest, latest
food, pinned food, provenance, and receipt. Store provider responses outside Git and retain only
stable public identifiers plus SHA-256 digests in the report.

Run the cases in canonical order and never overlap them:

| # | Case | Live boundary | Restoration proof |
|---|---|---|---|
| 1 | Idempotent replay | Replay only the controlled synthetic delivery/wake; never a new contribution | One acknowledged effect and zero later provider/database deltas |
| 2 | Forge outage | Use the controlled unavailable adapter or a disposable invalid authentication probe before commit creation | Normal Forge authentication succeeds again; no branch or PR was created |
| 3 | Signer outage | Refuse the controlled signing request before registry publication | Online signer verifies a non-published challenge after restoration |
| 4 | R2 conflict | Use a dedicated non-latest drill key preloaded with different immutable bytes | Conflict is returned, original bytes remain, latest is untouched |
| 5 | Worker restart | Restart only the refresh-only publication worker | A fresh instance is live and refresh-only within ten minutes |
| 6 | Database lease recovery | Exercise lease loss in the controlled PostgreSQL fixture; restart live worker during a non-claiming readiness probe | Probe and worker recover with zero claimable work |
| 7 | Rollback/equivocation | Submit older and same-version/different-manifest candidates only to the controlled activation adapter | Both are rejected and the live pointer is byte-identical |
| 8 | Forge credential rotation | Add a replacement key, deploy a fresh worker environment, prove it, then revoke the old key | New authentication succeeds and old authentication fails without printing tokens |
| 9 | Claims pause/resume | Restart refresh-only with claims disabled and activation IDs absent | Fresh instance logs claims disabled and public refresh remains healthy |
| 10 | Public navigation rollback | Remove only `explorer-search` from `OPENNOSH_PUBLIC_NAV_FEATURES`, deploy web, then restore the exact prior value | Navigation hides within five minutes, public APIs remain 200, then navigation is restored |

For every case, record ordered aware timestamps for start, failure observation, restoration start,
and full recovery. Recovery must complete within 600 seconds, except public navigation rollback,
which must complete within 300 seconds. Re-run all seven public checks after restoration and bind
their digests to the baseline. The release version, publication UUID, manifest digest, receipt
digest, pointer key ID, and public origin must not change.

Build the final report with exactly the fields emitted by `drill-plan`. Never include raw logs,
provider responses, database URLs, credential-bearing URLs, environment dumps, PEM blocks, tokens,
passwords, or secret field names. Validate the exact file before upload:

```bash
opennosh federation validate-drill-report \
  --report-file /secure/operator-volume/t33-5-redacted-report.json \
  --json
```

Post only the validator summary, canonical report digest, and redacted artifact/provider links to
GitHub issue #130. Stop immediately on identity drift, non-200 public checks, recovery timeout,
missing restoration, false publication, overwrite, or secret-pattern rejection. Restore the
current case before investigating. Do not proceed to the next case and do not enable claims.

Normal production claims remain a separate activation gate. They require a complete exit-0 report,
a healthy refresh-only worker, unchanged public identity, all protected checks green, and explicit
operator approval. The code PR, scheduled synthetic matrix, or a partial live report cannot satisfy
that gate.

### T33.6 bounded continuous-claims activation gate

T33.6 adds the ability to claim every eligible publication wake-up, but it does not enable that
ability. Merge and deploy the implementation with exactly this refresh-only state:

```text
PUBLICATION_CLAIMS_ENABLED=false
PUBLICATION_CONTINUOUS_CLAIMS_ENABLED=false
PUBLICATION_ACTIVATION_IDS absent
PUBLICATION_CLAIM_CONCURRENCY=1
PUBLICATION_PREACTIVATION_SMOKE_ENABLED=false
LATEST_REFRESH_ENABLED=true
```

The only valid claim states are refresh-only, single activation with exactly one canonical UUID,
and explicit continuous claims with no activation UUID. Any other combination must fail before a
database pool or provider client is opened. Keep one `opennosh-publication` replica. Do not increase
claim concurrency above one during this activation phase.

After the disabled deployment is live, require a fresh healthy worker instance with no restart or
startup-error event for five minutes. Run the ten-adapter zero-claim smoke with claims disabled,
then open a Render Shell for only `opennosh-publication` and run:

```bash
python deploy/render_runtime.py publication-readiness
```

This wrapper derives the least-privilege publication database URL without printing it and runs
`opennosh commons production-claims-readiness --json`. Save only the JSON report. A passing report
has `status=ready`, an empty `failures` array, `credentials_complete=true`, `picked=0`, a valid
`RENDER_GIT_COMMIT`, the pre-activation smoke switch off, one replica, claim concurrency one, and
the expected T33.5 and public-release pins. Queue and federation counts may be nonzero; they are
evidence, not identifiers. The report
must contain no URLs with credentials, environment dumps, tokens, private keys, emails, GitHub
logins, contribution content, or database row payloads.

The `readiness_sha256` is SHA-256 over UTF-8 canonical JSON with sorted keys and compact separators,
excluding only the `readiness_sha256` field itself. It binds the observation time, deployed commit,
queue state, federation-state counts, runtime mode, capacity, and activation candidate. Any change
requires a fresh report and a new approval. Post the redacted report and ask for this exact, separate
message:

```text
I approve T33.6 production-claims activation for readiness digest <sha256>, continuous mode, concurrency 1.
```

Approval of the issue, implementation PR, disabled deployment, smoke test, or a different readiness
digest is not activation approval. Do not change Render before the exact message exists.

After approval, change only `opennosh-publication` to claims enabled, continuous claims enabled,
activation IDs absent, concurrency one, and refresh enabled. Deploy only that worker. For 30 minutes:

1. Require one live instance, no restart loop, and no startup, signature, receipt, immutable-object,
   federation-scope, duplicate-effect, or pointer-activation error.
2. Sample queue counts at least every five minutes. Roll back if active publication wake-ups grow on
   three consecutive samples without matching newly accepted contributions.
3. Verify the public origin, release lineage, manifest digest, receipt digest, and pointer key remain
   the approved baseline or advance together through one receipt-gated publication. An older release
   or same release version with a different manifest is pointer regression.
4. If a natural eligible contribution exists, capture one redacted end-to-end proof. If none exists,
   record a healthy zero-claim canary; never create synthetic production data for this proof.

Rollback immediately on any stop condition. Set both claim switches false, remove activation IDs,
keep refresh enabled, and deploy only `opennosh-publication`. Do not edit or delete queue, publication,
governance, receipt, or federation rows. A successful rollback has a fresh live instance within five
minutes, zero picked publication wake-ups, healthy latest refresh, all seven T33.5 public checks at
HTTP 200, and unchanged last verified public identity.

## Pre-cutover verification

Using Render's temporary hostname, verify all of the following:

```text
GET /                         -> redirect to /en
GET /en                      -> 200 public Commons
GET /tracker                 -> 200 Tracker
GET /healthz                 -> 200 web-process liveness, independent of PostgreSQL
GET /api/v1/healthz          -> 200 and healthy database state
GET /en/explore              -> 200
GET /en/contribute           -> 200
```

Also complete the desktop and mobile browser smoke: public-to-Tracker full-page handoff, Tracker
return link, language selection, contribution local preservation, starter-food search, sign-up and
sign-in error handling, one-time recovery-code acknowledgement, optional guided setup, Account and
Records routes, and no critical console errors. Do not cut over DNS if any check fails.

## Cloudflare cutover

1. Add `opennosh.org` to the Render web service and note the exact DNS records Render displays.
2. In Cloudflare, remove or disable the apex/`www` redirect rule to GitHub.
3. Remove conflicting apex/`www` A, AAAA, or CNAME records. Do not touch MX, SPF, DKIM, DMARC, or
   Email Routing records.
4. Add Render's displayed apex and `www` records. Start in DNS-only mode if Render requests it for
   ownership and certificate verification; enable Cloudflare proxying only after Render reports the
   domain verified and the documented Cloudflare configuration is confirmed compatible.
5. Verify Render's TLS certificate, HTTP-to-HTTPS redirect, apex response, and `www` canonical
   redirect before declaring the cutover complete.

## Production verification

Run a single post-cutover verification pass:

```bash
curl -fsS https://opennosh.org/api/v1/healthz
curl -fsSI https://opennosh.org/
curl -fsSI https://www.opennosh.org/
```

Then repeat the browser smoke against `https://opennosh.org`, capture a screenshot, check console
errors, and confirm the response uses secure `__Host-opennosh-session` and
`__Host-opennosh-csrf` cookies after authentication begins.

## Rollback

If Render fails before DNS cutover, leave Cloudflare's GitHub redirect untouched and roll the web or
API service back to its last successful Render deploy. If the domain cutover fails, restore the
previous proxied apex/`www` records and redirect rule, then confirm all three public URL variants
again redirect to GitHub. Do not downgrade the database schema automatically; migrations must stay
backward compatible through the documented rolling-deploy window, and a forward fix requires its
own reviewed change.
