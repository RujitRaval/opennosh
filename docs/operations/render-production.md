# Render production operations

## Topology and cost boundary

`render.yaml` is the production source of truth. It creates three Ohio resources:

- `opennosh-web`: Starter public Docker web service;
- `opennosh-api`: Starter private Docker service;
- `opennosh-db`: Basic-256mb PostgreSQL 16 with 5 GB storage and no public ingress.

The approved launch budget is approximately USD 20 per month before unusual bandwidth or build
usage. Preview environments are disabled so pull requests cannot create additional paid resources.
Changing a plan, region, disk size, replica count, preview policy, or database exposure requires a
reviewed pull request and renewed cost review.

## Provisioning

1. Merge the reviewed Blueprint change only after repository CI passes.
2. In Render, create a Blueprint from `https://github.com/RujitRaval/opennosh` and `render.yaml`.
3. Confirm exactly the three resources above, the `ohio` region, one instance per service, and the
   approved plans before accepting the charge.
4. Let Render generate the database-role passwords, proxy token, and cursor-signing secret. Never
   copy their values into Git, chat, shell history, or this runbook.
5. Wait for `opennosh-api` pre-deploy to create the `opennosh_migration` and `opennosh_web` roles,
   verify the 100-connection ceiling, run Alembic once, and refresh runtime grants.
6. Verify `opennosh-web` through its temporary `onrender.com` hostname before changing Cloudflare.

The owner database URL exists only in the Render wrapper environment. Before the API starts, the
wrapper derives the bounded web-role URL and removes the owner URL, migration password, and raw
generated secrets from the application process environment. The API service is private; browsers
reach it only through the authenticated Next.js proxy.

The initial hosted release intentionally leaves the signed Commons artifact paths and T1 public
artifact origin unset. Public snapshot consumers therefore use their explicit unavailable/quiet
states, while food pages stay on the PostgreSQL read path until an offline-signed release and
durable HTTPS artifact origin are provisioned. `PUBLIC_ARTIFACT_READS_ENABLED=false` is the explicit
web dark-launch default. Never enable a filesystem path or a development verification key in
production.

### T1 artifact read-plane activation

Provision an independently durable object-store/CDN origin before enabling the web switch. Upload
content-addressed record JSON, provenance HTML, and pack bytes first; then the signed release
manifest and signed publication receipt; replace `latest/v1.json` last. Configure the private API
with `PUBLIC_ARTIFACT_BASE_URL`, a durable writable `PUBLIC_ARTIFACT_CHECKPOINT_PATH`, approved
`PUBLIC_COMMONS_VERIFYING_KEYS`, and approved `PUBLICATION_RECEIPT_VERIFYING_KEYS`. The API process
receives public verification keys only.

Verify the latest and one pinned release while PostgreSQL access is paused:

```text
GET /api/v1/public/foods/community/{source_id}
GET /api/v1/public/releases/{release}/foods/community/{source_id}
GET /api/v1/public/releases/{release}/foods/community/{source_id}/provenance
GET /api/v1/public/releases/{release}/manifest
GET /api/v1/public/releases/{release}/packs/{pack_id}/{pack_version}/download
```

Each exact-version response must be `200`, immutable, and carry the expected release header. Tamper
with a disposable object in staging and confirm the read fails closed. Make the latest pointer
unavailable and confirm only the checkpointed verified release is returned with
`x-opennosh-release-state: stale`, `x-opennosh-stale-age`, and HTTP Warning 110. Only then set
`PUBLIC_ARTIFACT_READS_ENABLED=true` on `opennosh-web`. Roll back by setting that flag to `false`;
do not delete or rewrite immutable artifacts.

## Pre-cutover verification

Using Render's temporary hostname, verify all of the following:

```text
GET /                         -> redirect to /en
GET /en                      -> 200 public Commons
GET /tracker                 -> 200 Tracker
GET /api/v1/healthz          -> 200 and healthy database state
GET /en/explore              -> 200
GET /en/contribute           -> 200
```

Also complete the desktop and mobile browser smoke: public-to-Tracker full-page handoff, Tracker
return link, language selection, contribution local preservation, sign-up/sign-in error handling,
and no critical console errors. Do not cut over DNS if any check fails.

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
