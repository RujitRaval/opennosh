# Governed forge operations

T2 makes a scoped steward approval the only routine human authorization for a pack change. The
approval transaction records the exact files and digest, expected base commit, required checks,
steward identity, and forge target before it wakes publication. A contributor cannot approve their
own draft. Recusal, steward revocation, and the audited emergency pause are checked at approval and
again against the merge time.

The publication worker uses a forge GitHub App with only metadata read, checks read, contents write,
and pull-request write permissions. It cannot administer the repository, push to `main`, bypass rules,
or merge with failing checks. It creates a deterministic contribution branch and opens a pull
request. After every independent CI check passes, the worker reloads steward authority and arms
protected squash auto-merge while the separately sourced governance attestation is still missing and
therefore blocking. A retry observes GitHub's current auto-merge request, so a lost success response
cannot cause a duplicate arm. The worker then runs one short per-pack database transaction: it
serializes with withdrawal writes, reloads authority, and commits an immutable merge-authorization
record bound to the decision, payload, and exact pull-request head. That commit is the
merge-authorization linearization point. The database connection is released before the separate
attester App emits the final check, and GitHub completes the already-armed merge. A crash or lost
response before the check stays safely retryable without reopening the committed decision.
The worker accepts the commit only after the changed paths and contents reproduce the approved digest.

The seven governance trust checks are enforcement invariants, not invented GitHub check-run names.
Schema, provenance, license, and evidence run under `API checks`. Authorization, self-review, and the
exact open-head payload are rechecked by the worker, then emitted as the source-pinned governance
attestation check by a second GitHub App that has checks write but no contents, pull-request, or
administration permission. The worker reloads PostgreSQL authority again immediately before emitting
that final check through the committed authorization record, and recomputes payload identity from
the merged tree afterward. No PostgreSQL connection is held across a forge or attester request. The
outbox contains the exact
six protected GitHub status-check names declared in the versioned policy. Every ruleset entry pins
its expected source so another writer cannot satisfy protection with a look-alike check name.

`config/forge-policy.v1.json` is the desired production ruleset. CI validates it, but a feature PR
does not mutate live repository administration. After this change lands on `main`, an administrator
must create both GitHub Apps, install their disjoint bounded permissions, replace
`$OPENNOSH_GOVERNANCE_ATTESTER_APP_ID` with the attester App's numeric integration ID, apply the
manifest as the `main` branch ruleset, and verify the live ruleset has no bypass actors. Never give
the forge App checks-write permission or the attester App contents/pull-request permission.
Both installation-token providers mint repository-scoped tokens containing only the numeric
repository ID for `RujitRaval/opennosh`; approval, binding, and adapter boundaries reject every
other forge target.
Publication replicas remain zero until those credentials, T3 evidence durability, and T5 signed
receipts are ready together.

The forge and attester installation tokens are production trust roots. A leaked token is a credential
incident, not an alternate governance path: pause publication, rotate the installation, and close any
unexpected contribution pull requests. Governance withdrawal before the authorization transaction
commits prevents the authorization record from being written. Once merge authorization commits, a later role
change or pause applies to future decisions, but an intervention cannot retroactively cancel the
already-committed merge. The worker may safely retry a lost attestation response until GitHub
observes the protected check. A later correction uses a new governed contribution rather than
pretending the committed decision can be cancelled across systems.

Emergency intervention is an authorized, auditable database command, never a manual merge bypass.
A scoped steward may pause publication with actor, time, and reason; a different active steward must
resume it. A non-recused steward may also reject or request changes, which writes an immutable
intervention, blocks the publication intent, and returns the contribution to `changes_requested`.
The current or historical pause interval is evaluated at authorization time, so later role changes
do not rewrite committed history. Decisions, recusals, interventions, and merge authorizations are
append-only at the database boundary. Role and pause rows permit only their complete audited
one-way revoke and resume transitions; governance audit rows cannot be deleted.
