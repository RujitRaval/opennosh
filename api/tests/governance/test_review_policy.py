from datetime import UTC, datetime, timedelta

import pytest
from opennosh_api.governance.reviews import (
    ReviewCaseState,
    ReviewEventType,
    ReviewTransitionError,
    queue_priority,
    transition_review_state,
    validate_reason,
)


@pytest.mark.parametrize(
    ("current", "event", "expected"),
    [
        ("pending", "claimed", "in_review"),
        ("in_review", "released", "pending"),
        ("in_review", "recused", "pending"),
        ("in_review", "changes_requested", "changes_requested"),
        ("in_review", "approved", "approved"),
        ("in_review", "rejected", "rejected"),
        ("approved", "dispute_opened", "disputed"),
        ("rejected", "dispute_opened", "disputed"),
        ("disputed", "dispute_resolved", "reopened"),
        ("disputed", "appeal_opened", "appealed"),
        ("appealed", "appeal_resolved", "reopened"),
        ("reopened", "claimed", "in_review"),
        ("closed", "reopened", "reopened"),
    ],
)
def test_review_transition_matrix(
    current: str,
    event: str,
    expected: str,
) -> None:
    assert transition_review_state(
        ReviewCaseState(current), ReviewEventType(event)
    ) == ReviewCaseState(expected)


@pytest.mark.parametrize("event", ["paused", "resumed"])
@pytest.mark.parametrize("state", ["pending", "in_review", "reopened"])
def test_pause_events_preserve_open_review_state(state: str, event: str) -> None:
    assert transition_review_state(
        ReviewCaseState(state), ReviewEventType(event)
    ) == ReviewCaseState(state)


@pytest.mark.parametrize(
    ("current", "event"),
    [
        ("pending", "approved"),
        ("changes_requested", "approved"),
        ("approved", "claimed"),
        ("rejected", "approved"),
        ("appealed", "closed"),
        ("closed", "claimed"),
    ],
)
def test_illegal_review_transition_fails_closed(current: str, event: str) -> None:
    with pytest.raises(ReviewTransitionError) as caught:
        transition_review_state(ReviewCaseState(current), ReviewEventType(event))
    assert caught.value.code == f"review_{current}_cannot_{event}"


def test_reason_is_normalized_and_bounded() -> None:
    assert validate_reason("  compare\npackaging   facts ") == "compare packaging facts"
    with pytest.raises(ValueError):
        validate_reason("   ")
    with pytest.raises(ValueError):
        validate_reason("x" * 2001)


def test_queue_priority_is_explicit_and_deterministic() -> None:
    now = datetime(2026, 9, 1, 20, tzinfo=UTC)
    opened = now - timedelta(days=3)
    overdue = now - timedelta(hours=1)
    future = now + timedelta(hours=1)
    assert queue_priority(
        acknowledged_at=None,
        next_review_at=future,
        opened_at=opened,
        now=now,
    ) == (0, opened)
    assert queue_priority(
        acknowledged_at=opened,
        next_review_at=overdue,
        opened_at=opened,
        now=now,
    ) == (1, overdue)
    assert queue_priority(
        acknowledged_at=opened,
        next_review_at=future,
        opened_at=opened,
        now=now,
    ) == (2, future)


def test_queue_priority_rejects_naive_times() -> None:
    aware = datetime(2026, 9, 1, 20, tzinfo=UTC)
    with pytest.raises(ValueError):
        queue_priority(
            acknowledged_at=None,
            next_review_at=None,
            opened_at=aware.replace(tzinfo=None),
            now=aware,
        )
