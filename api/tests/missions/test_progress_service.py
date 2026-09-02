from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from opennosh_api.missions.contracts import (
    AcceptedMissionFact,
    MissionBindingFact,
)
from opennosh_api.missions.models import MissionContributionBinding
from opennosh_api.missions.progress_service import (
    BindMissionContribution,
    MissionProgressError,
    RebuildMissionProgress,
    bind_mission_contribution,
    rebuild_mission_progress,
)
from opennosh_api.missions.repository import MissionProjectionInputs

NOW = datetime(2026, 9, 2, 21, tzinfo=UTC)
MISSION_ID = UUID("10000000-0000-4000-8000-000000000030")
DEFINITION_ID = UUID("20000000-0000-4000-8000-000000000030")
DRAFT_ID = UUID("30000000-0000-4000-8000-000000000030")
ACTOR_ID = UUID("40000000-0000-4000-8000-000000000030")


class FakeStore:
    def __init__(self) -> None:
        self.definition_row = SimpleNamespace(
            id=DEFINITION_ID,
            mission_id=MISSION_ID,
            target_pack_id="opennosh-starter",
        )
        self.latest_event = SimpleNamespace(
            action="approve",
            definition_id=DEFINITION_ID,
        )
        self.draft = SimpleNamespace(
            id=DRAFT_ID,
            user_id=ACTOR_ID,
            draft_version=1,
            fields_json={"pack_id": "opennosh-starter"},
        )
        self.bindings_by_id: dict[UUID, object] = {}
        self.source_binding: object | None = None
        self.inputs = MissionProjectionInputs(bindings=(), accepted_events=())
        self.checkpoint: object | None = None
        self.records: list[object] = []
        self.activation: object | None = None
        self.added_checkpoints: list[object] = []
        self.added_records: list[object] = []
        self.flushes = 0

    async def lock_mission(self, _mission_id: UUID) -> None:
        return None

    async def lock_contribution_version(self, _draft_id: UUID, _version: int) -> None:
        return None

    async def definition(self, definition_id: UUID) -> object | None:
        return self.definition_row if definition_id == DEFINITION_ID else None

    async def latest_definition(self, mission_id: UUID) -> object | None:
        return self.definition_row if mission_id == MISSION_ID else None

    async def latest_lifecycle_event(self, _mission_id: UUID) -> object | None:
        return self.latest_event

    async def contribution_draft(self, draft_id: UUID) -> object | None:
        return self.draft if draft_id == DRAFT_ID else None

    async def contribution_binding(self, binding_id: UUID) -> object | None:
        return self.bindings_by_id.get(binding_id)

    async def contribution_binding_for_source(
        self, _draft_id: UUID, _draft_version: int
    ) -> object | None:
        return self.source_binding

    async def projection_inputs(
        self, *, mission_id: UUID, definition_id: UUID, target_pack_id: str
    ) -> MissionProjectionInputs:
        assert (mission_id, definition_id) == (MISSION_ID, DEFINITION_ID)
        assert target_pack_id == "opennosh-starter"
        return self.inputs

    async def progress_checkpoint_for_digest(
        self, _definition_id: UUID, _digest: str
    ) -> object | None:
        return self.checkpoint

    async def progress_checkpoint(self, checkpoint_id: UUID) -> object | None:
        if self.checkpoint is not None and self.checkpoint.id == checkpoint_id:
            return self.checkpoint
        return None

    async def progress_records(self, _checkpoint_id: UUID) -> tuple[object, ...]:
        return tuple(self.records)

    async def progress_activation(self, _definition_id: UUID) -> object | None:
        return self.activation

    def add_contribution_binding(self, binding: object) -> None:
        self.bindings_by_id[binding.id] = binding  # type: ignore[attr-defined]
        self.source_binding = binding

    def add_progress_checkpoint(self, checkpoint: object) -> None:
        self.checkpoint = checkpoint
        self.added_checkpoints.append(checkpoint)

    def add_progress_record(self, record: object) -> None:
        self.records.append(record)
        self.added_records.append(record)

    def add_progress_activation(self, activation: object) -> None:
        self.activation = activation

    async def flush(self) -> None:
        self.flushes += 1


def _bind_command(*, binding_id: UUID | None = None) -> BindMissionContribution:
    return BindMissionContribution(
        binding_id=binding_id or uuid4(),
        mission_id=MISSION_ID,
        definition_id=DEFINITION_ID,
        source_draft_id=DRAFT_ID,
        source_draft_version=1,
        actor_id=ACTOR_ID,
    )


def _rebuild_command(
    *, checkpoint_id: UUID | None = None
) -> RebuildMissionProgress:
    return RebuildMissionProgress(
        checkpoint_id=checkpoint_id or uuid4(),
        activation_id=uuid4(),
        mission_id=MISSION_ID,
        definition_id=DEFINITION_ID,
        expected_active_checkpoint_id=None,
    )


def test_binding_command_rejects_nonpositive_draft_version() -> None:
    with pytest.raises(ValueError, match="draft version must be positive"):
        BindMissionContribution(
            binding_id=uuid4(),
            mission_id=MISSION_ID,
            definition_id=DEFINITION_ID,
            source_draft_id=DRAFT_ID,
            source_draft_version=0,
            actor_id=ACTOR_ID,
        )


@pytest.mark.asyncio
async def test_binding_requires_exact_owned_current_draft_and_replays() -> None:
    store = FakeStore()
    command = _bind_command()

    created = await bind_mission_contribution(store, command, now=NOW)  # type: ignore[arg-type]
    replay = await bind_mission_contribution(store, command, now=NOW)  # type: ignore[arg-type]

    assert replay is created
    assert created.source_draft_id == DRAFT_ID
    assert created.source_draft_version == 1
    assert created.bound_by_actor_id == ACTOR_ID
    assert store.flushes == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "code"),
    [
        (lambda store: setattr(store.latest_event, "action", "pause"), "mission_not_active"),
        (
            lambda store: setattr(store.draft, "user_id", uuid4()),
            "mission_binding_actor_not_owner",
        ),
        (
            lambda store: setattr(store.draft, "draft_version", 2),
            "mission_binding_draft_version_stale",
        ),
        (
            lambda store: store.draft.fields_json.update(pack_id="different-pack"),
            "mission_binding_pack_mismatch",
        ),
    ],
)
async def test_binding_fails_closed(change: object, code: str) -> None:
    store = FakeStore()
    change(store)  # type: ignore[operator]

    with pytest.raises(MissionProgressError, match=code) as caught:
        await bind_mission_contribution(store, _bind_command(), now=NOW)  # type: ignore[arg-type]
    assert caught.value.code == code


@pytest.mark.asyncio
async def test_source_version_cannot_join_a_second_definition() -> None:
    store = FakeStore()
    store.source_binding = MissionContributionBinding(
        id=uuid4(),
        mission_id=uuid4(),
        definition_id=uuid4(),
        source_draft_id=DRAFT_ID,
        source_draft_version=1,
        bound_by_actor_id=ACTOR_ID,
        bound_at=NOW,
    )

    with pytest.raises(MissionProgressError, match="mission_binding_source_conflict"):
        await bind_mission_contribution(store, _bind_command(), now=NOW)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_semantically_identical_source_binding_is_reused() -> None:
    store = FakeStore()
    prior = MissionContributionBinding(
        id=uuid4(),
        mission_id=MISSION_ID,
        definition_id=DEFINITION_ID,
        source_draft_id=DRAFT_ID,
        source_draft_version=1,
        bound_by_actor_id=ACTOR_ID,
        bound_at=NOW,
    )
    store.source_binding = prior

    result = await bind_mission_contribution(store, _bind_command(), now=NOW)  # type: ignore[arg-type]

    assert result is prior
    assert store.flushes == 0


@pytest.mark.asyncio
async def test_binding_replay_conflict_fails_closed() -> None:
    store = FakeStore()
    command = _bind_command()
    store.bindings_by_id[command.binding_id] = MissionContributionBinding(
        id=command.binding_id,
        mission_id=MISSION_ID,
        definition_id=DEFINITION_ID,
        source_draft_id=DRAFT_ID,
        source_draft_version=1,
        bound_by_actor_id=uuid4(),
        bound_at=NOW,
    )

    with pytest.raises(MissionProgressError, match="mission_binding_idempotency_conflict"):
        await bind_mission_contribution(store, command, now=NOW)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_binding_rejects_missing_draft_and_mismatched_lifecycle_definition() -> None:
    missing = FakeStore()
    missing.draft = None
    with pytest.raises(MissionProgressError, match="mission_binding_draft_not_found"):
        await bind_mission_contribution(missing, _bind_command(), now=NOW)  # type: ignore[arg-type]

    mismatched = FakeStore()
    mismatched.latest_event.definition_id = uuid4()
    with pytest.raises(MissionProgressError, match="mission_definition_not_current"):
        await bind_mission_contribution(mismatched, _bind_command(), now=NOW)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_rebuild_writes_complete_checkpoint_then_activation_and_replays() -> None:
    store = FakeStore()
    binding = MissionBindingFact(
        mission_id=MISSION_ID,
        definition_id=DEFINITION_ID,
        source_draft_id=DRAFT_ID,
        source_draft_version=1,
    )
    accepted = AcceptedMissionFact(
        event_id=uuid4(),
        receipt_digest="a" * 64,
        repository="github:RujitRaval/opennosh",
        commit_sha="b" * 40,
        pack_id="opennosh-starter",
        record_id="food-1",
        event_type="publication",
        published_at=NOW,
        source_draft_id=DRAFT_ID,
        source_draft_version=1,
    )
    store.inputs = MissionProjectionInputs(bindings=(binding,), accepted_events=(accepted,))
    command = RebuildMissionProgress(
        checkpoint_id=uuid4(),
        activation_id=uuid4(),
        mission_id=MISSION_ID,
        definition_id=DEFINITION_ID,
        expected_active_checkpoint_id=None,
    )

    first = await rebuild_mission_progress(store, command, now=NOW)  # type: ignore[arg-type]
    replay = await rebuild_mission_progress(store, command, now=NOW)  # type: ignore[arg-type]

    assert first.checkpoint_created
    assert first.activation_changed
    assert first.progress.accepted_count == 1
    assert len(store.added_checkpoints) == 1
    assert len(store.added_records) == 1
    assert replay.checkpoint is first.checkpoint
    assert not replay.checkpoint_created
    assert not replay.activation_changed


@pytest.mark.asyncio
async def test_rebuild_rejects_stale_activation_revision() -> None:
    store = FakeStore()
    store.activation = SimpleNamespace(
        id=uuid4(),
        mission_id=MISSION_ID,
        definition_id=DEFINITION_ID,
        checkpoint_id=uuid4(),
        activated_at=NOW,
    )
    command = RebuildMissionProgress(
        checkpoint_id=uuid4(),
        activation_id=uuid4(),
        mission_id=MISSION_ID,
        definition_id=DEFINITION_ID,
        expected_active_checkpoint_id=None,
    )

    with pytest.raises(MissionProgressError, match="mission_progress_revision_conflict"):
        await rebuild_mission_progress(store, command, now=NOW)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_rebuild_rejects_checkpoint_id_reuse_for_different_material() -> None:
    store = FakeStore()
    checkpoint_id = uuid4()
    store.checkpoint = SimpleNamespace(
        id=checkpoint_id,
        mission_id=MISSION_ID,
        definition_id=DEFINITION_ID,
        accepted_count=1,
        matched_event_count=1,
        event_set_digest="f" * 64,
    )
    command = RebuildMissionProgress(
        checkpoint_id=checkpoint_id,
        activation_id=uuid4(),
        mission_id=MISSION_ID,
        definition_id=DEFINITION_ID,
        expected_active_checkpoint_id=None,
    )

    with pytest.raises(
        MissionProgressError,
        match="mission_checkpoint_idempotency_conflict",
    ):
        await rebuild_mission_progress(store, command, now=NOW)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_rebuild_rejects_conflicting_digest_checkpoint_material() -> None:
    store = FakeStore()
    command = _rebuild_command()
    store.checkpoint = SimpleNamespace(
        id=uuid4(),
        mission_id=MISSION_ID,
        definition_id=DEFINITION_ID,
        accepted_count=0,
        matched_event_count=0,
        event_set_digest=(
            "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e6bf11f"
            "a10f1d31e7b0f"
        ),
    )
    store.records = [
        SimpleNamespace(
            repository="github:other/repository",
            pack_id="other-pack",
            record_id="other-record",
            accepted_event_id=uuid4(),
            published_at=NOW,
        )
    ]

    with pytest.raises(MissionProgressError, match="mission_checkpoint_conflict"):
        await rebuild_mission_progress(store, command, now=NOW)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_rebuild_requires_approved_current_definition() -> None:
    proposed = FakeStore()
    proposed.latest_event.action = "propose"
    with pytest.raises(MissionProgressError, match="mission_not_approved"):
        await rebuild_mission_progress(proposed, _rebuild_command(), now=NOW)  # type: ignore[arg-type]

    mismatched_event = FakeStore()
    mismatched_event.latest_event.definition_id = uuid4()
    with pytest.raises(MissionProgressError, match="mission_definition_not_current"):
        await rebuild_mission_progress(
            mismatched_event, _rebuild_command(), now=NOW  # type: ignore[arg-type]
        )

    missing = FakeStore()
    missing.definition_row = None
    with pytest.raises(MissionProgressError, match="mission_definition_not_found"):
        await rebuild_mission_progress(missing, _rebuild_command(), now=NOW)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_progress_operations_require_timezone_aware_time() -> None:
    naive = datetime(2026, 9, 2, 21)
    with pytest.raises(ValueError, match="timestamps must include a timezone"):
        await bind_mission_contribution(FakeStore(), _bind_command(), now=naive)  # type: ignore[arg-type]
