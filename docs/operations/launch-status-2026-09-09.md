# Living Commons launch status — 2026-09-09

This ledger separates merged implementation, deployed capability, and retained production proof.
An implementation issue being closed is not evidence that its production feature is enabled.

## Verified baseline

- Production began this review at application `0.99.0.0`, commit
  `bc631b3600010c89e729b0902682a7323da3ce6f` (PR #195).
- Commons has one verified 166-record release,
  `1.1787978335.2679268334.4110916185`, with manifest digest
  `e72956320d7b30cb88d65c79dd0a4810a94af70a1248986fca43a61e787b9395` and receipt digest
  `1f544742a128498359336953ed1bd53277e32898c84962738d0521ddcab19cfb`.
- The public pointer was available and unexpired during the investigation. A request at an activity
  bucket rollover reported stale proof; a later request reported quiet with the same verified release.
  The regression was the snapshot's bucket-equality freshness check, not a missing published release.
- Read-only production SQL showed a search snapshot created at `2026-09-09T19:40:02.140901Z`, the
  first slow rice request, with expiry at `20:00:02.140901Z`. No autonomous search refresher existed.
- API environment inspection confirmed evidence uploads, mission mutations, public missions, and
  public status disabled. The Blueprint retains the remaining controlled activation boundaries.
- Public package versions at audit start were PyPI `0.22.0.0` and npm `0.22.0`.

## Implementation reconciliation

| Train | Implementation evidence | Production checkpoint |
|---|---|---|
| T34.1 evidence intake | #135 closed, PR #136 | Hosted intake remains disabled |
| T34.2 evidence preservation | #137 closed, PR #138 | Evidence worker/provider activation remains outstanding |
| T34.3 stewardship | #139 closed, PR #140 | Named active independent steward and enabled workflow required |
| T34.4 natural proof tooling | #142 and #144 closed | Live natural contribution proof is explicitly separate and outstanding |
| T34.5 federation | #146, #147, #149 closed | Reviewed maintainers/packs and production activation required |
| T34.6 missions | PRs #155–#161 merged; #154 remains open | Real steward-owned mission, projection, readiness and activation evidence required |
| T34.7 developer kit | #162 closed | Publish current packages and retain install evidence |
| T34.8 reuse/impact/operations | #169 closed | Real reuse/monitor inputs and activation required |

The initial live readiness run reported `claim_credentials_incomplete` and
`living_commons_migration_not_current`. Investigation found two report defects: the disabled-worker
wrapper stripped configured signing credentials before validation, and the report pinned migration
0038 although production had advanced to 0039. The reliability release corrects both; activation
still requires a fresh report from the deployed commit and the natural-proof inputs below.

## Reliability release acceptance

The `0.99.1.0` reliability change fixes Commons freshness, enables default search snapshot warming,
and extends the existing worker canary into periodic outage/recovery monitoring. It does not enable
publication claims, evidence, governance, federation, missions, reuse, impact, or public-status flags.

- [ ] Protected PR merged and exact application commit deployed.
- [ ] Commons remains verified across a five-minute activity bucket boundary.
- [ ] Searches remain successful and bounded while a replacement snapshot is built.
- [ ] Worker logs show healthy startup and periodic checks.
- [ ] Current npm/PyPI packages published through release-confidence and trusted publishing.
- [x] Local `main` reconciled without losing divergent work. Previous HEAD
  `9241b1d1d4d6cb04019b4f5f88058311126fde74` is retained on
  `codex/preserved-local-main-20260909`; its complete history was also verified in a Git bundle.

## Remaining full-launch inputs

The natural-proof runbook requires a real contributor and a different active steward, trusted
production evidence storage/scanning, and one evidence worker. No real contribution or independent
person may be invented to satisfy that proof. A retained report must bind the natural browser
capture, approved review, exact-one publication, signed receipt, and public record, followed by
30 minutes of observation. See [the natural proof runbook](natural-publication-proof.md).

Missions need a real scoped mission and assigned steward; federation needs independently maintained
packs; reuse and impact need real participating projects. Public status needs fresh monitor inputs
and an incident operator. These are operational checkpoints on #134, not missing foundation code.
The original issue acceptance criteria remain in force until their evidence exists.
