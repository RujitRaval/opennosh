from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid5

from opennosh_api.contributions.models import ContributionDraft
from opennosh_api.missions.contracts import MissionLifecycleState, MissionProgress
from opennosh_api.missions.models import (
    MissionContributionBinding,
    MissionDefinition,
    MissionLifecycleEvent,
    MissionProgressActivation,
    MissionProgressCheckpoint,
)
from opennosh_api.missions.models import (
    MissionProgressRecord as StoredMissionProgressRecord,
)
from opennosh_api.missions.projector import project_mission_progress
from opennosh_api.missions.repository import MissionProjectionInputs
from opennosh_api.missions.service import lifecycle_state


class MissionProgressError(ValueError):
    """Fail-closed binding or checkpoint error with a stable safe code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class BindMissionContribution:
    binding_id: UUID
    mission_id: UUID
    definition_id: UUID
    source_draft_id: UUID
    source_draft_version: int
    actor_id: UUID

    def __post_init__(self) -> None:
        if self.source_draft_version < 1:
            raise ValueError("Mission binding draft version must be positive")


@dataclass(frozen=True, slots=True)
class RebuildMissionProgress:
    checkpoint_id: UUID
    activation_id: UUID
    mission_id: UUID
    definition_id: UUID
    expected_active_checkpoint_id: UUID | None


@dataclass(frozen=True, slots=True)
class MissionProgressBuild:
    checkpoint: MissionProgressCheckpoint
    activation: MissionProgressActivation
    progress: MissionProgress
    checkpoint_created: bool
    activation_changed: bool


class MissionProgressStore(Protocol):
    async def lock_mission(self, mission_id: UUID) -> None: ...

    async def lock_contribution_version(self, draft_id: UUID, draft_version: int) -> None: ...

    async def definition(self, definition_id: UUID) -> MissionDefinition | None: ...

    async def latest_definition(self, mission_id: UUID) -> MissionDefinition | None: ...

    async def latest_lifecycle_event(self, mission_id: UUID) -> MissionLifecycleEvent | None: ...

    async def contribution_draft(self, draft_id: UUID) -> ContributionDraft | None: ...

    async def contribution_binding(self, binding_id: UUID) -> MissionContributionBinding | None: ...

    async def contribution_binding_for_source(
        self, draft_id: UUID, draft_version: int
    ) -> MissionContributionBinding | None: ...

    async def projection_inputs(
        self, *, mission_id: UUID, definition_id: UUID, target_pack_id: str
    ) -> MissionProjectionInputs: ...

    async def progress_checkpoint_for_digest(
        self, definition_id: UUID, event_set_digest: str
    ) -> MissionProgressCheckpoint | None: ...

    async def progress_checkpoint(
        self, checkpoint_id: UUID
    ) -> MissionProgressCheckpoint | None: ...

    async def progress_records(
        self, checkpoint_id: UUID
    ) -> tuple[StoredMissionProgressRecord, ...]: ...

    async def progress_activation(
        self, definition_id: UUID
    ) -> MissionProgressActivation | None: ...

    def add_contribution_binding(self, binding: MissionContributionBinding) -> None: ...

    def add_progress_checkpoint(self, checkpoint: MissionProgressCheckpoint) -> None: ...

    def add_progress_record(self, record: StoredMissionProgressRecord) -> None: ...

    def add_progress_activation(self, activation: MissionProgressActivation) -> None: ...

    async def flush(self) -> None: ...


async def bind_mission_contribution(
    store: MissionProgressStore,
    command: BindMissionContribution,
    *,
    now: datetime,
) -> MissionContributionBinding:
    """Bind the contributor's exact current draft version to one active definition."""

    _require_aware(now)
    await store.lock_mission(command.mission_id)
    await store.lock_contribution_version(
        command.source_draft_id,
        command.source_draft_version,
    )
    replay = await store.contribution_binding(command.binding_id)
    if replay is not None:
        if not _binding_matches(replay, command):
            raise MissionProgressError("mission_binding_idempotency_conflict")
        return replay

    definition = await _current_definition(store, command.mission_id, command.definition_id)
    latest_event = await store.latest_lifecycle_event(command.mission_id)
    if latest_event is None or lifecycle_state(latest_event) is not MissionLifecycleState.ACTIVE:
        raise MissionProgressError("mission_not_active")
    if latest_event.definition_id != definition.id:
        raise MissionProgressError("mission_definition_not_current")

    draft = await store.contribution_draft(command.source_draft_id)
    if draft is None:
        raise MissionProgressError("mission_binding_draft_not_found")
    if draft.user_id != command.actor_id:
        raise MissionProgressError("mission_binding_actor_not_owner")
    if draft.draft_version != command.source_draft_version:
        raise MissionProgressError("mission_binding_draft_version_stale")
    if draft.fields_json.get("pack_id") != definition.target_pack_id:
        raise MissionProgressError("mission_binding_pack_mismatch")

    existing = await store.contribution_binding_for_source(
        command.source_draft_id,
        command.source_draft_version,
    )
    if existing is not None:
        if _binding_matches(existing, command, require_id=False):
            return existing
        raise MissionProgressError("mission_binding_source_conflict")

    binding = MissionContributionBinding(
        id=command.binding_id,
        mission_id=command.mission_id,
        definition_id=command.definition_id,
        source_draft_id=command.source_draft_id,
        source_draft_version=command.source_draft_version,
        bound_by_actor_id=command.actor_id,
        bound_at=now,
    )
    store.add_contribution_binding(binding)
    await store.flush()
    return binding


async def rebuild_mission_progress(
    store: MissionProgressStore,
    command: RebuildMissionProgress,
    *,
    now: datetime,
) -> MissionProgressBuild:
    """Build a complete checkpoint and atomically move the definition pointer last."""

    _require_aware(now)
    await store.lock_mission(command.mission_id)
    definition = await _current_definition(store, command.mission_id, command.definition_id)
    latest_event = await store.latest_lifecycle_event(command.mission_id)
    if latest_event is None or lifecycle_state(latest_event) is MissionLifecycleState.PROPOSED:
        raise MissionProgressError("mission_not_approved")
    if latest_event.definition_id != command.definition_id:
        raise MissionProgressError("mission_definition_not_current")

    inputs = await store.projection_inputs(
        mission_id=command.mission_id,
        definition_id=command.definition_id,
        target_pack_id=definition.target_pack_id,
    )
    progress = project_mission_progress(
        mission_id=command.mission_id,
        definition_id=command.definition_id,
        bindings=inputs.bindings,
        accepted_events=inputs.accepted_events,
    )
    checkpoint = await store.progress_checkpoint(command.checkpoint_id)
    if checkpoint is not None and not await _checkpoint_matches(store, checkpoint, progress):
        raise MissionProgressError("mission_checkpoint_idempotency_conflict")
    if checkpoint is None:
        checkpoint = await store.progress_checkpoint_for_digest(
            command.definition_id,
            progress.event_set_digest,
        )
    checkpoint_created = checkpoint is None
    if checkpoint is None:
        checkpoint = MissionProgressCheckpoint(
            id=command.checkpoint_id,
            mission_id=command.mission_id,
            definition_id=command.definition_id,
            accepted_count=progress.accepted_count,
            matched_event_count=progress.matched_event_count,
            event_set_digest=progress.event_set_digest,
            built_at=now,
        )
        store.add_progress_checkpoint(checkpoint)
        for record in progress.records:
            store.add_progress_record(
                StoredMissionProgressRecord(
                    id=uuid5(
                        command.checkpoint_id,
                        "\x00".join((record.repository, record.pack_id, record.record_id)),
                    ),
                    checkpoint_id=command.checkpoint_id,
                    accepted_event_id=record.accepted_event_id,
                    repository=record.repository,
                    pack_id=record.pack_id,
                    record_id=record.record_id,
                    published_at=record.published_at,
                )
            )
        await store.flush()
    elif not await _checkpoint_matches(store, checkpoint, progress):
        raise MissionProgressError("mission_checkpoint_conflict")

    activation = await store.progress_activation(command.definition_id)
    if activation is not None and activation.checkpoint_id == checkpoint.id:
        return MissionProgressBuild(
            checkpoint=checkpoint,
            activation=activation,
            progress=progress,
            checkpoint_created=checkpoint_created,
            activation_changed=False,
        )
    current_checkpoint_id = activation.checkpoint_id if activation is not None else None
    if current_checkpoint_id != command.expected_active_checkpoint_id:
        raise MissionProgressError("mission_progress_revision_conflict")

    if activation is None:
        activation = MissionProgressActivation(
            id=command.activation_id,
            mission_id=command.mission_id,
            definition_id=command.definition_id,
            checkpoint_id=checkpoint.id,
            activated_at=now,
        )
        store.add_progress_activation(activation)
    else:
        activation.checkpoint_id = checkpoint.id
        activation.activated_at = now
    await store.flush()
    return MissionProgressBuild(
        checkpoint=checkpoint,
        activation=activation,
        progress=progress,
        checkpoint_created=checkpoint_created,
        activation_changed=True,
    )


async def _current_definition(
    store: MissionProgressStore,
    mission_id: UUID,
    definition_id: UUID,
) -> MissionDefinition:
    definition = await store.definition(definition_id)
    if definition is None or definition.mission_id != mission_id:
        raise MissionProgressError("mission_definition_not_found")
    latest = await store.latest_definition(mission_id)
    if latest is None or latest.id != definition.id:
        raise MissionProgressError("mission_definition_not_current")
    return definition


async def _checkpoint_matches(
    store: MissionProgressStore,
    checkpoint: MissionProgressCheckpoint,
    progress: MissionProgress,
) -> bool:
    if (
        checkpoint.mission_id != progress.mission_id
        or checkpoint.definition_id != progress.definition_id
        or checkpoint.accepted_count != progress.accepted_count
        or checkpoint.matched_event_count != progress.matched_event_count
        or checkpoint.event_set_digest != progress.event_set_digest
    ):
        return False
    stored = await store.progress_records(checkpoint.id)
    stored_material = {
        (
            record.repository,
            record.pack_id,
            record.record_id,
            record.accepted_event_id,
            record.published_at,
        )
        for record in stored
    }
    projected_material = {
        (
            record.repository,
            record.pack_id,
            record.record_id,
            record.accepted_event_id,
            record.published_at,
        )
        for record in progress.records
    }
    return stored_material == projected_material and len(stored) == len(stored_material)


def _binding_matches(
    binding: MissionContributionBinding,
    command: BindMissionContribution,
    *,
    require_id: bool = True,
) -> bool:
    return (
        (not require_id or binding.id == command.binding_id)
        and binding.mission_id == command.mission_id
        and binding.definition_id == command.definition_id
        and binding.source_draft_id == command.source_draft_id
        and binding.source_draft_version == command.source_draft_version
        and binding.bound_by_actor_id == command.actor_id
    )


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Mission progress timestamps must include a timezone")


__all__ = [
    "BindMissionContribution",
    "MissionProgressBuild",
    "MissionProgressError",
    "RebuildMissionProgress",
    "bind_mission_contribution",
    "rebuild_mission_progress",
]
