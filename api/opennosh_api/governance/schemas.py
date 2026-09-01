from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from opennosh_api.contributions.schemas import ContributionFieldPatch
from opennosh_api.governance.contracts import GovernanceDecisionOutcome
from opennosh_api.governance.reviews import (
    DisputeCategory,
    ReviewCaseState,
    ReviewEventType,
)


class ReviewCaseAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: Annotated[int, Field(ge=1)]


class ReviewCaseRelease(ReviewCaseAction):
    reason: Annotated[str, Field(min_length=1, max_length=1000)]

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Reason cannot be blank")
        return normalized


class ReviewCasePause(ReviewCaseRelease):
    next_review_at: datetime


class ReviewCaseResume(ReviewCaseRelease):
    pass


class ReviewCaseRecusal(ReviewCaseRelease):
    pass


class ReviewCaseDecision(ReviewCaseAction):
    outcome: Literal[
        GovernanceDecisionOutcome.CHANGES_REQUESTED,
        GovernanceDecisionOutcome.REJECTED,
    ]
    reason: Annotated[str, Field(min_length=1, max_length=2000)]

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Reason cannot be blank")
        return normalized


class ReviewApprovedFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: Annotated[str, Field(min_length=1, max_length=512)]
    content: Annotated[str, Field(max_length=1_048_576)]


class ReviewCaseApproval(ReviewCaseAction):
    pack_id: Annotated[str, Field(min_length=1, max_length=160)]
    record_id: Annotated[str, Field(min_length=1, max_length=160)]
    expected_base_commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}([0-9a-f]{24})?$")]
    files: Annotated[list[ReviewApprovedFile], Field(min_length=1, max_length=32)]
    reason: Annotated[str, Field(min_length=1, max_length=2000)]


class ReviewEventResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: Annotated[int, Field(ge=1)]
    event_type: ReviewEventType
    actor_id: UUID | None
    public_reason: str | None
    occurred_at: datetime


class DisputeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dispute_id: UUID
    decision_id: UUID
    category: DisputeCategory
    public_reason: str
    requested_remedy: str
    state: Literal["open", "resolved"]
    revision: Annotated[int, Field(ge=1)]
    opened_at: datetime
    resolution: str | None
    resolved_at: datetime | None


class AppealResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    appeal_id: UUID
    dispute_id: UUID
    public_reason: str
    requested_remedy: str
    state: Literal["open", "resolved", "reopened"]
    revision: Annotated[int, Field(ge=1)]
    opened_at: datetime
    resolution: str | None
    resolved_at: datetime | None


class ReviewCaseResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_case_id: UUID
    source_draft_id: UUID
    source_draft_version: Annotated[int, Field(ge=1)]
    pack_id: Annotated[str, Field(min_length=1, max_length=160)]
    submitted_fields: dict[str, Any]
    viewer_role: Literal["contributor", "steward"]
    state: ReviewCaseState
    revision: Annotated[int, Field(ge=1)]
    assigned_steward_actor_id: UUID | None
    acknowledged_at: datetime | None
    pause_reason: str | None
    next_review_at: datetime | None
    opened_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    events: list[ReviewEventResponse] = Field(default_factory=list)
    disputes: list[DisputeResponse] = Field(default_factory=list)
    appeals: list[AppealResponse] = Field(default_factory=list)


class ReviewQueueResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pack_id: str
    cases: Annotated[list[ReviewCaseResponse], Field(max_length=100)]


class ReviewDecisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_case: ReviewCaseResponse
    decision_id: UUID
    outcome: Literal[
        GovernanceDecisionOutcome.CHANGES_REQUESTED,
        GovernanceDecisionOutcome.REJECTED,
    ]
    reason: str
    decided_at: datetime


class ReviewApprovalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_case: ReviewCaseResponse
    decision_id: UUID
    publication_intent_id: UUID
    status: Literal["publication_pending"] = "publication_pending"


class PublicDecisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: UUID
    pack_id: Annotated[str, Field(min_length=1, max_length=160)]
    source_draft_version: Annotated[int, Field(ge=1)]
    outcome: GovernanceDecisionOutcome
    reason: Annotated[str, Field(min_length=1, max_length=2000)]
    decided_at: datetime
    publication_state: str | None


class ReviewResponseRequest(ReviewCaseAction):
    expected_draft_version: Annotated[int, Field(ge=1)]
    patches: Annotated[list[ContributionFieldPatch], Field(min_length=1, max_length=25)]
    public_reason: Annotated[str, Field(min_length=1, max_length=2000)]


class ReviewResponseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prior_review_case: ReviewCaseResponse
    next_review_case: ReviewCaseResponse
    next_draft_version: Annotated[int, Field(ge=1)]


class DisputeOpenRequest(ReviewCaseAction):
    category: DisputeCategory
    public_reason: Annotated[str, Field(min_length=1, max_length=2000)]
    requested_remedy: Annotated[str, Field(min_length=1, max_length=1000)]


class DisputeResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_case_revision: Annotated[int, Field(ge=1)]
    expected_dispute_revision: Annotated[int, Field(ge=1)]
    resolution: Annotated[str, Field(min_length=1, max_length=2000)]


class AppealOpenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_case_revision: Annotated[int, Field(ge=1)]
    expected_dispute_revision: Annotated[int, Field(ge=1)]
    public_reason: Annotated[str, Field(min_length=1, max_length=2000)]
    requested_remedy: Annotated[str, Field(min_length=1, max_length=1000)]


class AppealResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_case_revision: Annotated[int, Field(ge=1)]
    expected_appeal_revision: Annotated[int, Field(ge=1)]
    resolution: Annotated[str, Field(min_length=1, max_length=2000)]


__all__ = [
    "AppealOpenRequest",
    "AppealResolveRequest",
    "AppealResponse",
    "DisputeOpenRequest",
    "DisputeResolveRequest",
    "DisputeResponse",
    "ReviewCaseAction",
    "ReviewApprovalResponse",
    "PublicDecisionResponse",
    "ReviewApprovedFile",
    "ReviewCaseApproval",
    "ReviewCaseDecision",
    "ReviewCasePause",
    "ReviewCaseRecusal",
    "ReviewCaseRelease",
    "ReviewCaseResume",
    "ReviewCaseResponse",
    "ReviewDecisionResponse",
    "ReviewEventResponse",
    "ReviewQueueResponse",
    "ReviewResponseRequest",
    "ReviewResponseResult",
]
