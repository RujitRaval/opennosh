from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from opennosh_api.contributions.service import ContributionReviewResponseError
from opennosh_api.governance import review_service
from opennosh_api.governance.reviews import (
    ReviewCaseState,
    ReviewEventType,
    ReviewTransitionError,
    queue_priority,
    transition_review_state,
    validate_reason,
)
from opennosh_api.governance.schemas import ReviewCaseDecision, ReviewCaseRelease
from pydantic import ValidationError

ACTOR = UUID("00000000-0000-4000-8000-000000000001")
OTHER_ACTOR = UUID("00000000-0000-4000-8000-000000000002")
CASE = UUID("00000000-0000-4000-8000-000000000003")
DRAFT = UUID("00000000-0000-4000-8000-000000000004")


class ScalarSession:
    def __init__(self, *values: object) -> None:
        self.values = iter(values)

    async def scalar(self, _statement: object) -> object | None:
        return next(self.values, None)

    async def scalars(self, _statement: object) -> tuple[object, ...]:
        value = next(self.values, ())
        assert isinstance(value, tuple)
        return value


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
    with pytest.raises(ValueError):
        queue_priority(
            acknowledged_at=aware,
            next_review_at=aware.replace(tzinfo=None),
            opened_at=aware,
            now=aware,
        )


def test_review_boundary_errors_preserve_codes_and_reject_blank_reasons() -> None:
    error = ContributionReviewResponseError("contribution_version_conflict")
    assert str(error) == "contribution_version_conflict"
    assert error.code == "contribution_version_conflict"

    with pytest.raises(ValidationError, match="Reason cannot be blank"):
        ReviewCaseRelease(expected_revision=1, reason="   ")
    with pytest.raises(ValidationError, match="Reason cannot be blank"):
        ReviewCaseDecision(expected_revision=1, outcome="rejected", reason=" \n ")


def test_review_service_rejects_naive_times_and_illegal_transitions() -> None:
    with pytest.raises(ValueError, match="timezone"):
        review_service._require_aware(datetime(2026, 9, 1, 20))
    with pytest.raises(review_service.ReviewCaseError, match="review_pending_cannot_approved"):
        review_service._transition("pending", ReviewEventType.APPROVED)


@pytest.mark.asyncio
async def test_review_service_fail_closed_helpers_cover_missing_records_and_authority() -> None:
    now = datetime(2026, 9, 1, 20, tzinfo=UTC)
    with pytest.raises(review_service.ReviewCaseError, match="review_case_not_found"):
        await review_service._load_case(ScalarSession(), CASE, for_update=True)  # type: ignore[arg-type]
    with pytest.raises(review_service.ReviewCaseError, match="steward_role_not_active"):
        await review_service._require_active_steward(  # type: ignore[arg-type]
            ScalarSession(), pack_id="starter-us", actor_id=ACTOR, now=now
        )

    own_case = SimpleNamespace(
        pack_id="starter-us",
        contributor_actor_id=ACTOR,
        source_draft_id=DRAFT,
    )
    with pytest.raises(review_service.ReviewCaseError, match="self_review_prohibited"):
        await review_service._require_steward_can_review(  # type: ignore[arg-type]
            ScalarSession(OTHER_ACTOR),
            review_case=own_case,
            actor_id=ACTOR,
            now=now,
        )

    event = SimpleNamespace(request_hash="different")
    with pytest.raises(review_service.ReviewCaseError, match="idempotency_payload_mismatch"):
        await review_service._idempotent_event(  # type: ignore[arg-type]
            ScalarSession(event),
            review_case_id=CASE,
            idempotency_key_hash="a" * 64,
            request_hash="b" * 64,
        )

    with pytest.raises(review_service.ReviewCaseError, match="dispute_not_found"):
        await review_service._load_dispute(ScalarSession(), CASE, for_update=True)  # type: ignore[arg-type]
    with pytest.raises(review_service.ReviewCaseError, match="appeal_not_found"):
        await review_service._load_appeal(ScalarSession(), CASE, for_update=True)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_open_review_case_validates_version_pack_and_draft_binding() -> None:
    now = datetime(2026, 9, 1, 20, tzinfo=UTC)
    base = {
        "session": ScalarSession(),
        "source_draft_id": DRAFT,
        "source_draft_version": 1,
        "pack_id": "starter-us",
        "contributor_actor_id": ACTOR,
        "now": now,
    }
    with pytest.raises(ValueError, match="positive draft version"):
        await review_service.open_review_case(**(base | {"source_draft_version": 0}))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="bounded pack ID"):
        await review_service.open_review_case(**(base | {"pack_id": ""}))  # type: ignore[arg-type]
    with pytest.raises(review_service.ReviewCaseError, match="contribution_not_found"):
        await review_service.open_review_case(**base)  # type: ignore[arg-type]

    stale = SimpleNamespace(draft_version=2, user_id=ACTOR, fields_json={"pack_id": "starter-us"})
    with pytest.raises(review_service.ReviewCaseError, match="review_case_draft_version_stale"):
        await review_service.open_review_case(**(base | {"session": ScalarSession(stale)}))  # type: ignore[arg-type]

    wrong_actor = SimpleNamespace(
        draft_version=1,
        user_id=OTHER_ACTOR,
        fields_json={"pack_id": "starter-us"},
    )
    with pytest.raises(review_service.ReviewCaseError, match="review_case_binding_mismatch"):
        await review_service.open_review_case(  # type: ignore[arg-type]
            **(base | {"session": ScalarSession(wrong_actor)})
        )


@pytest.mark.asyncio
async def test_review_queries_hide_unauthorized_or_missing_cases() -> None:
    now = datetime(2026, 9, 1, 20, tzinfo=UTC)
    review_case = SimpleNamespace(
        contributor_actor_id=OTHER_ACTOR,
        pack_id="starter-us",
    )
    with pytest.raises(review_service.ReviewCaseError, match="review_case_not_found"):
        await review_service.get_review_case_for_actor(  # type: ignore[arg-type]
            ScalarSession(review_case, None),
            review_case_id=CASE,
            actor_id=ACTOR,
            now=now,
        )
    with pytest.raises(review_service.ReviewCaseError, match="review_case_not_found"):
        await review_service.get_latest_review_case_for_contributor(  # type: ignore[arg-type]
            ScalarSession(), source_draft_id=DRAFT, actor_id=ACTOR
        )
    events = (SimpleNamespace(sequence=1), SimpleNamespace(sequence=2))
    assert await review_service.list_review_events(  # type: ignore[arg-type]
        ScalarSession(events), review_case_id=CASE
    ) == events
    with pytest.raises(ValueError, match="queue limit"):
        await review_service.list_review_cases_for_steward(  # type: ignore[arg-type]
            ScalarSession(), pack_id="starter-us", actor_id=ACTOR, now=now, limit=0
        )


@pytest.mark.asyncio
async def test_pause_rejects_deadlines_outside_the_bounded_window() -> None:
    now = datetime(2026, 9, 1, 20, tzinfo=UTC)
    with pytest.raises(ValueError, match="within 30 days"):
        await review_service.pause_review_case(  # type: ignore[arg-type]
            ScalarSession(),
            review_case_id=CASE,
            actor_id=ACTOR,
            expected_revision=1,
            idempotency_key=CASE,
            reason="Wait for a source.",
            next_review_at=now + timedelta(days=31),
            now=now,
        )
