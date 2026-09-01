from __future__ import annotations

from datetime import datetime
from enum import StrEnum


class ReviewCaseState(StrEnum):
    PENDING = "pending"
    IN_REVIEW = "in_review"
    CHANGES_REQUESTED = "changes_requested"
    APPROVED = "approved"
    REJECTED = "rejected"
    DISPUTED = "disputed"
    APPEALED = "appealed"
    REOPENED = "reopened"
    CLOSED = "closed"


class ReviewEventType(StrEnum):
    OPENED = "opened"
    CLAIMED = "claimed"
    RELEASED = "released"
    RECUSED = "recused"
    PAUSED = "paused"
    RESUMED = "resumed"
    CHANGES_REQUESTED = "changes_requested"
    CONTRIBUTOR_RESPONDED = "contributor_responded"
    APPROVED = "approved"
    REJECTED = "rejected"
    DISPUTE_OPENED = "dispute_opened"
    DISPUTE_RESOLVED = "dispute_resolved"
    APPEAL_OPENED = "appeal_opened"
    APPEAL_RESOLVED = "appeal_resolved"
    REOPENED = "reopened"
    CLOSED = "closed"


class DisputeCategory(StrEnum):
    EVIDENCE = "evidence"
    ACCURACY = "accuracy"
    RIGHTS = "rights"
    PROCESS = "process"
    OTHER = "other"


class ReviewTransitionError(RuntimeError):
    def __init__(self, current: ReviewCaseState, event: ReviewEventType) -> None:
        super().__init__(f"review_{current.value}_cannot_{event.value}")
        self.code = f"review_{current.value}_cannot_{event.value}"


_TRANSITIONS: dict[
    ReviewCaseState,
    dict[ReviewEventType, ReviewCaseState],
] = {
    ReviewCaseState.PENDING: {
        ReviewEventType.CLAIMED: ReviewCaseState.IN_REVIEW,
        ReviewEventType.CLOSED: ReviewCaseState.CLOSED,
    },
    ReviewCaseState.IN_REVIEW: {
        ReviewEventType.RELEASED: ReviewCaseState.PENDING,
        ReviewEventType.RECUSED: ReviewCaseState.PENDING,
        ReviewEventType.CHANGES_REQUESTED: ReviewCaseState.CHANGES_REQUESTED,
        ReviewEventType.APPROVED: ReviewCaseState.APPROVED,
        ReviewEventType.REJECTED: ReviewCaseState.REJECTED,
        ReviewEventType.CLOSED: ReviewCaseState.CLOSED,
    },
    ReviewCaseState.CHANGES_REQUESTED: {
        ReviewEventType.CONTRIBUTOR_RESPONDED: ReviewCaseState.REOPENED,
        ReviewEventType.DISPUTE_OPENED: ReviewCaseState.DISPUTED,
        ReviewEventType.CLOSED: ReviewCaseState.CLOSED,
    },
    ReviewCaseState.APPROVED: {
        ReviewEventType.DISPUTE_OPENED: ReviewCaseState.DISPUTED,
        ReviewEventType.CLOSED: ReviewCaseState.CLOSED,
    },
    ReviewCaseState.REJECTED: {
        ReviewEventType.DISPUTE_OPENED: ReviewCaseState.DISPUTED,
        ReviewEventType.CLOSED: ReviewCaseState.CLOSED,
    },
    ReviewCaseState.DISPUTED: {
        ReviewEventType.DISPUTE_RESOLVED: ReviewCaseState.REOPENED,
        ReviewEventType.APPEAL_OPENED: ReviewCaseState.APPEALED,
    },
    ReviewCaseState.APPEALED: {
        ReviewEventType.APPEAL_RESOLVED: ReviewCaseState.REOPENED,
    },
    ReviewCaseState.REOPENED: {
        ReviewEventType.CLAIMED: ReviewCaseState.IN_REVIEW,
        ReviewEventType.APPROVED: ReviewCaseState.APPROVED,
        ReviewEventType.REJECTED: ReviewCaseState.REJECTED,
        ReviewEventType.APPEAL_OPENED: ReviewCaseState.APPEALED,
        ReviewEventType.CLOSED: ReviewCaseState.CLOSED,
    },
    ReviewCaseState.CLOSED: {
        ReviewEventType.REOPENED: ReviewCaseState.REOPENED,
    },
}

_NON_STATE_EVENTS = {
    ReviewEventType.PAUSED,
    ReviewEventType.RESUMED,
}


def transition_review_state(
    current: ReviewCaseState,
    event: ReviewEventType,
) -> ReviewCaseState:
    if event in _NON_STATE_EVENTS and current in {
        ReviewCaseState.PENDING,
        ReviewCaseState.IN_REVIEW,
        ReviewCaseState.REOPENED,
    }:
        return current
    target = _TRANSITIONS[current].get(event)
    if target is None:
        raise ReviewTransitionError(current, event)
    return target


def validate_reason(value: str, *, maximum: int = 2000) -> str:
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > maximum:
        raise ValueError("Governance reason must be present and bounded")
    return normalized


def queue_priority(
    *,
    acknowledged_at: datetime | None,
    next_review_at: datetime | None,
    opened_at: datetime,
    now: datetime,
) -> tuple[int, datetime]:
    for value in (opened_at, now):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Governance queue times must include a timezone")
    if next_review_at is not None and (
        next_review_at.tzinfo is None or next_review_at.utcoffset() is None
    ):
        raise ValueError("Governance queue times must include a timezone")
    if acknowledged_at is None:
        return (0, opened_at)
    if next_review_at is not None and next_review_at <= now:
        return (1, next_review_at)
    return (2, next_review_at or opened_at)


__all__ = [
    "DisputeCategory",
    "ReviewCaseState",
    "ReviewEventType",
    "ReviewTransitionError",
    "queue_priority",
    "transition_review_state",
    "validate_reason",
]
