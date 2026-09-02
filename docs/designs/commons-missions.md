# Commons missions and verified accepted activity

Status: T34.6 governed lifecycle and durable projection; code lands disabled by default.

## Product boundary

A mission is a moderated, measurable Commons gap. It helps contributors coordinate work; it does
not create a second food database, publication workflow, or proof of acceptance. Submission,
steward approval, mission progress, mission completion, and signed release are separate states.
Only an existing canonical `accepted_events` fact may advance projected progress.

Mission gaps may describe a cuisine, locale, institution, dataset, or missing field. Every immutable
definition names a target pack and dataset, a public acceptance rule and bounded numeric target, and
one responsible scoped steward. Later definitions link to exactly one prior definition rather than
rewriting history.

## Durable model

| Fact or projection | Mutability | Purpose |
|---|---|---|
| `mission_definitions` | Append-only | Versioned gap, target, criteria, and responsible steward |
| `mission_lifecycle_events` | Append-only | Proposal, approval, pause, resume, completion, release, and close chronology |
| `mission_contribution_bindings` | Append-only | Exact draft version that explicitly joined one mission definition |
| `mission_progress_checkpoints` | Append-only | Deterministic accepted-event-set digest and counts |
| `mission_progress_records` | Append-only | Active canonical records in one complete checkpoint |
| `mission_progress_activations` | Mutable pointer | One atomic active checkpoint per definition |

Database triggers reject updates and deletes to every fact table. Projection activation is the only
mutable pointer and must move last, in the transaction that writes a complete checkpoint.

## Governed lifecycle

Mission proposals and transitions serialize on a per-mission PostgreSQL advisory lock. Every new
decision requires an active human steward in the target pack and an exact expected prior event;
reusing an event ID with the same request is idempotent, while conflicting reuse or stale state
fails closed. Proposal approval must come from a different steward.

The bounded transition graph is `proposed -> active -> paused -> active`, `active -> completed ->
released`, with explicit closure from any non-closed state. Pauses require a future review time.
Completion rebuilds progress from the current canonical accepted-event and receipt rows, checks the
active checkpoint digest, counts, and complete materialized record set, and then requires the
immutable definition target. A stale checkpoint therefore cannot preserve completion after a late
correction or revocation. Release additionally requires a later, reconciled, non-revocation
publication receipt for the target pack, so lifecycle state cannot impersonate signed publication.

## Accepted-event projector

The projector accepts exact contribution bindings and canonical accepted-event facts. A directly
bound publication establishes mission membership. A correction or revocation inherits membership
only through its exact prior receipt digest. It must preserve repository, pack, and record identity,
and cannot precede the prior event. A correction replaces the active record proof; a revocation
removes the record from the active count without erasing either event.

Input order cannot change output. Identical event retries collapse, conflicting event IDs or receipt
digests fail closed, cross-definition bindings fail closed, and a missing or conflicting receipt
lineage fails closed. The checkpoint digest covers every matched event, including corrections and
revocations, while `accepted_count` covers only the current active records.

A contributor may bind only their own exact current draft version, only while the current mission
definition is active, and only when the draft targets the definition's pack. The draft-version lock
prevents one contribution version from joining competing definitions concurrently. Projection
rebuilds verify the accepted-event and receipt representations, reconciliation time, canonical
repository and commit, definition pack, and complete ancestor/descendant receipt lineage before
projecting. The repository resolves that lineage recursively from definition-bound draft versions;
it never loads the global accepted-event ledger into memory.

Each rebuild reuses an identical verified checkpoint or appends a complete new checkpoint and its
records. It then compares the caller's expected active checkpoint and moves the single mutable
activation pointer last. Checkpoint and activation identifiers are idempotency boundaries; a reused
identifier with different material fails closed. A correction replaces the active record and a
revocation removes it while every prior checkpoint and accepted event remains immutable.

## Public and privacy boundary

`GET /api/v1/public/missions` exposes a bounded catalog only when `MISSION_PUBLIC_ENABLED=true`.
It selects the latest immutable definition for each mission, hides unmoderated proposals, derives
lifecycle state from the append-only event chain, and reads counts only from the active verified
checkpoint. Missing or cross-definition lifecycle proof fails the whole catalog closed. A missing
checkpoint is explicitly unavailable rather than zero; an outdated checkpoint is stale. Released
missions include the exact validated publication-receipt digest that authorized the transition.

The response excludes contributor identity, location, rankings, leaderboards, streaks, and health
comparisons. Geographic aggregation remains a later independent surface: it must be country-level
or broader and require at least ten underlying verified accepted events, with smaller cohorts hidden
or rolled upward. The system does not collect contributor geolocation for missions.

## Feature switches

- `MISSION_MUTATIONS_ENABLED`
- `MISSION_PROJECTION_ENABLED`
- `MISSION_PUBLIC_ENABLED`
- `MISSION_ACTIVITY_MAP_ENABLED`
- `MISSION_PACK_RELEASE_ENABLED`

All five default to `false`, are explicitly false for the Render API and publication worker, and are
included in production readiness. Activity maps require both public missions and projection. Mission
pack release requires mutations and projection. Activation is a later digest-bound operator action,
not an implication of implementation or merge approval.

## Rollback

Disable the five mission switches. Do not delete or rewrite mission definitions, lifecycle events,
contribution bindings, accepted events, checkpoints, signed releases, or receipts. Existing signed
public records and the last verified release remain readable. The migration refuses downgrade once
mission facts exist.
