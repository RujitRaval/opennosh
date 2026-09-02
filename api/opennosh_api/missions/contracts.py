from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MissionGapKind(StrEnum):
    CUISINE = "cuisine"
    LOCALE = "locale"
    INSTITUTION = "institution"
    DATASET = "dataset"
    MISSING_FIELD = "missing_field"


class MissionLifecycleAction(StrEnum):
    PROPOSE = "propose"
    APPROVE = "approve"
    PAUSE = "pause"
    RESUME = "resume"
    COMPLETE = "complete"
    RELEASE = "release"
    CLOSE = "close"


class MissionLifecycleState(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    RELEASED = "released"
    CLOSED = "closed"


class MissionDefinitionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    gap_kind: MissionGapKind
    title: Annotated[str, Field(min_length=1, max_length=160)]
    summary: Annotated[str, Field(min_length=1, max_length=1000)]
    target_pack_id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,159}$")]
    target_dataset: Annotated[str, Field(min_length=1, max_length=256)]
    acceptance_target: Annotated[int, Field(ge=1, le=100_000)]
    acceptance_criteria: Annotated[str, Field(min_length=1, max_length=2000)]

    @field_validator("title", "summary", "target_dataset", "acceptance_criteria")
    @classmethod
    def public_text_is_meaningful(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Mission definition text must contain non-whitespace text")
        return normalized


class MissionProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mission_id: UUID
    definition_id: UUID
    event_id: UUID
    responsible_steward_actor_id: UUID
    definition: MissionDefinitionSpec
    public_reason: Annotated[str, Field(min_length=1, max_length=2000)]

    @field_validator("public_reason")
    @classmethod
    def public_reason_is_meaningful(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("public_reason must contain non-whitespace text")
        return normalized


class _MissionTransitionRequestBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    definition_id: UUID
    event_id: UUID
    expected_prior_event_id: UUID
    public_reason: Annotated[str, Field(min_length=1, max_length=2000)]

    @field_validator("public_reason")
    @classmethod
    def public_reason_is_meaningful(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("public_reason must contain non-whitespace text")
        return normalized


class MissionSimpleTransitionRequest(_MissionTransitionRequestBase):
    action: Literal[
        MissionLifecycleAction.APPROVE,
        MissionLifecycleAction.RESUME,
        MissionLifecycleAction.COMPLETE,
        MissionLifecycleAction.CLOSE,
    ]


class MissionPauseTransitionRequest(_MissionTransitionRequestBase):
    action: Literal[MissionLifecycleAction.PAUSE]
    next_review_at: datetime

    @field_validator("next_review_at")
    @classmethod
    def next_review_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("next_review_at must include a timezone")
        return value


class MissionReleaseTransitionRequest(_MissionTransitionRequestBase):
    action: Literal[MissionLifecycleAction.RELEASE]
    release_receipt_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


MissionTransitionRequest = Annotated[
    MissionSimpleTransitionRequest
    | MissionPauseTransitionRequest
    | MissionReleaseTransitionRequest,
    Field(discriminator="action"),
]


class MissionLifecycleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    mission_id: UUID
    definition_id: UUID
    event_id: UUID
    sequence: Annotated[int, Field(gt=0)]
    action: MissionLifecycleAction
    state: MissionLifecycleState
    public_reason: Annotated[str, Field(min_length=1, max_length=2000)]
    next_review_at: datetime | None = None
    release_receipt_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    occurred_at: datetime


class MissionBindingFact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mission_id: UUID
    definition_id: UUID
    source_draft_id: UUID
    source_draft_version: Annotated[int, Field(gt=0)]


class AcceptedMissionFact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: UUID
    receipt_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    prior_receipt_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    repository: Annotated[str, Field(min_length=1, max_length=512)]
    commit_sha: Annotated[str, Field(pattern=r"^[0-9a-f]{40}([0-9a-f]{24})?$")]
    pack_id: Annotated[str, Field(min_length=1, max_length=160)]
    record_id: Annotated[str, Field(min_length=1, max_length=160)]
    event_type: Literal["publication", "correction", "revocation"]
    published_at: datetime
    source_draft_id: UUID
    source_draft_version: Annotated[int, Field(gt=0)]
    activity_locale: Annotated[str, Field(min_length=1, max_length=35)] | None = None
    activity_pack_version: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    activity_source_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None

    @field_validator("published_at")
    @classmethod
    def published_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("published_at must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_lineage_shape(self) -> AcceptedMissionFact:
        if (self.event_type == "publication") != (self.prior_receipt_digest is None):
            raise ValueError("accepted event lineage does not match its event type")
        activity_proof = (
            self.activity_locale,
            self.activity_pack_version,
            self.activity_source_digest,
        )
        if any(value is None for value in activity_proof) != all(
            value is None for value in activity_proof
        ):
            raise ValueError("accepted event activity proof must be all present or all absent")
        return self


class MissionProgressRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    repository: Annotated[str, Field(min_length=1, max_length=512)]
    pack_id: Annotated[str, Field(min_length=1, max_length=160)]
    record_id: Annotated[str, Field(min_length=1, max_length=160)]
    accepted_event_id: UUID
    receipt_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    activity_locale: Annotated[str, Field(min_length=1, max_length=35)] | None = None
    activity_pack_version: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    activity_source_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    published_at: datetime

    @field_validator("published_at")
    @classmethod
    def published_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("published_at must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_activity_proof(self) -> MissionProgressRecord:
        activity_proof = (
            self.activity_locale,
            self.activity_pack_version,
            self.activity_source_digest,
        )
        if any(value is None for value in activity_proof) != all(
            value is None for value in activity_proof
        ):
            raise ValueError("mission record activity proof must be all present or all absent")
        return self


class MissionProgress(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    mission_id: UUID
    definition_id: UUID
    accepted_count: Annotated[int, Field(ge=0)]
    matched_event_count: Annotated[int, Field(ge=0)]
    event_set_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    records: tuple[MissionProgressRecord, ...]

    @model_validator(mode="after")
    def validate_counts(self) -> MissionProgress:
        if self.accepted_count != len(self.records):
            raise ValueError("accepted count must equal projected record count")
        if self.matched_event_count < self.accepted_count:
            raise ValueError("matched event count cannot be lower than accepted count")
        return self
