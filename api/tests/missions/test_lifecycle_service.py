from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from opennosh_api.missions.contracts import (
    MissionDefinitionSpec,
    MissionGapKind,
    MissionLifecycleAction,
    MissionLifecycleState,
)
from opennosh_api.missions.models import MissionDefinition, MissionLifecycleEvent
from opennosh_api.missions.policy import MissionLifecycleError, state_after
from opennosh_api.missions.service import (
    ProposeMission,
    TransitionMission,
    lifecycle_state,
    propose_mission,
    transition_mission,
)

NOW = datetime(2026, 9, 2, 14, tzinfo=UTC)
MISSION_ID = UUID("10000000-0000-4000-8000-000000000001")
DEFINITION_ID = UUID("20000000-0000-4000-8000-000000000001")
PROPOSER_ID = UUID("30000000-0000-4000-8000-000000000001")
STEWARD_ID = UUID("40000000-0000-4000-8000-000000000001")


class FakeMissionStore:
    def __init__(self) -> None:
        self.definitions: dict[UUID, MissionDefinition] = {}
        self.events: dict[UUID, MissionLifecycleEvent] = {}
        self.stewards = {PROPOSER_ID, STEWARD_ID}
        self.checkpoint: SimpleNamespace | None = None
        self.progress_current = True
        self.receipts: dict[str, SimpleNamespace] = {}
        self.locks: list[UUID] = []
        self.flushes = 0

    async def lock_mission(self, mission_id: UUID) -> None:
        self.locks.append(mission_id)

    async def definition(self, definition_id: UUID) -> MissionDefinition | None:
        return self.definitions.get(definition_id)

    async def latest_definition(self, mission_id: UUID) -> MissionDefinition | None:
        matches = [item for item in self.definitions.values() if item.mission_id == mission_id]
        return max(matches, key=lambda item: item.definition_version, default=None)

    async def lifecycle_event(self, event_id: UUID) -> MissionLifecycleEvent | None:
        return self.events.get(event_id)

    async def latest_lifecycle_event(self, mission_id: UUID) -> MissionLifecycleEvent | None:
        matches = [item for item in self.events.values() if item.mission_id == mission_id]
        return max(matches, key=lambda item: item.sequence, default=None)

    async def active_progress(self, definition_id: UUID):
        return self.checkpoint

    async def progress_is_current(self, checkpoint: SimpleNamespace) -> bool:
        return self.progress_current

    async def receipt(self, digest: str):
        return self.receipts.get(digest)

    async def actor_is_active_human_steward(
        self, *, actor_id: UUID, pack_id: str, at: datetime
    ) -> bool:
        return actor_id in self.stewards and pack_id == "opennosh-starter" and at >= NOW

    def add_definition(self, definition: MissionDefinition) -> None:
        self.definitions[definition.id] = definition

    def add_lifecycle_event(self, event: MissionLifecycleEvent) -> None:
        self.events[event.id] = event

    async def flush(self) -> None:
        self.flushes += 1


def _spec(*, target: int = 10) -> MissionDefinitionSpec:
    return MissionDefinitionSpec(
        gap_kind=MissionGapKind.MISSING_FIELD,
        title="Complete sodium values",
        summary="Fill verified sodium gaps without ranking contributors.",
        target_pack_id="opennosh-starter",
        target_dataset="foods",
        acceptance_target=target,
        acceptance_criteria="Count distinct active records with a verified sodium value.",
    )


def _proposal(*, event_id: UUID | None = None, reason: str = "Open a measurable gap."):
    return ProposeMission(
        mission_id=MISSION_ID,
        definition_id=DEFINITION_ID,
        event_id=event_id or uuid4(),
        actor_id=PROPOSER_ID,
        responsible_steward_actor_id=STEWARD_ID,
        definition=_spec(),
        public_reason=reason,
    )


def _transition(
    prior: MissionLifecycleEvent,
    action: MissionLifecycleAction,
    *,
    actor_id: UUID = STEWARD_ID,
    event_id: UUID | None = None,
    next_review_at: datetime | None = None,
    receipt: str | None = None,
) -> TransitionMission:
    return TransitionMission(
        mission_id=MISSION_ID,
        definition_id=DEFINITION_ID,
        event_id=event_id or uuid4(),
        expected_prior_event_id=prior.id,
        actor_id=actor_id,
        action=action,
        public_reason=f"Apply {action.value} with a public explanation.",
        next_review_at=next_review_at,
        release_receipt_digest=receipt,
    )


async def _proposed(store: FakeMissionStore) -> MissionLifecycleEvent:
    _definition, event = await propose_mission(store, _proposal(), now=NOW)
    return event


async def _active(store: FakeMissionStore) -> MissionLifecycleEvent:
    proposed = await _proposed(store)
    return await transition_mission(
        store,
        _transition(proposed, MissionLifecycleAction.APPROVE),
        now=NOW + timedelta(minutes=1),
    )


@pytest.mark.asyncio
async def test_proposal_persists_immutable_definition_and_event() -> None:
    store = FakeMissionStore()
    command = _proposal()

    definition, event = await propose_mission(store, command, now=NOW)

    assert definition.definition_json == _spec().model_dump(mode="json")
    assert definition.responsible_steward_actor_id == STEWARD_ID
    assert event.sequence == 1
    assert lifecycle_state(event) is MissionLifecycleState.PROPOSED
    assert store.locks == [MISSION_ID]
    assert store.flushes == 1


@pytest.mark.asyncio
async def test_proposal_replay_is_idempotent_but_conflicting_key_fails() -> None:
    store = FakeMissionStore()
    command = _proposal()
    first = await propose_mission(store, command, now=NOW)

    replay = await propose_mission(store, command, now=NOW + timedelta(hours=1))

    assert replay == first
    assert store.flushes == 1
    with pytest.raises(MissionLifecycleError, match="mission_idempotency_conflict"):
        await propose_mission(
            store,
            _proposal(event_id=command.event_id, reason="A conflicting reuse."),
            now=NOW + timedelta(hours=2),
        )

    with pytest.raises(MissionLifecycleError, match="mission_already_exists"):
        await propose_mission(store, _proposal(), now=NOW + timedelta(hours=3))


@pytest.mark.asyncio
async def test_proposal_requires_active_human_actor_and_responsible_steward() -> None:
    store = FakeMissionStore()
    store.stewards.remove(STEWARD_ID)

    with pytest.raises(MissionLifecycleError, match="responsible_steward_not_active"):
        await propose_mission(store, _proposal(), now=NOW)

    store.stewards.add(STEWARD_ID)
    store.stewards.remove(PROPOSER_ID)
    with pytest.raises(MissionLifecycleError, match="mission_actor_not_active_steward"):
        await propose_mission(store, _proposal(), now=NOW)


@pytest.mark.asyncio
async def test_approval_is_moderated_and_optimistically_concurrent() -> None:
    store = FakeMissionStore()
    proposed = await _proposed(store)

    with pytest.raises(MissionLifecycleError, match="mission_self_approval_prohibited"):
        await transition_mission(
            store,
            _transition(proposed, MissionLifecycleAction.APPROVE, actor_id=PROPOSER_ID),
            now=NOW + timedelta(minutes=1),
        )

    stale = _transition(proposed, MissionLifecycleAction.APPROVE)
    approved = await transition_mission(store, stale, now=NOW + timedelta(minutes=1))
    assert lifecycle_state(approved) is MissionLifecycleState.ACTIVE
    with pytest.raises(MissionLifecycleError, match="mission_revision_conflict"):
        await transition_mission(
            store,
            _transition(proposed, MissionLifecycleAction.CLOSE),
            now=NOW + timedelta(minutes=2),
        )


@pytest.mark.asyncio
async def test_transition_replay_is_idempotent_after_role_revocation() -> None:
    store = FakeMissionStore()
    proposed = await _proposed(store)
    command = _transition(proposed, MissionLifecycleAction.APPROVE)
    first = await transition_mission(store, command, now=NOW + timedelta(minutes=1))
    store.stewards.remove(STEWARD_ID)

    replay = await transition_mission(store, command, now=NOW + timedelta(days=1))

    assert replay is first
    assert store.flushes == 2

    with pytest.raises(MissionLifecycleError, match="mission_idempotency_conflict"):
        await transition_mission(
            store,
            TransitionMission(
                mission_id=command.mission_id,
                definition_id=command.definition_id,
                event_id=command.event_id,
                expected_prior_event_id=command.expected_prior_event_id,
                actor_id=command.actor_id,
                action=command.action,
                public_reason="Conflicting idempotent replay.",
            ),
            now=NOW + timedelta(days=1),
        )


@pytest.mark.asyncio
async def test_pause_replay_remains_idempotent_after_review_deadline() -> None:
    store = FakeMissionStore()
    active = await _active(store)
    command = _transition(
        active,
        MissionLifecycleAction.PAUSE,
        next_review_at=NOW + timedelta(days=1),
    )
    first = await transition_mission(store, command, now=NOW + timedelta(minutes=2))

    replay = await transition_mission(store, command, now=NOW + timedelta(days=2))

    assert replay is first


@pytest.mark.asyncio
async def test_transition_rejects_backdated_chronology() -> None:
    store = FakeMissionStore()
    proposed = await _proposed(store)

    with pytest.raises(MissionLifecycleError, match="mission_event_time_invalid"):
        await transition_mission(
            store,
            _transition(proposed, MissionLifecycleAction.APPROVE),
            now=NOW - timedelta(seconds=1),
        )


@pytest.mark.asyncio
async def test_pause_requires_a_future_review_and_resume_returns_active() -> None:
    store = FakeMissionStore()
    active = await _active(store)
    review_at = NOW + timedelta(days=7)
    paused = await transition_mission(
        store,
        _transition(
            active,
            MissionLifecycleAction.PAUSE,
            next_review_at=review_at,
        ),
        now=NOW + timedelta(minutes=2),
    )
    assert lifecycle_state(paused) is MissionLifecycleState.PAUSED
    assert paused.next_review_at == review_at

    resumed = await transition_mission(
        store,
        _transition(paused, MissionLifecycleAction.RESUME),
        now=NOW + timedelta(minutes=3),
    )
    assert lifecycle_state(resumed) is MissionLifecycleState.ACTIVE


@pytest.mark.asyncio
async def test_transition_fails_closed_on_missing_scope_state_and_authority() -> None:
    missing = FakeMissionStore()
    absent_prior = MissionLifecycleEvent(
        id=uuid4(),
        mission_id=MISSION_ID,
        definition_id=DEFINITION_ID,
        sequence=1,
        prior_event_id=None,
        action=MissionLifecycleAction.PROPOSE.value,
        actor_id=PROPOSER_ID,
        public_reason="Proposed elsewhere.",
        occurred_at=NOW,
    )
    with pytest.raises(MissionLifecycleError, match="mission_definition_not_found"):
        await transition_mission(
            missing,
            _transition(absent_prior, MissionLifecycleAction.APPROVE),
            now=NOW + timedelta(minutes=1),
        )

    store = FakeMissionStore()
    proposed = await _proposed(store)
    store.events.clear()
    with pytest.raises(MissionLifecycleError, match="mission_not_found"):
        await transition_mission(
            store,
            _transition(proposed, MissionLifecycleAction.APPROVE),
            now=NOW + timedelta(minutes=1),
        )

    store.events[proposed.id] = proposed
    store.stewards.remove(STEWARD_ID)
    with pytest.raises(MissionLifecycleError, match="mission_actor_not_active_steward"):
        await transition_mission(
            store,
            _transition(proposed, MissionLifecycleAction.APPROVE),
            now=NOW + timedelta(minutes=1),
        )


@pytest.mark.asyncio
async def test_pause_rejects_a_review_time_that_is_not_future() -> None:
    store = FakeMissionStore()
    active = await _active(store)

    with pytest.raises(MissionLifecycleError, match="mission_next_review_not_future"):
        await transition_mission(
            store,
            _transition(
                active,
                MissionLifecycleAction.PAUSE,
                next_review_at=NOW + timedelta(minutes=2),
            ),
            now=NOW + timedelta(minutes=2),
        )


@pytest.mark.asyncio
async def test_completion_requires_available_progress_at_the_target() -> None:
    store = FakeMissionStore()
    active = await _active(store)
    command = _transition(active, MissionLifecycleAction.COMPLETE)

    with pytest.raises(MissionLifecycleError, match="mission_progress_unavailable"):
        await transition_mission(store, command, now=NOW + timedelta(minutes=2))

    store.checkpoint = SimpleNamespace(accepted_count=9)
    with pytest.raises(MissionLifecycleError, match="mission_acceptance_target_not_met"):
        await transition_mission(store, command, now=NOW + timedelta(minutes=2))

    store.checkpoint = SimpleNamespace(accepted_count=10)
    store.progress_current = False
    with pytest.raises(MissionLifecycleError, match="mission_progress_stale"):
        await transition_mission(store, command, now=NOW + timedelta(minutes=2))

    store.progress_current = True
    completed = await transition_mission(store, command, now=NOW + timedelta(minutes=2))
    assert lifecycle_state(completed) is MissionLifecycleState.COMPLETED


@pytest.mark.asyncio
async def test_release_requires_a_later_valid_receipt_for_the_target_pack() -> None:
    store = FakeMissionStore()
    active = await _active(store)
    store.checkpoint = SimpleNamespace(accepted_count=10)
    completed = await transition_mission(
        store,
        _transition(active, MissionLifecycleAction.COMPLETE),
        now=NOW + timedelta(minutes=2),
    )
    digest = "a" * 64
    release = _transition(completed, MissionLifecycleAction.RELEASE, receipt=digest)

    with pytest.raises(MissionLifecycleError, match="mission_release_receipt_not_found"):
        await transition_mission(store, release, now=NOW + timedelta(minutes=3))

    store.receipts[digest] = SimpleNamespace(
        pack_id="another-pack",
        event_type="publication",
        published_at=NOW + timedelta(minutes=3),
        reconciled_at=NOW + timedelta(minutes=3),
    )
    with pytest.raises(MissionLifecycleError, match="mission_release_receipt_invalid"):
        await transition_mission(store, release, now=NOW + timedelta(minutes=3))

    store.receipts[digest].pack_id = "opennosh-starter"
    released = await transition_mission(store, release, now=NOW + timedelta(minutes=3))
    assert lifecycle_state(released) is MissionLifecycleState.RELEASED


def test_lifecycle_transition_matrix_rejects_state_skips() -> None:
    assert (
        state_after(MissionLifecycleState.PAUSED, MissionLifecycleAction.RESUME)
        is MissionLifecycleState.ACTIVE
    )
    with pytest.raises(MissionLifecycleError, match="mission_transition_not_allowed"):
        state_after(MissionLifecycleState.PROPOSED, MissionLifecycleAction.RELEASE)


def test_transition_command_requires_action_specific_evidence() -> None:
    with pytest.raises(ValueError, match="Only a pause requires"):
        TransitionMission(
            mission_id=MISSION_ID,
            definition_id=DEFINITION_ID,
            event_id=uuid4(),
            expected_prior_event_id=uuid4(),
            actor_id=STEWARD_ID,
            action=MissionLifecycleAction.PAUSE,
            public_reason="Pause with no hidden reason.",
        )
    with pytest.raises(ValueError, match="Only release requires"):
        TransitionMission(
            mission_id=MISSION_ID,
            definition_id=DEFINITION_ID,
            event_id=uuid4(),
            expected_prior_event_id=uuid4(),
            actor_id=STEWARD_ID,
            action=MissionLifecycleAction.RELEASE,
            public_reason="Release only with durable proof.",
        )


def test_commands_reject_invalid_action_digest_reason_and_time_shapes() -> None:
    with pytest.raises(ValueError, match="bounded public reason"):
        _proposal(reason="   ")
    with pytest.raises(ValueError, match="Mission proposals use"):
        TransitionMission(
            mission_id=MISSION_ID,
            definition_id=DEFINITION_ID,
            event_id=uuid4(),
            expected_prior_event_id=uuid4(),
            actor_id=STEWARD_ID,
            action=MissionLifecycleAction.PROPOSE,
            public_reason="Use the dedicated proposal command.",
        )
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        TransitionMission(
            mission_id=MISSION_ID,
            definition_id=DEFINITION_ID,
            event_id=uuid4(),
            expected_prior_event_id=uuid4(),
            actor_id=STEWARD_ID,
            action=MissionLifecycleAction.RELEASE,
            public_reason="Reject malformed receipt proof.",
            release_receipt_digest="NOT-A-DIGEST",
        )
    with pytest.raises(ValueError, match="include a timezone"):
        TransitionMission(
            mission_id=MISSION_ID,
            definition_id=DEFINITION_ID,
            event_id=uuid4(),
            expected_prior_event_id=uuid4(),
            actor_id=STEWARD_ID,
            action=MissionLifecycleAction.PAUSE,
            public_reason="Review at an unambiguous instant.",
            next_review_at=datetime(2026, 9, 3),
        )


@pytest.mark.asyncio
async def test_commands_reject_naive_operation_times() -> None:
    store = FakeMissionStore()
    with pytest.raises(ValueError, match="include a timezone"):
        await propose_mission(store, _proposal(), now=datetime(2026, 9, 2))
