from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from opennosh_api.missions.contracts import MissionLifecycleState
from opennosh_api.missions.public_service import (
    MissionCatalogState,
    PublicMission,
    PublicMissionCatalog,
    PublicMissionState,
    public_mission_catalog,
)

NOW = datetime(2026, 9, 2, 22, tzinfo=UTC)
MISSION_ID = UUID("10000000-0000-4000-8000-000000000040")
DEFINITION_ID = UUID("20000000-0000-4000-8000-000000000040")


class FakeStore:
    def __init__(self) -> None:
        self.definition = SimpleNamespace(
            id=DEFINITION_ID,
            mission_id=MISSION_ID,
            definition_version=1,
            gap_kind="dataset",
            title="Complete a verified dataset",
            summary="Accept records through governed publication only.",
            target_pack_id="opennosh-starter",
            target_dataset="starter-foods",
            acceptance_target=10,
            acceptance_criteria="Ten current accepted records.",
        )
        self.event = SimpleNamespace(
            action="approve",
            definition_id=DEFINITION_ID,
            public_reason="The scoped steward approved this mission.",
            next_review_at=None,
            release_receipt_digest=None,
        )
        self.checkpoint: object | None = SimpleNamespace(
            id=uuid4(),
            accepted_count=3,
            matched_event_count=3,
            built_at=NOW,
        )
        self.current = True
        self.requested_limit: int | None = None

    async def public_mission_snapshots(self, limit: int) -> tuple[object, ...]:
        self.requested_limit = limit
        return (
            SimpleNamespace(
                definition=self.definition,
                lifecycle_event=self.event,
                checkpoint=self.checkpoint,
                progress_is_current=self.current,
            ),
        )


@pytest.mark.asyncio
async def test_disabled_catalog_never_reads_mission_storage() -> None:
    store = FakeStore()

    result = await public_mission_catalog(store, enabled=False, limit=50)  # type: ignore[arg-type]

    assert result.state is MissionCatalogState.UNAVAILABLE
    assert result.reason == "disabled"
    assert result.missions == ()
    assert store.requested_limit is None


@pytest.mark.asyncio
async def test_empty_or_unmoderated_catalog_is_honest_zero() -> None:
    empty = FakeStore()
    empty.public_mission_snapshots = lambda _limit: _empty()  # type: ignore[method-assign]
    assert (
        await public_mission_catalog(empty, enabled=True, limit=20)  # type: ignore[arg-type]
    ).state is MissionCatalogState.ZERO

    proposed = FakeStore()
    proposed.event.action = "propose"
    result = await public_mission_catalog(proposed, enabled=True, limit=20)  # type: ignore[arg-type]
    assert result.state is MissionCatalogState.ZERO
    assert result.missions == ()


async def _empty() -> tuple[object, ...]:
    return ()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "accepted", "current", "expected_lifecycle", "expected_progress"),
    [
        ("approve", 0, True, MissionLifecycleState.ACTIVE, PublicMissionState.ZERO),
        ("approve", 3, True, MissionLifecycleState.ACTIVE, PublicMissionState.PARTIAL),
        ("approve", 10, True, MissionLifecycleState.ACTIVE, PublicMissionState.LIVE),
        ("approve", 3, False, MissionLifecycleState.ACTIVE, PublicMissionState.STALE),
        ("pause", 3, True, MissionLifecycleState.PAUSED, PublicMissionState.PAUSED),
        (
            "complete",
            10,
            True,
            MissionLifecycleState.COMPLETED,
            PublicMissionState.COMPLETED,
        ),
        (
            "release",
            10,
            True,
            MissionLifecycleState.RELEASED,
            PublicMissionState.RELEASED,
        ),
        ("close", 10, True, MissionLifecycleState.CLOSED, PublicMissionState.CLOSED),
    ],
)
async def test_public_mission_states_are_explicit(
    action: str,
    accepted: int,
    current: bool,
    expected_lifecycle: MissionLifecycleState,
    expected_progress: PublicMissionState,
) -> None:
    store = FakeStore()
    store.event.action = action
    store.current = current
    assert store.checkpoint is not None
    store.checkpoint.accepted_count = accepted
    store.checkpoint.matched_event_count = accepted
    if action == "pause":
        store.event.next_review_at = NOW + timedelta(days=7)
    if action == "release":
        store.event.release_receipt_digest = "a" * 64

    result = await public_mission_catalog(store, enabled=True, limit=12)  # type: ignore[arg-type]

    assert result.state is MissionCatalogState.LIVE
    assert store.requested_limit == 12
    mission = result.missions[0]
    assert mission.lifecycle_state is expected_lifecycle
    assert mission.progress_state is expected_progress
    assert mission.accepted_count == accepted
    assert mission.target_pack_id == "opennosh-starter"
    assert mission.release_receipt_digest == ("a" * 64 if action == "release" else None)


@pytest.mark.asyncio
async def test_missing_projection_is_unavailable_not_zero() -> None:
    store = FakeStore()
    store.checkpoint = None

    result = await public_mission_catalog(store, enabled=True, limit=50)  # type: ignore[arg-type]

    mission = result.missions[0]
    assert mission.progress_state is PublicMissionState.UNAVAILABLE
    assert mission.accepted_count is None
    assert mission.checkpoint_id is None


@pytest.mark.asyncio
async def test_missing_or_cross_definition_lifecycle_fails_entire_catalog_closed() -> None:
    missing = FakeStore()
    missing.event = None
    result = await public_mission_catalog(missing, enabled=True, limit=50)  # type: ignore[arg-type]
    assert result.state is MissionCatalogState.UNAVAILABLE
    assert result.reason == "proof_unavailable"

    mismatched = FakeStore()
    mismatched.event.definition_id = uuid4()
    result = await public_mission_catalog(mismatched, enabled=True, limit=50)  # type: ignore[arg-type]
    assert result.state is MissionCatalogState.UNAVAILABLE
    assert result.reason == "proof_unavailable"


def test_catalog_contract_rejects_ambiguous_states() -> None:
    with pytest.raises(ValueError, match="require exactly one safe reason"):
        PublicMissionCatalog(state=MissionCatalogState.UNAVAILABLE)
    with pytest.raises(ValueError, match="cannot contain missions"):
        PublicMissionCatalog(
            state=MissionCatalogState.ZERO,
            # Model construction is not the subject of this state-shape test.
            missions=(
                PublicMission.model_construct(
                    lifecycle_state=MissionLifecycleState.ACTIVE,
                    release_receipt_digest=None,
                ),
            ),
        )
    with pytest.raises(ValueError, match="Unavailable mission catalogs cannot contain"):
        PublicMissionCatalog(
            state=MissionCatalogState.UNAVAILABLE,
            reason="proof_unavailable",
            missions=(
                PublicMission.model_construct(
                    lifecycle_state=MissionLifecycleState.ACTIVE,
                    release_receipt_digest=None,
                ),
            ),
        )


def test_released_mission_contract_requires_exact_receipt_proof() -> None:
    store = FakeStore()
    values = {
        "mission_id": store.definition.mission_id,
        "definition_id": store.definition.id,
        "definition_version": store.definition.definition_version,
        "gap_kind": store.definition.gap_kind,
        "title": store.definition.title,
        "summary": store.definition.summary,
        "target_pack_id": store.definition.target_pack_id,
        "target_dataset": store.definition.target_dataset,
        "acceptance_target": store.definition.acceptance_target,
        "acceptance_criteria": store.definition.acceptance_criteria,
        "lifecycle_state": MissionLifecycleState.RELEASED,
        "progress_state": PublicMissionState.RELEASED,
        "public_reason": store.event.public_reason,
    }
    with pytest.raises(ValueError, match="require exactly one release receipt"):
        PublicMission(**values)
    with pytest.raises(ValueError, match="string_pattern_mismatch"):
        PublicMission(**values, release_receipt_digest="not-a-digest")
