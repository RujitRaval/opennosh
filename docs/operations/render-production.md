# Render production operations

## Topology and cost boundary

`render.yaml` is the production source of truth. It creates three Ohio services/resources:

- `opennosh-web`: Starter public Docker web service;
- `opennosh-api`: Starter private Docker service;
- `opennosh-db`: Basic-256mb PostgreSQL 16 with 5 GB storage and no public ingress.

T32 also attaches one 1 GB `opennosh-public-artifact-state` disk to `opennosh-api` for the
anti-rollback checkpoint and verified release cache. The approved launch budget is approximately
USD 20.25 per month before unusual bandwidth or build usage. Preview environments are disabled so
pull requests cannot create additional paid resources. Changing a plan, region, disk size, replica
count, preview policy, or database exposure requires a reviewed pull request and renewed cost
review. The disk intentionally trades zero-downtime API deploys and horizontal API scaling for the
smallest independent durable checkpoint in this bounded release.

## Provisioning

1. Merge the reviewed Blueprint change only after repository CI passes.
2. In Render, create a Blueprint from `https://github.com/RujitRaval/opennosh` and `render.yaml`.
3. Confirm exactly the three resources above, the `ohio` region, one instance per service, the approved
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
