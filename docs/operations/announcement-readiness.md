# Public beta release verification

Selected release: application/Python `0.103.1.0`, npm `0.103.1`.

The announcement scope is public food search and provenance, a private nutrition Tracker, source
and developer packages, and an owner-operated contribution/mission pilot. Independent stewardship,
uploaded evidence, federation, general missions, reuse/impact, hosted barcode lookup, and hosted
strength entry are outside this beta. No additional paid service is authorized by this release.

## Verification ledger

Checked September 22, 2026. Release publication and operational checks remain distinct gates;
a passing application suite does not prove production backup recovery.

| Check | Evidence / state |
|---|---|
| Accurate public scope and hosted data explanation | Implemented in this release candidate; owner-reviewed beta, disabled integrations, recovery and deletion limits stated |
| First impressions | Hero count fixed at 320/375/768 px; mobile Tracker header corrected; search copy, contribution review, developer entry and hosting links updated; 32 pinned visual tests pass |
| Clean installation | Fresh clone plus candidate patch, default `.env.example`, isolated Compose volume: build, capacity preflight, migrations and health pass; all 168 records across six packs load with zero rejected entries; Thepla search survives API restart |
| Developer packages | Local wheel/npm build and clean-installed hosted-shape/self-hosted starter checks pass; registry publication for 0.103.1.0 / 0.103.1 is pending |
| Live desktop/mobile account lifecycle | Passed on production 0.103.0.2 using one disposable account; details below. Repeat changed UI checks after deployment |
| Current production backup and isolated restore | Not yet verified; requires the owner's signed-in Render session. This remains an announcement blocker |
| Alert delivery | Existing production outage and recovery messages confirmed in the operator receiver on September 14 (17:23 and 17:25 UTC); a fresh delivery test is pending Render access |
| Support ownership | Rujit Raval; support@opennosh.org; weekly food-suggestion review when capacity allows, best-effort support. Existing forwarding configuration is documented in domain operations; fresh mailbox receipt is not claimed |
| Independent human installation | Not performed; automated fresh-install verification is not represented as an independent human trial |

### Live account journey

Registration created a new synthetic account and displayed a one-time recovery code. The code was
saved privately, US display units selected, and optional targets skipped. Logging 100 g of the USDA
Rice cake record produced one entry with 392 kcal, 7.1 g protein, 81.1 g carbohydrate, and 4.3 g fat.
The same totals were visible on desktop and a 375 px mobile viewport. Sign-out and sign-in retained
the entry. JSON export returned HTTP 200, `Cache-Control: no-store`, an attachment filename, and the
saved food. Recovery accepted the original code and new password, issued a replacement code, and
retained the log. Account deletion then returned the signed-out view, export returned HTTP 401,
and sign-in with the deleted credentials was rejected. The synthetic account is deleted.

### Local validation

`make test`: 2,202 pytest passes (199 database-dependent skips), 124 unittest passes, 341 web unit
passes, and documentation/contracts checks. The browser suite additionally covers hero and Tracker
header geometry: 93 pass, three intentional project-specific skips. The production build and
`make lint typecheck package-check` pass. The pinned visual suite passes all 32
tests without updating snapshots. CI must supply the real-database lanes before merge.

## Previous baseline

The September 22 assessment verified production `0.103.0.2`, clean main, no open PRs, healthy public
search and Commons, 168 signed community records, and 13,497 separately identified USDA references.
All nine PR quality jobs and the September 20 scheduled drills passed. The
[T34 issue](https://github.com/RujitRaval/opennosh/issues/134) contains newer owner-publication,
mission, search acceptance, and package evidence than the historical September 9 launch ledger.

## Operating procedure

Rujit Raval owns support, weekly food-suggestion review, incident response, and restore decisions.
Use `support@opennosh.org` for ordinary reports and GitHub private vulnerability reporting for
security issues. The beta makes no guaranteed support or uptime commitment.

For an incident, check database health, public search readiness, Commons proof freshness, and the
exact deployed commit. Inspect the existing worker's canary/monitor logs and its configured alert
receiver. Restore service before closing an incident and retain redacted evidence of recovery.

Never restore a backup over the production database for a drill. Download an on-demand Render
export into an owner-controlled private directory and restore to an isolated local PostgreSQL 16
instance with no application, publication worker, webhook, or outbound integration attached.
Validate schema revision, constraints/indexes, representative table counts, and receipt integrity
without printing personal records. Stop the isolated instance afterwards and protect the retained
backup as private account data. Record export time, restore time, result, and retention here.

A real recovery must also reconcile account deletions after the backup timestamp before enabling
access. Deployments follow the exact protected merge attestation and automatic Render deployment;
never bypass pending checks with a manual deployment.
