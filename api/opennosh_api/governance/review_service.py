from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import case, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.contributions.models import ContributionDraft
from opennosh_api.governance.contracts import (
    CANONICAL_FORGE_TARGET,
    PROTECTED_STATUS_CHECKS,
    ApprovedChangeSet,
    GovernanceDecisionOutcome,
    GovernanceRole,
)
from opennosh_api.governance.models import (
    GovernanceAppeal,
    GovernanceDecision,
    GovernanceDispute,
    GovernanceRecusal,
    GovernanceReviewCase,
    GovernanceReviewEvent,
    GovernanceRoleAssignment,
)
from opennosh_api.governance.reviews import (
    DisputeCategory,
    ReviewCaseState,
    ReviewEventType,
    ReviewTransitionError,
    transition_review_state,
    validate_reason,
)
from opennosh_api.governance.service import (
    ApproveContribution,
    GovernanceDecisionError,
    approve_contribution,
)
from opennosh_api.jobs import JobQueue
from opennosh_api.publication.models import PublicationIntent


class ReviewCaseError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _transition(
    current: str,
    event: ReviewEventType,
) -> ReviewCaseState:
    try:
        return transition_review_state(ReviewCaseState(current), event)
    except ReviewTransitionError as error:
        raise ReviewCaseError(error.code) from error


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Governance time must include a timezone")


def _hash_idempotency_key(value: UUID) -> str:
    return hashlib.sha256(str(value).encode()).hexdigest()


def _request_hash(value: object) -> str:
    material = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(material.encode()).hexdigest()


async def _load_case(
    session: AsyncSession,
    review_case_id: UUID,
    *,
    for_update: bool,
) -> GovernanceReviewCase:
    statement = select(GovernanceReviewCase).where(GovernanceReviewCase.id == review_case_id)
    if for_update:
        statement = statement.with_for_update()
    review_case = await session.scalar(statement)
    if review_case is None:
        raise ReviewCaseError("review_case_not_found")
    return review_case


async def _require_active_steward(
    session: AsyncSession,
    *,
    pack_id: str,
    actor_id: UUID,
    now: datetime,
) -> None:
    role_id = await session.scalar(
        select(GovernanceRoleAssignment.id).where(
            GovernanceRoleAssignment.pack_id == pack_id,
            GovernanceRoleAssignment.actor_id == actor_id,
            GovernanceRoleAssignment.role == GovernanceRole.STEWARD.value,
            GovernanceRoleAssignment.granted_at <= now,
            (
                GovernanceRoleAssignment.revoked_at.is_(None)
                | (GovernanceRoleAssignment.revoked_at > now)
            ),
        )
    )
    if role_id is None:
        raise ReviewCaseError("steward_role_not_active")


async def _require_steward_can_review(
    session: AsyncSession,
    *,
    review_case: GovernanceReviewCase,
    actor_id: UUID,
    now: datetime,
) -> None:
    await _require_active_steward(
        session,
        pack_id=review_case.pack_id,
        actor_id=actor_id,
        now=now,
    )
    if review_case.contributor_actor_id == actor_id:
        raise ReviewCaseError("self_review_prohibited")
    recusal_id = await session.scalar(
        select(GovernanceRecusal.id).where(
            GovernanceRecusal.source_draft_id == review_case.source_draft_id,
            GovernanceRecusal.actor_id == actor_id,
            GovernanceRecusal.recused_at <= now,
        )
    )
    if recusal_id is not None:
        raise ReviewCaseError("steward_recused")


async def _idempotent_event(
    session: AsyncSession,
    *,
    review_case_id: UUID,
    idempotency_key_hash: str,
    request_hash: str,
) -> GovernanceReviewEvent | None:
    event = await session.scalar(
        select(GovernanceReviewEvent).where(
            GovernanceReviewEvent.review_case_id == review_case_id,
            GovernanceReviewEvent.idempotency_key_hash == idempotency_key_hash,
        )
    )
    if event is not None and event.request_hash != request_hash:
        raise ReviewCaseError("idempotency_payload_mismatch")
    return event


def _append_event(
    session: AsyncSession,
    *,
    review_case: GovernanceReviewCase,
    event_type: ReviewEventType,
    actor_id: UUID | None,
    public_reason: str | None,
    now: datetime,
    idempotency_key_hash: str | None = None,
    request_hash: str | None = None,
    details: dict[str, object] | None = None,
    event_id_generator: Callable[[], UUID] = uuid4,
) -> GovernanceReviewEvent:
    event = GovernanceReviewEvent(
        id=event_id_generator(),
        review_case_id=review_case.id,
        sequence=review_case.revision,
        event_type=event_type.value,
        actor_id=actor_id,
        public_reason=public_reason,
        idempotency_key_hash=idempotency_key_hash,
        request_hash=request_hash,
        details_json={} if details is None else details,
        occurred_at=now,
    )
    session.add(event)
    return event


async def open_review_case(
    session: AsyncSession,
    *,
    source_draft_id: UUID,
    source_draft_version: int,
    pack_id: str,
    contributor_actor_id: UUID,
    now: datetime,
    review_case_id_generator: Callable[[], UUID] = uuid4,
    event_id_generator: Callable[[], UUID] = uuid4,
) -> GovernanceReviewCase:
    _require_aware(now)
    if source_draft_version < 1:
        raise ValueError("Review case requires a positive draft version")
    if not pack_id or len(pack_id) > 160:
        raise ValueError("Review case requires a bounded pack ID")
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
        {"scope": f"opennosh.review-case:{source_draft_id}:{source_draft_version}"},
    )
    existing = await session.scalar(
        select(GovernanceReviewCase).where(
            GovernanceReviewCase.source_draft_id == source_draft_id,
            GovernanceReviewCase.source_draft_version == source_draft_version,
        )
    )
    if existing is not None:
        if existing.pack_id != pack_id or existing.contributor_actor_id != contributor_actor_id:
            raise ReviewCaseError("review_case_binding_mismatch")
        return existing
    review_case = GovernanceReviewCase(
        id=review_case_id_generator(),
        source_draft_id=source_draft_id,
        source_draft_version=source_draft_version,
        pack_id=pack_id,
        contributor_actor_id=contributor_actor_id,
        state=ReviewCaseState.PENDING.value,
        revision=1,
        opened_at=now,
        updated_at=now,
    )
    session.add(review_case)
    _append_event(
        session,
        review_case=review_case,
        event_type=ReviewEventType.OPENED,
        actor_id=contributor_actor_id,
        public_reason="Submitted for steward review.",
        now=now,
        event_id_generator=event_id_generator,
    )
    await session.flush()
    return review_case


async def claim_review_case(
    session: AsyncSession,
    *,
    review_case_id: UUID,
    actor_id: UUID,
    expected_revision: int,
    idempotency_key: UUID,
    now: datetime,
) -> GovernanceReviewCase:
    _require_aware(now)
    key_hash = _hash_idempotency_key(idempotency_key)
    request_hash = _request_hash(
        {"action": "claim", "actor_id": actor_id, "expected_revision": expected_revision}
    )
    review_case = await _load_case(session, review_case_id, for_update=True)
    if await _idempotent_event(
        session,
        review_case_id=review_case.id,
        idempotency_key_hash=key_hash,
        request_hash=request_hash,
    ):
        return review_case
    if review_case.revision != expected_revision:
        raise ReviewCaseError("review_case_revision_conflict")
    await _require_steward_can_review(session, review_case=review_case, actor_id=actor_id, now=now)
    next_state = _transition(review_case.state, ReviewEventType.CLAIMED)
    review_case.state = next_state.value
    review_case.assigned_steward_actor_id = actor_id
    review_case.acknowledged_at = now
    review_case.pause_reason = None
    review_case.next_review_at = None
    review_case.revision += 1
    review_case.updated_at = now
    _append_event(
        session,
        review_case=review_case,
        event_type=ReviewEventType.CLAIMED,
        actor_id=actor_id,
        public_reason="A steward acknowledged this review.",
        now=now,
        idempotency_key_hash=key_hash,
        request_hash=request_hash,
    )
    await session.flush()
    return review_case


async def release_review_case(
    session: AsyncSession,
    *,
    review_case_id: UUID,
    actor_id: UUID,
    expected_revision: int,
    idempotency_key: UUID,
    reason: str,
    now: datetime,
) -> GovernanceReviewCase:
    _require_aware(now)
    normalized_reason = validate_reason(reason, maximum=1000)
    key_hash = _hash_idempotency_key(idempotency_key)
    request_hash = _request_hash(
        {
            "action": "release",
            "actor_id": actor_id,
            "expected_revision": expected_revision,
            "reason": normalized_reason,
        }
    )
    review_case = await _load_case(session, review_case_id, for_update=True)
    if await _idempotent_event(
        session,
        review_case_id=review_case.id,
        idempotency_key_hash=key_hash,
        request_hash=request_hash,
    ):
        return review_case
    if review_case.revision != expected_revision:
        raise ReviewCaseError("review_case_revision_conflict")
    await _require_steward_can_review(session, review_case=review_case, actor_id=actor_id, now=now)
    if review_case.assigned_steward_actor_id != actor_id:
        raise ReviewCaseError("review_case_not_assigned_to_actor")
    review_case.state = _transition(review_case.state, ReviewEventType.RELEASED).value
    review_case.assigned_steward_actor_id = None
    review_case.acknowledged_at = None
    review_case.pause_reason = None
    review_case.next_review_at = None
    review_case.revision += 1
    review_case.updated_at = now
    _append_event(
        session,
        review_case=review_case,
        event_type=ReviewEventType.RELEASED,
        actor_id=actor_id,
        public_reason=normalized_reason,
        now=now,
        idempotency_key_hash=key_hash,
        request_hash=request_hash,
    )
    await session.flush()
    return review_case


async def record_nonapproval_decision(
    session: AsyncSession,
    *,
    review_case_id: UUID,
    actor_id: UUID,
    outcome: GovernanceDecisionOutcome,
    expected_revision: int,
    idempotency_key: UUID,
    reason: str,
    now: datetime,
    decision_id_generator: Callable[[], UUID] = uuid4,
) -> tuple[GovernanceReviewCase, GovernanceDecision]:
    _require_aware(now)
    if outcome not in {
        GovernanceDecisionOutcome.CHANGES_REQUESTED,
        GovernanceDecisionOutcome.REJECTED,
    }:
        raise ValueError("Non-approval review requires changes_requested or rejected")
    normalized_reason = validate_reason(reason)
    key_hash = _hash_idempotency_key(idempotency_key)
    request_hash = _request_hash(
        {
            "action": outcome.value,
            "actor_id": actor_id,
            "expected_revision": expected_revision,
            "reason": normalized_reason,
        }
    )
    review_case = await _load_case(session, review_case_id, for_update=True)
    existing_event = await _idempotent_event(
        session,
        review_case_id=review_case.id,
        idempotency_key_hash=key_hash,
        request_hash=request_hash,
    )
    if existing_event is not None:
        decision_id = existing_event.details_json.get("decision_id")
        decision = (
            None
            if not isinstance(decision_id, str)
            else await session.get(GovernanceDecision, UUID(decision_id))
        )
        if decision is None:
            raise ReviewCaseError("review_decision_not_found")
        return review_case, decision
    if review_case.revision != expected_revision:
        raise ReviewCaseError("review_case_revision_conflict")
    await _require_steward_can_review(session, review_case=review_case, actor_id=actor_id, now=now)
    if review_case.assigned_steward_actor_id != actor_id:
        raise ReviewCaseError("review_case_not_assigned_to_actor")
    draft = await session.scalar(
        select(ContributionDraft)
        .where(ContributionDraft.id == review_case.source_draft_id)
        .with_for_update()
    )
    if draft is None:
        raise ReviewCaseError("contribution_not_found")
    if draft.draft_version != review_case.source_draft_version:
        raise ReviewCaseError("review_case_draft_version_stale")
    if draft.review_state != "in_review":
        raise ReviewCaseError("contribution_not_in_review")
    event_type = ReviewEventType(outcome.value)
    review_case.state = _transition(review_case.state, event_type).value
    review_case.revision += 1
    review_case.updated_at = now
    draft.review_state = outcome.value
    draft.updated_at = now
    decision = GovernanceDecision(
        id=decision_id_generator(),
        source_draft_id=draft.id,
        source_draft_version=draft.draft_version,
        pack_id=review_case.pack_id,
        record_id=f"draft:{draft.id}",
        contributor_actor_id=draft.user_id,
        deciding_actor_id=actor_id,
        outcome=outcome.value,
        reason=normalized_reason,
        approved_payload_digest=None,
        approved_changes_json=None,
        expected_base_commit=None,
        required_checks_json=None,
        forge_target=None,
        decided_at=now,
    )
    session.add(decision)
    _append_event(
        session,
        review_case=review_case,
        event_type=event_type,
        actor_id=actor_id,
        public_reason=normalized_reason,
        now=now,
        idempotency_key_hash=key_hash,
        request_hash=request_hash,
        details={"decision_id": str(decision.id)},
    )
    await session.flush()
    return review_case, decision


async def approve_review_case(
    session: AsyncSession,
    queue: JobQueue,
    *,
    review_case_id: UUID,
    actor_id: UUID,
    approved_changes: ApprovedChangeSet,
    record_id: str,
    expected_base_commit: str,
    expected_revision: int,
    idempotency_key: UUID,
    reason: str,
    now: datetime,
) -> tuple[GovernanceReviewCase, GovernanceDecision, PublicationIntent]:
    _require_aware(now)
    normalized_reason = validate_reason(reason)
    key_hash = _hash_idempotency_key(idempotency_key)
    request_hash = _request_hash(
        {
            "action": "approved",
            "actor_id": actor_id,
            "approved_changes_digest": approved_changes.digest,
            "record_id": record_id,
            "expected_base_commit": expected_base_commit,
            "expected_revision": expected_revision,
            "reason": normalized_reason,
        }
    )
    review_case = await _load_case(session, review_case_id, for_update=True)
    existing_event = await _idempotent_event(
        session,
        review_case_id=review_case.id,
        idempotency_key_hash=key_hash,
        request_hash=request_hash,
    )
    if existing_event is not None:
        decision_id = existing_event.details_json.get("decision_id")
        publication_intent_id = existing_event.details_json.get("publication_intent_id")
        decision = (
            None
            if not isinstance(decision_id, str)
            else await session.get(GovernanceDecision, UUID(decision_id))
        )
        publication_intent = (
            None
            if not isinstance(publication_intent_id, str)
            else await session.get(PublicationIntent, UUID(publication_intent_id))
        )
        if decision is None or publication_intent is None:
            raise ReviewCaseError("review_approval_not_found")
        return review_case, decision, publication_intent
    if review_case.revision != expected_revision:
        raise ReviewCaseError("review_case_revision_conflict")
    await _require_steward_can_review(session, review_case=review_case, actor_id=actor_id, now=now)
    if review_case.assigned_steward_actor_id != actor_id:
        raise ReviewCaseError("review_case_not_assigned_to_actor")
    if approved_changes.pack_id != review_case.pack_id:
        raise ReviewCaseError("pack_scope_mismatch")
    try:
        decision, publication_intent = await approve_contribution(
            session,
            queue,
            ApproveContribution(
                source_draft_id=review_case.source_draft_id,
                deciding_actor_id=actor_id,
                approved_changes=approved_changes,
                record_id=record_id,
                expected_base_commit=expected_base_commit,
                required_checks=PROTECTED_STATUS_CHECKS,
                forge_target=CANONICAL_FORGE_TARGET,
                reason=normalized_reason,
            ),
            now=now,
        )
    except GovernanceDecisionError as error:
        raise ReviewCaseError(error.code) from error
    review_case.state = _transition(review_case.state, ReviewEventType.APPROVED).value
    review_case.revision += 1
    review_case.updated_at = now
    _append_event(
        session,
        review_case=review_case,
        event_type=ReviewEventType.APPROVED,
        actor_id=actor_id,
        public_reason=normalized_reason,
        now=now,
        idempotency_key_hash=key_hash,
        request_hash=request_hash,
        details={
            "decision_id": str(decision.id),
            "publication_intent_id": str(publication_intent.id),
        },
    )
    await session.flush()
    return review_case, decision, publication_intent


async def list_review_cases_for_steward(
    session: AsyncSession,
    *,
    pack_id: str,
    actor_id: UUID,
    now: datetime,
    limit: int = 50,
) -> tuple[GovernanceReviewCase, ...]:
    _require_aware(now)
    if not 1 <= limit <= 100:
        raise ValueError("Governance queue limit must be between 1 and 100")
    await _require_active_steward(session, pack_id=pack_id, actor_id=actor_id, now=now)
    priority = case(
        (GovernanceReviewCase.acknowledged_at.is_(None), 0),
        (
            GovernanceReviewCase.next_review_at.is_not(None)
            & (GovernanceReviewCase.next_review_at <= now),
            1,
        ),
        else_=2,
    )
    cases = await session.scalars(
        select(GovernanceReviewCase)
        .where(
            GovernanceReviewCase.pack_id == pack_id,
            GovernanceReviewCase.state != ReviewCaseState.CLOSED.value,
        )
        .order_by(
            priority,
            GovernanceReviewCase.next_review_at.asc().nulls_last(),
            GovernanceReviewCase.opened_at.asc(),
            GovernanceReviewCase.id.asc(),
        )
        .limit(limit)
    )
    return tuple(cases)


async def get_review_case_for_actor(
    session: AsyncSession,
    *,
    review_case_id: UUID,
    actor_id: UUID,
    now: datetime,
) -> GovernanceReviewCase:
    _require_aware(now)
    review_case = await _load_case(session, review_case_id, for_update=False)
    if review_case.contributor_actor_id == actor_id:
        return review_case
    try:
        await _require_active_steward(
            session,
            pack_id=review_case.pack_id,
            actor_id=actor_id,
            now=now,
        )
    except ReviewCaseError as error:
        raise ReviewCaseError("review_case_not_found") from error
    return review_case


async def list_review_events(
    session: AsyncSession,
    *,
    review_case_id: UUID,
) -> tuple[GovernanceReviewEvent, ...]:
    events = await session.scalars(
        select(GovernanceReviewEvent)
        .where(GovernanceReviewEvent.review_case_id == review_case_id)
        .order_by(GovernanceReviewEvent.sequence.asc())
    )
    return tuple(events)


async def _load_dispute(
    session: AsyncSession,
    dispute_id: UUID,
    *,
    for_update: bool,
) -> GovernanceDispute:
    statement = select(GovernanceDispute).where(GovernanceDispute.id == dispute_id)
    if for_update:
        statement = statement.with_for_update()
    dispute = await session.scalar(statement)
    if dispute is None:
        raise ReviewCaseError("dispute_not_found")
    return dispute


async def _load_appeal(
    session: AsyncSession,
    appeal_id: UUID,
    *,
    for_update: bool,
) -> GovernanceAppeal:
    statement = select(GovernanceAppeal).where(GovernanceAppeal.id == appeal_id)
    if for_update:
        statement = statement.with_for_update()
    appeal = await session.scalar(statement)
    if appeal is None:
        raise ReviewCaseError("appeal_not_found")
    return appeal


async def open_dispute(
    session: AsyncSession,
    *,
    review_case_id: UUID,
    actor_id: UUID,
    category: DisputeCategory,
    public_reason: str,
    requested_remedy: str,
    expected_revision: int,
    idempotency_key: UUID,
    now: datetime,
    dispute_id_generator: Callable[[], UUID] = uuid4,
) -> tuple[GovernanceReviewCase, GovernanceDispute]:
    _require_aware(now)
    reason = validate_reason(public_reason)
    remedy = validate_reason(requested_remedy, maximum=1000)
    key_hash = _hash_idempotency_key(idempotency_key)
    request_hash = _request_hash(
        {
            "action": "open_dispute",
            "actor_id": actor_id,
            "category": category.value,
            "expected_revision": expected_revision,
            "public_reason": reason,
            "requested_remedy": remedy,
        }
    )
    review_case = await _load_case(session, review_case_id, for_update=True)
    existing_event = await _idempotent_event(
        session,
        review_case_id=review_case.id,
        idempotency_key_hash=key_hash,
        request_hash=request_hash,
    )
    if existing_event is not None:
        dispute_id = existing_event.details_json.get("dispute_id")
        dispute = (
            None
            if not isinstance(dispute_id, str)
            else await session.get(GovernanceDispute, UUID(dispute_id))
        )
        if dispute is None:
            raise ReviewCaseError("dispute_not_found")
        return review_case, dispute
    if review_case.revision != expected_revision:
        raise ReviewCaseError("review_case_revision_conflict")
    if review_case.contributor_actor_id != actor_id:
        await _require_steward_can_review(
            session, review_case=review_case, actor_id=actor_id, now=now
        )
    decision = await session.scalar(
        select(GovernanceDecision)
        .where(
            GovernanceDecision.source_draft_id == review_case.source_draft_id,
            GovernanceDecision.source_draft_version == review_case.source_draft_version,
        )
        .order_by(GovernanceDecision.decided_at.desc())
        .limit(1)
    )
    review_case.state = _transition(review_case.state, ReviewEventType.DISPUTE_OPENED).value
    review_case.revision += 1
    review_case.updated_at = now
    dispute = GovernanceDispute(
        id=dispute_id_generator(),
        review_case_id=review_case.id,
        decision_id=None if decision is None else decision.id,
        pack_id=review_case.pack_id,
        opened_by_actor_id=actor_id,
        category=category.value,
        public_reason=reason,
        requested_remedy=remedy,
        state="open",
        revision=1,
        opened_at=now,
    )
    session.add(dispute)
    _append_event(
        session,
        review_case=review_case,
        event_type=ReviewEventType.DISPUTE_OPENED,
        actor_id=actor_id,
        public_reason=reason,
        now=now,
        idempotency_key_hash=key_hash,
        request_hash=request_hash,
        details={"dispute_id": str(dispute.id), "category": category.value},
    )
    await session.flush()
    return review_case, dispute


async def resolve_dispute(
    session: AsyncSession,
    *,
    dispute_id: UUID,
    actor_id: UUID,
    expected_case_revision: int,
    expected_dispute_revision: int,
    idempotency_key: UUID,
    resolution: str,
    now: datetime,
) -> tuple[GovernanceReviewCase, GovernanceDispute]:
    _require_aware(now)
    normalized_resolution = validate_reason(resolution)
    dispute = await _load_dispute(session, dispute_id, for_update=True)
    review_case = await _load_case(session, dispute.review_case_id, for_update=True)
    key_hash = _hash_idempotency_key(idempotency_key)
    request_hash = _request_hash(
        {
            "action": "resolve_dispute",
            "actor_id": actor_id,
            "expected_case_revision": expected_case_revision,
            "expected_dispute_revision": expected_dispute_revision,
            "resolution": normalized_resolution,
        }
    )
    if await _idempotent_event(
        session,
        review_case_id=review_case.id,
        idempotency_key_hash=key_hash,
        request_hash=request_hash,
    ):
        return review_case, dispute
    if review_case.revision != expected_case_revision:
        raise ReviewCaseError("review_case_revision_conflict")
    if dispute.revision != expected_dispute_revision:
        raise ReviewCaseError("dispute_revision_conflict")
    if dispute.state != "open":
        raise ReviewCaseError("dispute_not_open")
    await _require_steward_can_review(session, review_case=review_case, actor_id=actor_id, now=now)
    dispute.state = "resolved"
    dispute.revision += 1
    dispute.resolution = normalized_resolution
    dispute.resolved_by_actor_id = actor_id
    dispute.resolved_at = now
    review_case.state = _transition(review_case.state, ReviewEventType.DISPUTE_RESOLVED).value
    review_case.revision += 1
    review_case.updated_at = now
    _append_event(
        session,
        review_case=review_case,
        event_type=ReviewEventType.DISPUTE_RESOLVED,
        actor_id=actor_id,
        public_reason=normalized_resolution,
        now=now,
        idempotency_key_hash=key_hash,
        request_hash=request_hash,
        details={"dispute_id": str(dispute.id)},
    )
    await session.flush()
    return review_case, dispute


async def open_appeal(
    session: AsyncSession,
    *,
    dispute_id: UUID,
    actor_id: UUID,
    expected_case_revision: int,
    expected_dispute_revision: int,
    idempotency_key: UUID,
    public_reason: str,
    requested_remedy: str,
    now: datetime,
    appeal_id_generator: Callable[[], UUID] = uuid4,
) -> tuple[GovernanceReviewCase, GovernanceAppeal]:
    _require_aware(now)
    reason = validate_reason(public_reason)
    remedy = validate_reason(requested_remedy, maximum=1000)
    dispute = await _load_dispute(session, dispute_id, for_update=True)
    review_case = await _load_case(session, dispute.review_case_id, for_update=True)
    key_hash = _hash_idempotency_key(idempotency_key)
    request_hash = _request_hash(
        {
            "action": "open_appeal",
            "actor_id": actor_id,
            "expected_case_revision": expected_case_revision,
            "expected_dispute_revision": expected_dispute_revision,
            "public_reason": reason,
            "requested_remedy": remedy,
        }
    )
    existing_event = await _idempotent_event(
        session,
        review_case_id=review_case.id,
        idempotency_key_hash=key_hash,
        request_hash=request_hash,
    )
    if existing_event is not None:
        appeal_id = existing_event.details_json.get("appeal_id")
        appeal = (
            None
            if not isinstance(appeal_id, str)
            else await session.get(GovernanceAppeal, UUID(appeal_id))
        )
        if appeal is None:
            raise ReviewCaseError("appeal_not_found")
        return review_case, appeal
    if review_case.revision != expected_case_revision:
        raise ReviewCaseError("review_case_revision_conflict")
    if dispute.revision != expected_dispute_revision:
        raise ReviewCaseError("dispute_revision_conflict")
    if dispute.state != "resolved" or dispute.resolved_by_actor_id is None:
        raise ReviewCaseError("appeal_requires_resolved_dispute")
    if review_case.contributor_actor_id != actor_id and dispute.opened_by_actor_id != actor_id:
        raise ReviewCaseError("appeal_actor_not_eligible")
    review_case.state = _transition(review_case.state, ReviewEventType.APPEAL_OPENED).value
    review_case.revision += 1
    review_case.updated_at = now
    appeal = GovernanceAppeal(
        id=appeal_id_generator(),
        dispute_id=dispute.id,
        review_case_id=review_case.id,
        opened_by_actor_id=actor_id,
        original_deciding_actor_id=dispute.resolved_by_actor_id,
        public_reason=reason,
        requested_remedy=remedy,
        state="open",
        revision=1,
        opened_at=now,
    )
    session.add(appeal)
    _append_event(
        session,
        review_case=review_case,
        event_type=ReviewEventType.APPEAL_OPENED,
        actor_id=actor_id,
        public_reason=reason,
        now=now,
        idempotency_key_hash=key_hash,
        request_hash=request_hash,
        details={"appeal_id": str(appeal.id), "dispute_id": str(dispute.id)},
    )
    await session.flush()
    return review_case, appeal


async def resolve_appeal(
    session: AsyncSession,
    *,
    appeal_id: UUID,
    actor_id: UUID,
    expected_case_revision: int,
    expected_appeal_revision: int,
    idempotency_key: UUID,
    resolution: str,
    now: datetime,
) -> tuple[GovernanceReviewCase, GovernanceAppeal]:
    _require_aware(now)
    normalized_resolution = validate_reason(resolution)
    appeal = await _load_appeal(session, appeal_id, for_update=True)
    review_case = await _load_case(session, appeal.review_case_id, for_update=True)
    key_hash = _hash_idempotency_key(idempotency_key)
    request_hash = _request_hash(
        {
            "action": "resolve_appeal",
            "actor_id": actor_id,
            "expected_case_revision": expected_case_revision,
            "expected_appeal_revision": expected_appeal_revision,
            "resolution": normalized_resolution,
        }
    )
    if await _idempotent_event(
        session,
        review_case_id=review_case.id,
        idempotency_key_hash=key_hash,
        request_hash=request_hash,
    ):
        return review_case, appeal
    if review_case.revision != expected_case_revision:
        raise ReviewCaseError("review_case_revision_conflict")
    if appeal.revision != expected_appeal_revision:
        raise ReviewCaseError("appeal_revision_conflict")
    if appeal.state not in {"open", "reopened"}:
        raise ReviewCaseError("appeal_not_open")
    await _require_steward_can_review(session, review_case=review_case, actor_id=actor_id, now=now)
    if actor_id == appeal.original_deciding_actor_id:
        raise ReviewCaseError("appeal_requires_independent_steward")
    appeal.state = "resolved"
    appeal.revision += 1
    appeal.resolution = normalized_resolution
    appeal.decided_by_actor_id = actor_id
    appeal.resolved_at = now
    review_case.state = _transition(review_case.state, ReviewEventType.APPEAL_RESOLVED).value
    review_case.revision += 1
    review_case.updated_at = now
    _append_event(
        session,
        review_case=review_case,
        event_type=ReviewEventType.APPEAL_RESOLVED,
        actor_id=actor_id,
        public_reason=normalized_resolution,
        now=now,
        idempotency_key_hash=key_hash,
        request_hash=request_hash,
        details={"appeal_id": str(appeal.id)},
    )
    await session.flush()
    return review_case, appeal


async def list_disputes_and_appeals(
    session: AsyncSession,
    *,
    review_case_id: UUID,
) -> tuple[tuple[GovernanceDispute, ...], tuple[GovernanceAppeal, ...]]:
    disputes = tuple(
        await session.scalars(
            select(GovernanceDispute)
            .where(GovernanceDispute.review_case_id == review_case_id)
            .order_by(GovernanceDispute.opened_at.asc())
        )
    )
    appeals = tuple(
        await session.scalars(
            select(GovernanceAppeal)
            .where(GovernanceAppeal.review_case_id == review_case_id)
            .order_by(GovernanceAppeal.opened_at.asc())
        )
    )
    return disputes, appeals


__all__ = [
    "ReviewCaseError",
    "approve_review_case",
    "claim_review_case",
    "get_review_case_for_actor",
    "list_disputes_and_appeals",
    "list_review_cases_for_steward",
    "list_review_events",
    "open_review_case",
    "open_appeal",
    "open_dispute",
    "record_nonapproval_decision",
    "release_review_case",
    "resolve_appeal",
    "resolve_dispute",
]
