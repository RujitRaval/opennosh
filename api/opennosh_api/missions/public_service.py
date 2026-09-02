from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from opennosh_api.missions.contracts import MissionGapKind, MissionLifecycleState
from opennosh_api.missions.models import (
    MissionDefinition,
    MissionProgressCheckpoint,
)
from opennosh_api.missions.repository import PublicMissionSnapshot
from opennosh_api.missions.service import lifecycle_state


class MissionCatalogState(StrEnum):
    UNAVAILABLE = "unavailable"
    ZERO = "zero"
    LIVE = "live"


class PublicMissionState(StrEnum):
    UNAVAILABLE = "unavailable"
    ZERO = "zero"
    PARTIAL = "partial"
    LIVE = "live"
    STALE = "stale"
    PAUSED = "paused"
    COMPLETED = "completed"
    RELEASED = "released"
    CLOSED = "closed"


class PublicMission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mission_id: UUID
    definition_id: UUID
    definition_version: Annotated[int, Field(gt=0)]
    gap_kind: MissionGapKind
    title: Annotated[str, Field(min_length=1, max_length=160)]
    summary: Annotated[str, Field(min_length=1, max_length=1000)]
    target_pack_id: Annotated[str, Field(min_length=1, max_length=160)]
    target_dataset: Annotated[str, Field(min_length=1, max_length=256)]
    acceptance_target: Annotated[int, Field(ge=1, le=100_000)]
    acceptance_criteria: Annotated[str, Field(min_length=1, max_length=2000)]
    lifecycle_state: MissionLifecycleState
    progress_state: PublicMissionState
    public_reason: Annotated[str, Field(min_length=1, max_length=2000)]
    next_review_at: datetime | None = None
    accepted_count: Annotated[int, Field(ge=0)] | None = None
    matched_event_count: Annotated[int, Field(ge=0)] | None = None
    checkpoint_id: UUID | None = None
    checkpoint_built_at: datetime | None = None
    release_receipt_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None

    @model_validator(mode="after")
    def validate_release_proof(self) -> PublicMission:
        released = self.lifecycle_state is MissionLifecycleState.RELEASED
        if released != (self.release_receipt_digest is not None):
            raise ValueError("Released missions require exactly one release receipt digest")
        return self


class PublicMissionCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    state: MissionCatalogState
    reason: Literal["disabled", "proof_unavailable"] | None = None
    missions: tuple[PublicMission, ...] = ()

    @model_validator(mode="after")
    def validate_state_shape(self) -> PublicMissionCatalog:
        if (self.state is MissionCatalogState.UNAVAILABLE) != (self.reason is not None):
            raise ValueError("Unavailable mission catalogs require exactly one safe reason")
        if self.state is MissionCatalogState.UNAVAILABLE and self.missions:
            raise ValueError("Unavailable mission catalogs cannot contain missions")
        if self.state is MissionCatalogState.ZERO and self.missions:
            raise ValueError("Zero mission catalogs cannot contain missions")
        if self.state is MissionCatalogState.LIVE and not self.missions:
            raise ValueError("Live mission catalogs require at least one mission")
        return self


class PublicMissionStore(Protocol):
    async def public_mission_snapshots(self, limit: int) -> tuple[PublicMissionSnapshot, ...]: ...


async def public_mission_catalog(
    store: PublicMissionStore,
    *,
    enabled: bool,
    limit: int,
) -> PublicMissionCatalog:
    """Resolve only moderated current definitions and fail closed on missing proof."""

    if not enabled:
        return PublicMissionCatalog(
            state=MissionCatalogState.UNAVAILABLE,
            reason="disabled",
        )
    snapshots = await store.public_mission_snapshots(limit)
    missions: list[PublicMission] = []
    for snapshot in snapshots:
        definition = snapshot.definition
        event = snapshot.lifecycle_event
        if event is None or event.definition_id != definition.id:
            return unavailable_public_mission_catalog()
        state = lifecycle_state(event)
        if state is MissionLifecycleState.PROPOSED:
            continue
        checkpoint = snapshot.checkpoint
        progress_state = _progress_state(
            state,
            definition,
            checkpoint,
            current=snapshot.progress_is_current,
        )
        missions.append(
            PublicMission(
                mission_id=definition.mission_id,
                definition_id=definition.id,
                definition_version=definition.definition_version,
                gap_kind=MissionGapKind(definition.gap_kind),
                title=definition.title,
                summary=definition.summary,
                target_pack_id=definition.target_pack_id,
                target_dataset=definition.target_dataset,
                acceptance_target=definition.acceptance_target,
                acceptance_criteria=definition.acceptance_criteria,
                lifecycle_state=state,
                progress_state=progress_state,
                public_reason=event.public_reason,
                next_review_at=event.next_review_at,
                accepted_count=checkpoint.accepted_count if checkpoint is not None else None,
                matched_event_count=(
                    checkpoint.matched_event_count if checkpoint is not None else None
                ),
                checkpoint_id=checkpoint.id if checkpoint is not None else None,
                checkpoint_built_at=checkpoint.built_at if checkpoint is not None else None,
                release_receipt_digest=event.release_receipt_digest,
            )
        )
    return PublicMissionCatalog(
        state=MissionCatalogState.LIVE if missions else MissionCatalogState.ZERO,
        missions=tuple(missions),
    )


def unavailable_public_mission_catalog() -> PublicMissionCatalog:
    return PublicMissionCatalog(
        state=MissionCatalogState.UNAVAILABLE,
        reason="proof_unavailable",
    )


def _progress_state(
    lifecycle: MissionLifecycleState,
    definition: MissionDefinition,
    checkpoint: MissionProgressCheckpoint | None,
    *,
    current: bool,
) -> PublicMissionState:
    if checkpoint is None:
        return PublicMissionState.UNAVAILABLE
    if not current:
        return PublicMissionState.STALE
    if lifecycle is MissionLifecycleState.PAUSED:
        return PublicMissionState.PAUSED
    if lifecycle is MissionLifecycleState.RELEASED:
        return PublicMissionState.RELEASED
    if lifecycle is MissionLifecycleState.COMPLETED:
        return PublicMissionState.COMPLETED
    if lifecycle is MissionLifecycleState.CLOSED:
        return PublicMissionState.CLOSED
    if checkpoint.accepted_count == 0:
        return PublicMissionState.ZERO
    if checkpoint.accepted_count < definition.acceptance_target:
        return PublicMissionState.PARTIAL
    return PublicMissionState.LIVE


__all__ = [
    "MissionCatalogState",
    "PublicMission",
    "PublicMissionCatalog",
    "PublicMissionState",
    "public_mission_catalog",
    "unavailable_public_mission_catalog",
]
