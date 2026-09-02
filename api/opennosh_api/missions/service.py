from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from opennosh_api.missions.contracts import (
    MissionDefinitionSpec,
    MissionLifecycleAction,
    MissionLifecycleState,
)
from opennosh_api.missions.models import (
    MissionDefinition,
    MissionLifecycleEvent,
    MissionProgressCheckpoint,
)
from opennosh_api.missions.policy import MissionLifecycleError, state_after
from opennosh_api.publication.models import PublicationReceiptRecord


@dataclass(frozen=True, slots=True)
class ProposeMission:
    mission_id: UUID
    definition_id: UUID
    event_id: UUID
    actor_id: UUID
    responsible_steward_actor_id: UUID
    definition: MissionDefinitionSpec
    public_reason: str

    def __post_init__(self) -> None:
        _bounded_reason(self.public_reason)


@dataclass(frozen=True, slots=True)
class TransitionMission:
    mission_id: UUID
    definition_id: UUID
    event_id: UUID
    expected_prior_event_id: UUID
    actor_id: UUID
    action: MissionLifecycleAction
    public_reason: str
    next_review_at: datetime | None = None
    release_receipt_digest: str | None = None

    def __post_init__(self) -> None:
        _bounded_reason(self.public_reason)
        if self.action is MissionLifecycleAction.PROPOSE:
            raise ValueError("Mission proposals use ProposeMission")
        if (self.action is MissionLifecycleAction.PAUSE) != (self.next_review_at is not None):
            raise ValueError("Only a pause requires a next review time")
        if self.next_review_at is not None:
            _require_aware(self.next_review_at)
        if (self.action is MissionLifecycleAction.RELEASE) != (
            self.release_receipt_digest is not None
        ):
            raise ValueError("Only release requires a receipt digest")
        if self.release_receipt_digest is not None and (
            len(self.release_receipt_digest) != 64
            or any(character not in "0123456789abcdef" for character in self.release_receipt_digest)
        ):
            raise ValueError("Mission release receipt must be a lowercase SHA-256 digest")


class MissionStore(Protocol):
    async def lock_mission(self, mission_id: UUID) -> None: ...

    async def definition(self, definition_id: UUID) -> MissionDefinition | None: ...

    async def latest_definition(self, mission_id: UUID) -> MissionDefinition | None: ...

    async def lifecycle_event(self, event_id: UUID) -> MissionLifecycleEvent | None: ...

    async def latest_lifecycle_event(self, mission_id: UUID) -> MissionLifecycleEvent | None: ...

    async def active_progress(
        self, definition_id: UUID
    ) -> MissionProgressCheckpoint | None: ...

    async def progress_is_current(self, checkpoint: MissionProgressCheckpoint) -> bool: ...

    async def receipt(self, digest: str) -> PublicationReceiptRecord | None: ...

    async def actor_is_active_human_steward(
        self, *, actor_id: UUID, pack_id: str, at: datetime
    ) -> bool: ...

    def add_definition(self, definition: MissionDefinition) -> None: ...

    def add_lifecycle_event(self, event: MissionLifecycleEvent) -> None: ...

    async def flush(self) -> None: ...


async def propose_mission(
    store: MissionStore,
    command: ProposeMission,
    *,
    now: datetime,
) -> tuple[MissionDefinition, MissionLifecycleEvent]:
    """Create one immutable definition and its append-only proposal event."""

    _require_aware(now)
    await store.lock_mission(command.mission_id)
    replay = await store.lifecycle_event(command.event_id)
    if replay is not None:
        definition = await store.definition(command.definition_id)
        if definition is None or not _proposal_matches(definition, replay, command):
            raise MissionLifecycleError("mission_idempotency_conflict")
        return definition, replay
    if await store.latest_definition(command.mission_id) is not None:
        raise MissionLifecycleError("mission_already_exists")
    if not await store.actor_is_active_human_steward(
        actor_id=command.responsible_steward_actor_id,
        pack_id=command.definition.target_pack_id,
        at=now,
    ):
        raise MissionLifecycleError("responsible_steward_not_active")
    if not await store.actor_is_active_human_steward(
        actor_id=command.actor_id,
        pack_id=command.definition.target_pack_id,
        at=now,
    ):
        raise MissionLifecycleError("mission_actor_not_active_steward")

    definition = MissionDefinition(
        id=command.definition_id,
        mission_id=command.mission_id,
        definition_version=1,
        prior_definition_id=None,
        gap_kind=command.definition.gap_kind.value,
        title=command.definition.title,
        summary=command.definition.summary,
        target_pack_id=command.definition.target_pack_id,
        target_dataset=command.definition.target_dataset,
        acceptance_target=command.definition.acceptance_target,
        acceptance_criteria=command.definition.acceptance_criteria,
        definition_json=command.definition.model_dump(mode="json"),
        proposed_by_actor_id=command.actor_id,
        responsible_steward_actor_id=command.responsible_steward_actor_id,
        defined_at=now,
    )
    event = MissionLifecycleEvent(
        id=command.event_id,
        mission_id=command.mission_id,
        definition_id=command.definition_id,
        sequence=1,
        prior_event_id=None,
        action=MissionLifecycleAction.PROPOSE.value,
        actor_id=command.actor_id,
        public_reason=command.public_reason,
        next_review_at=None,
        release_receipt_digest=None,
        occurred_at=now,
    )
    store.add_definition(definition)
    store.add_lifecycle_event(event)
    await store.flush()
    return definition, event


async def transition_mission(
    store: MissionStore,
    command: TransitionMission,
    *,
    now: datetime,
) -> MissionLifecycleEvent:
    """Append one authorized, optimistic, idempotent lifecycle transition."""

    _require_aware(now)
    await store.lock_mission(command.mission_id)
    replay = await store.lifecycle_event(command.event_id)
    if replay is not None:
        if not _transition_matches(replay, command):
            raise MissionLifecycleError("mission_idempotency_conflict")
        return replay
    if command.next_review_at is not None and command.next_review_at <= now:
        raise MissionLifecycleError("mission_next_review_not_future")

    definition = await store.definition(command.definition_id)
    if definition is None or definition.mission_id != command.mission_id:
        raise MissionLifecycleError("mission_definition_not_found")
    latest_definition = await store.latest_definition(command.mission_id)
    if latest_definition is None or latest_definition.id != definition.id:
        raise MissionLifecycleError("mission_definition_not_current")
    prior = await store.latest_lifecycle_event(command.mission_id)
    if prior is None:
        raise MissionLifecycleError("mission_not_found")
    if prior.id != command.expected_prior_event_id:
        raise MissionLifecycleError("mission_revision_conflict")
    if now < prior.occurred_at:
        raise MissionLifecycleError("mission_event_time_invalid")
    if prior.definition_id != definition.id:
        raise MissionLifecycleError("mission_definition_not_current")
    if not await store.actor_is_active_human_steward(
        actor_id=command.actor_id,
        pack_id=definition.target_pack_id,
        at=now,
    ):
        raise MissionLifecycleError("mission_actor_not_active_steward")
    if (
        prior.action == MissionLifecycleAction.PROPOSE.value
        and command.action is MissionLifecycleAction.APPROVE
        and prior.actor_id == command.actor_id
    ):
        raise MissionLifecycleError("mission_self_approval_prohibited")

    current_state = _state_for_action(MissionLifecycleAction(prior.action))
    state_after(current_state, command.action)
    if command.action is MissionLifecycleAction.COMPLETE:
        checkpoint = await store.active_progress(definition.id)
        if checkpoint is None:
            raise MissionLifecycleError("mission_progress_unavailable")
        if not await store.progress_is_current(checkpoint):
            raise MissionLifecycleError("mission_progress_stale")
        if checkpoint.accepted_count < definition.acceptance_target:
            raise MissionLifecycleError("mission_acceptance_target_not_met")
    if command.action is MissionLifecycleAction.RELEASE:
        assert command.release_receipt_digest is not None
        receipt = await store.receipt(command.release_receipt_digest)
        if receipt is None:
            raise MissionLifecycleError("mission_release_receipt_not_found")
        if (
            receipt.pack_id != definition.target_pack_id
            or receipt.event_type == "revocation"
            or receipt.published_at < prior.occurred_at
            or receipt.reconciled_at < receipt.published_at
        ):
            raise MissionLifecycleError("mission_release_receipt_invalid")

    event = MissionLifecycleEvent(
        id=command.event_id,
        mission_id=command.mission_id,
        definition_id=command.definition_id,
        sequence=prior.sequence + 1,
        prior_event_id=prior.id,
        action=command.action.value,
        actor_id=command.actor_id,
        public_reason=command.public_reason,
        next_review_at=command.next_review_at,
        release_receipt_digest=command.release_receipt_digest,
        occurred_at=now,
    )
    store.add_lifecycle_event(event)
    await store.flush()
    return event


def lifecycle_state(event: MissionLifecycleEvent) -> MissionLifecycleState:
    return _state_for_action(MissionLifecycleAction(event.action))


def _state_for_action(action: MissionLifecycleAction) -> MissionLifecycleState:
    return {
        MissionLifecycleAction.PROPOSE: MissionLifecycleState.PROPOSED,
        MissionLifecycleAction.APPROVE: MissionLifecycleState.ACTIVE,
        MissionLifecycleAction.PAUSE: MissionLifecycleState.PAUSED,
        MissionLifecycleAction.RESUME: MissionLifecycleState.ACTIVE,
        MissionLifecycleAction.COMPLETE: MissionLifecycleState.COMPLETED,
        MissionLifecycleAction.RELEASE: MissionLifecycleState.RELEASED,
        MissionLifecycleAction.CLOSE: MissionLifecycleState.CLOSED,
    }[action]


def _proposal_matches(
    definition: MissionDefinition,
    event: MissionLifecycleEvent,
    command: ProposeMission,
) -> bool:
    return (
        definition.id == command.definition_id
        and definition.mission_id == command.mission_id
        and definition.definition_version == 1
        and definition.prior_definition_id is None
        and definition.gap_kind == command.definition.gap_kind.value
        and definition.title == command.definition.title
        and definition.summary == command.definition.summary
        and definition.target_pack_id == command.definition.target_pack_id
        and definition.target_dataset == command.definition.target_dataset
        and definition.acceptance_target == command.definition.acceptance_target
        and definition.acceptance_criteria == command.definition.acceptance_criteria
        and definition.definition_json == command.definition.model_dump(mode="json")
        and definition.proposed_by_actor_id == command.actor_id
        and definition.responsible_steward_actor_id == command.responsible_steward_actor_id
        and event.mission_id == command.mission_id
        and event.definition_id == command.definition_id
        and event.sequence == 1
        and event.prior_event_id is None
        and event.action == MissionLifecycleAction.PROPOSE.value
        and event.actor_id == command.actor_id
        and event.public_reason == command.public_reason
    )


def _transition_matches(
    event: MissionLifecycleEvent,
    command: TransitionMission,
) -> bool:
    return (
        event.mission_id == command.mission_id
        and event.definition_id == command.definition_id
        and event.prior_event_id == command.expected_prior_event_id
        and event.actor_id == command.actor_id
        and event.action == command.action.value
        and event.public_reason == command.public_reason
        and event.next_review_at == command.next_review_at
        and event.release_receipt_digest == command.release_receipt_digest
    )


def _bounded_reason(value: str) -> None:
    if not value.strip() or len(value) > 2000:
        raise ValueError("Mission lifecycle changes require a bounded public reason")


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Mission lifecycle timestamps must include a timezone")


__all__ = [
    "MissionLifecycleError",
    "ProposeMission",
    "TransitionMission",
    "lifecycle_state",
    "propose_mission",
    "transition_mission",
]
