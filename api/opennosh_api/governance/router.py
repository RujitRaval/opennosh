from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, Never
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.auth.dependencies import (
    CurrentSession,
    get_app_settings,
    get_current_session,
    require_csrf,
)
from opennosh_api.database import get_database_session
from opennosh_api.governance.contracts import (
    ApprovedChangeSet,
    ApprovedFileChange,
    GovernanceDecisionOutcome,
)
from opennosh_api.governance.models import (
    GovernanceAppeal,
    GovernanceDecision,
    GovernanceDispute,
    GovernanceReviewCase,
    GovernanceReviewEvent,
)
from opennosh_api.governance.review_service import (
    ReviewCaseError,
    approve_review_case,
    claim_review_case,
    get_latest_review_case_for_contributor,
    get_review_case_for_actor,
    list_disputes_and_appeals,
    list_review_cases_for_steward,
    list_review_events,
    open_appeal,
    open_dispute,
    pause_review_case,
    record_nonapproval_decision,
    recuse_review_case,
    release_review_case,
    resolve_appeal,
    resolve_dispute,
    respond_to_changes_request,
    resume_review_case,
)
from opennosh_api.governance.reviews import DisputeCategory, ReviewCaseState, ReviewEventType
from opennosh_api.governance.schemas import (
    AppealOpenRequest,
    AppealResolveRequest,
    AppealResponse,
    DisputeOpenRequest,
    DisputeResolveRequest,
    DisputeResponse,
    PublicDecisionResponse,
    ReviewApprovalResponse,
    ReviewCaseAction,
    ReviewCaseApproval,
    ReviewCaseDecision,
    ReviewCasePause,
    ReviewCaseRecusal,
    ReviewCaseRelease,
    ReviewCaseResponse,
    ReviewCaseResume,
    ReviewDecisionResponse,
    ReviewEventResponse,
    ReviewQueueResponse,
    ReviewResponseRequest,
    ReviewResponseResult,
)
from opennosh_api.jobs.pgqueuer import PgQueuerJobQueue
from opennosh_api.publication.models import PublicationIntent
from opennosh_api.settings import Settings

router = APIRouter(prefix="/api/v1/governance", tags=["governance"])


def _disabled() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="The requested resource was not found.",
    )


def require_governance_ui(
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> None:
    if not settings.governance_steward_ui_enabled:
        raise _disabled()


def require_governance_mutations(
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> None:
    if not (settings.governance_steward_ui_enabled and settings.governance_mutations_enabled):
        raise _disabled()


def require_public_decisions(
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> None:
    if not settings.governance_public_decisions_enabled:
        raise _disabled()


def require_governance_csrf(
    current: Annotated[CurrentSession, Depends(require_csrf)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> CurrentSession:
    if current.session.created_at < datetime.now(UTC) - timedelta(
        seconds=settings.governance_fresh_auth_seconds
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="fresh_auth_required",
        )
    return current


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _event_response(event: GovernanceReviewEvent) -> ReviewEventResponse:
    return ReviewEventResponse(
        sequence=event.sequence,
        event_type=ReviewEventType(event.event_type),
        actor_id=event.actor_id,
        public_reason=event.public_reason,
        occurred_at=event.occurred_at,
    )


def _case_response(
    review_case: GovernanceReviewCase,
    *,
    viewer_role: Literal["contributor", "steward"],
    events: tuple[GovernanceReviewEvent, ...] = (),
    disputes: tuple[GovernanceDispute, ...] = (),
    appeals: tuple[GovernanceAppeal, ...] = (),
) -> ReviewCaseResponse:
    return ReviewCaseResponse(
        review_case_id=review_case.id,
        source_draft_id=review_case.source_draft_id,
        source_draft_version=review_case.source_draft_version,
        pack_id=review_case.pack_id,
        submitted_fields=review_case.submitted_fields_json,
        viewer_role=viewer_role,
        state=ReviewCaseState(review_case.state),
        revision=review_case.revision,
        assigned_steward_actor_id=review_case.assigned_steward_actor_id,
        acknowledged_at=review_case.acknowledged_at,
        pause_reason=review_case.pause_reason,
        next_review_at=review_case.next_review_at,
        opened_at=review_case.opened_at,
        updated_at=review_case.updated_at,
        closed_at=review_case.closed_at,
        events=[_event_response(event) for event in events],
        disputes=[_dispute_response(dispute) for dispute in disputes],
        appeals=[_appeal_response(appeal) for appeal in appeals],
    )


def _dispute_response(dispute: GovernanceDispute) -> DisputeResponse:
    return DisputeResponse(
        dispute_id=dispute.id,
        decision_id=dispute.decision_id,
        category=DisputeCategory(dispute.category),
        public_reason=dispute.public_reason,
        requested_remedy=dispute.requested_remedy,
        state=dispute.state,  # type: ignore[arg-type]
        revision=dispute.revision,
        opened_at=dispute.opened_at,
        resolution=dispute.resolution,
        resolved_at=dispute.resolved_at,
    )


def _appeal_response(appeal: GovernanceAppeal) -> AppealResponse:
    return AppealResponse(
        appeal_id=appeal.id,
        dispute_id=appeal.dispute_id,
        public_reason=appeal.public_reason,
        requested_remedy=appeal.requested_remedy,
        state=appeal.state,  # type: ignore[arg-type]
        revision=appeal.revision,
        opened_at=appeal.opened_at,
        resolution=appeal.resolution,
        resolved_at=appeal.resolved_at,
    )


async def _complete_case_response(
    database: AsyncSession,
    review_case: GovernanceReviewCase,
    *,
    actor_id: UUID,
) -> ReviewCaseResponse:
    events = await list_review_events(database, review_case_id=review_case.id)
    disputes, appeals = await list_disputes_and_appeals(database, review_case_id=review_case.id)
    return _case_response(
        review_case,
        viewer_role=(
            "contributor" if review_case.contributor_actor_id == actor_id else "steward"
        ),
        events=events,
        disputes=disputes,
        appeals=appeals,
    )


def _raise_review_error(error: ReviewCaseError) -> Never:
    if error.code in {
        "appeal_not_found",
        "contribution_not_found",
        "dispute_not_found",
        "review_decision_not_found",
        "review_case_not_found",
    }:
        raise _disabled() from error
    if error.code.startswith("evidence_") or error.code in {
        "review_case_revision_conflict",
        "idempotency_payload_mismatch",
        "appeal_not_open",
        "appeal_requires_resolved_dispute",
        "appeal_revision_conflict",
        "dispute_not_open",
        "dispute_requires_decision",
        "dispute_revision_conflict",
        "review_case_draft_version_stale",
        "contribution_not_in_review",
        "review_case_not_assigned_to_actor",
        "review_case_not_paused",
        "acknowledgement_evidence_mismatch",
        "acknowledgement_class_mismatch",
        "acknowledgement_manifest_digest_mismatch",
        "duplicate_acknowledgement_kind",
        "durable_acknowledgement_missing",
        "sanitized_media_digest_mismatch",
        "dataset_snapshot_digest_mismatch",
        "dataset_manifest_digest_mismatch",
        "document_archive_digest_mismatch",
        "citation_manifest_digest_mismatch",
        "attestation_manifest_digest_mismatch",
        "publication_paused",
        "review_approval_not_found",
        "review_response_not_found",
        "contribution_not_awaiting_response",
        "contribution_version_conflict",
        "approved_decision_requires_intervention",
        "contribution_not_reopenable",
        "governance_decision_already_succeeded",
        "prior_governance_decision_binding_mismatch",
        "prior_governance_decision_not_found",
        "prior_governance_decision_required",
    }:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=error.code) from error
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=error.code) from error


@router.get(
    "/review-cases",
    response_model=ReviewQueueResponse,
    dependencies=[Depends(require_governance_ui)],
)
async def review_queue(
    response: Response,
    pack_id: Annotated[str, Query(min_length=1, max_length=160)],
    current: Annotated[CurrentSession, Depends(get_current_session)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ReviewQueueResponse:
    _no_store(response)
    try:
        cases = await list_review_cases_for_steward(
            database,
            pack_id=pack_id,
            actor_id=current.user_id,
            now=datetime.now(UTC),
            limit=limit,
        )
    except ReviewCaseError as error:
        _raise_review_error(error)
    return ReviewQueueResponse(
        pack_id=pack_id,
        cases=[_case_response(review_case, viewer_role="steward") for review_case in cases],
    )


@router.get(
    "/public-decisions/{decision_id}",
    response_model=PublicDecisionResponse,
    dependencies=[Depends(require_public_decisions)],
)
async def public_decision(
    decision_id: UUID,
    response: Response,
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> PublicDecisionResponse:
    _no_store(response)
    decision = await database.get(GovernanceDecision, decision_id)
    if decision is None:
        raise _disabled()
    publication_state = await database.scalar(
        select(PublicationIntent.state).where(PublicationIntent.reviewed_decision_id == decision.id)
    )
    return PublicDecisionResponse(
        decision_id=decision.id,
        pack_id=decision.pack_id,
        source_draft_version=decision.source_draft_version,
        outcome=GovernanceDecisionOutcome(decision.outcome),
        reason=decision.reason,
        decided_at=decision.decided_at,
        publication_state=publication_state,
    )


@router.get(
    "/review-cases/{review_case_id}",
    response_model=ReviewCaseResponse,
    dependencies=[Depends(require_governance_ui)],
)
async def review_case_detail(
    review_case_id: UUID,
    response: Response,
    current: Annotated[CurrentSession, Depends(get_current_session)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> ReviewCaseResponse:
    _no_store(response)
    try:
        review_case = await get_review_case_for_actor(
            database,
            review_case_id=review_case_id,
            actor_id=current.user_id,
            now=datetime.now(UTC),
        )
        result = await _complete_case_response(database, review_case, actor_id=current.user_id)
    except ReviewCaseError as error:
        _raise_review_error(error)
    return result


@router.get(
    "/contributor/review-case",
    response_model=ReviewCaseResponse,
    dependencies=[Depends(require_governance_ui)],
)
async def contributor_review_case(
    response: Response,
    draft_id: Annotated[UUID, Query()],
    current: Annotated[CurrentSession, Depends(get_current_session)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> ReviewCaseResponse:
    _no_store(response)
    try:
        review_case = await get_latest_review_case_for_contributor(
            database,
            source_draft_id=draft_id,
            actor_id=current.user_id,
        )
        result = await _complete_case_response(database, review_case, actor_id=current.user_id)
    except ReviewCaseError as error:
        _raise_review_error(error)
    return result


@router.post(
    "/review-cases/{review_case_id}/claim",
    response_model=ReviewCaseResponse,
    dependencies=[Depends(require_governance_mutations)],
)
async def claim_case(
    review_case_id: UUID,
    payload: ReviewCaseAction,
    response: Response,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
    current: Annotated[CurrentSession, Depends(require_governance_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> ReviewCaseResponse:
    _no_store(response)
    try:
        review_case = await claim_review_case(
            database,
            review_case_id=review_case_id,
            actor_id=current.user_id,
            expected_revision=payload.expected_revision,
            idempotency_key=idempotency_key,
            now=datetime.now(UTC),
        )
        await database.commit()
        result = await _complete_case_response(database, review_case, actor_id=current.user_id)
    except ReviewCaseError as error:
        await database.rollback()
        _raise_review_error(error)
    return result


@router.post(
    "/review-cases/{review_case_id}/release",
    response_model=ReviewCaseResponse,
    dependencies=[Depends(require_governance_mutations)],
)
async def release_case(
    review_case_id: UUID,
    payload: ReviewCaseRelease,
    response: Response,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
    current: Annotated[CurrentSession, Depends(require_governance_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> ReviewCaseResponse:
    _no_store(response)
    try:
        review_case = await release_review_case(
            database,
            review_case_id=review_case_id,
            actor_id=current.user_id,
            expected_revision=payload.expected_revision,
            idempotency_key=idempotency_key,
            reason=payload.reason,
            now=datetime.now(UTC),
        )
        await database.commit()
        result = await _complete_case_response(database, review_case, actor_id=current.user_id)
    except ReviewCaseError as error:
        await database.rollback()
        _raise_review_error(error)
    return result


@router.post(
    "/review-cases/{review_case_id}/pause",
    response_model=ReviewCaseResponse,
    dependencies=[Depends(require_governance_mutations)],
)
async def pause_case(
    review_case_id: UUID,
    payload: ReviewCasePause,
    response: Response,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
    current: Annotated[CurrentSession, Depends(require_governance_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> ReviewCaseResponse:
    _no_store(response)
    try:
        review_case = await pause_review_case(
            database,
            review_case_id=review_case_id,
            actor_id=current.user_id,
            expected_revision=payload.expected_revision,
            idempotency_key=idempotency_key,
            reason=payload.reason,
            next_review_at=payload.next_review_at,
            now=datetime.now(UTC),
        )
        await database.commit()
        result = await _complete_case_response(database, review_case, actor_id=current.user_id)
    except ReviewCaseError as error:
        await database.rollback()
        _raise_review_error(error)
    except ValueError as error:
        await database.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    return result


@router.post(
    "/review-cases/{review_case_id}/resume",
    response_model=ReviewCaseResponse,
    dependencies=[Depends(require_governance_mutations)],
)
async def resume_case(
    review_case_id: UUID,
    payload: ReviewCaseResume,
    response: Response,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
    current: Annotated[CurrentSession, Depends(require_governance_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> ReviewCaseResponse:
    _no_store(response)
    try:
        review_case = await resume_review_case(
            database,
            review_case_id=review_case_id,
            actor_id=current.user_id,
            expected_revision=payload.expected_revision,
            idempotency_key=idempotency_key,
            reason=payload.reason,
            now=datetime.now(UTC),
        )
        await database.commit()
        result = await _complete_case_response(database, review_case, actor_id=current.user_id)
    except ReviewCaseError as error:
        await database.rollback()
        _raise_review_error(error)
    return result


@router.post(
    "/review-cases/{review_case_id}/recuse",
    response_model=ReviewCaseResponse,
    dependencies=[Depends(require_governance_mutations)],
)
async def recuse_from_case(
    review_case_id: UUID,
    payload: ReviewCaseRecusal,
    response: Response,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
    current: Annotated[CurrentSession, Depends(require_governance_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> ReviewCaseResponse:
    _no_store(response)
    try:
        review_case = await recuse_review_case(
            database,
            review_case_id=review_case_id,
            actor_id=current.user_id,
            expected_revision=payload.expected_revision,
            idempotency_key=idempotency_key,
            reason=payload.reason,
            now=datetime.now(UTC),
        )
        await database.commit()
        result = await _complete_case_response(database, review_case, actor_id=current.user_id)
    except ReviewCaseError as error:
        await database.rollback()
        _raise_review_error(error)
    return result


@router.post(
    "/review-cases/{review_case_id}/decision",
    response_model=ReviewDecisionResponse,
    dependencies=[Depends(require_governance_mutations)],
)
async def decide_case(
    review_case_id: UUID,
    payload: ReviewCaseDecision,
    response: Response,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
    current: Annotated[CurrentSession, Depends(require_governance_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> ReviewDecisionResponse:
    _no_store(response)
    try:
        review_case, decision = await record_nonapproval_decision(
            database,
            review_case_id=review_case_id,
            actor_id=current.user_id,
            outcome=payload.outcome,
            expected_revision=payload.expected_revision,
            idempotency_key=idempotency_key,
            reason=payload.reason,
            now=datetime.now(UTC),
        )
        await database.commit()
        result = await _complete_case_response(database, review_case, actor_id=current.user_id)
    except ReviewCaseError as error:
        await database.rollback()
        _raise_review_error(error)
    return ReviewDecisionResponse(
        review_case=result,
        decision_id=decision.id,
        outcome=payload.outcome,
        reason=decision.reason,
        decided_at=decision.decided_at,
    )


@router.post(
    "/review-cases/{review_case_id}/approve",
    response_model=ReviewApprovalResponse,
    dependencies=[Depends(require_governance_mutations)],
)
async def approve_case(
    review_case_id: UUID,
    payload: ReviewCaseApproval,
    response: Response,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
    current: Annotated[CurrentSession, Depends(require_governance_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> ReviewApprovalResponse:
    _no_store(response)
    try:
        approved_changes = ApprovedChangeSet.build(
            pack_id=payload.pack_id,
            files=tuple(
                ApprovedFileChange(path=item.path, content=item.content) for item in payload.files
            ),
        )
        review_case, decision, publication_intent = await approve_review_case(
            database,
            PgQueuerJobQueue(),
            review_case_id=review_case_id,
            actor_id=current.user_id,
            approved_changes=approved_changes,
            record_id=payload.record_id,
            expected_base_commit=payload.expected_base_commit,
            expected_revision=payload.expected_revision,
            idempotency_key=idempotency_key,
            reason=payload.reason,
            now=datetime.now(UTC),
        )
        await database.commit()
        result = await _complete_case_response(database, review_case, actor_id=current.user_id)
    except ReviewCaseError as error:
        await database.rollback()
        _raise_review_error(error)
    except ValueError as error:
        await database.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    return ReviewApprovalResponse(
        review_case=result,
        decision_id=decision.id,
        publication_intent_id=publication_intent.id,
    )


@router.post(
    "/review-cases/{review_case_id}/response",
    response_model=ReviewResponseResult,
    dependencies=[Depends(require_governance_mutations)],
)
async def respond_to_case(
    review_case_id: UUID,
    payload: ReviewResponseRequest,
    response: Response,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
    current: Annotated[CurrentSession, Depends(require_governance_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> ReviewResponseResult:
    _no_store(response)
    try:
        prior_case, next_case, draft = await respond_to_changes_request(
            database,
            review_case_id=review_case_id,
            actor_id=current.user_id,
            patches=tuple(payload.patches),
            expected_revision=payload.expected_revision,
            expected_draft_version=payload.expected_draft_version,
            idempotency_key=idempotency_key,
            public_reason=payload.public_reason,
            now=datetime.now(UTC),
        )
        await database.commit()
        prior_result = await _complete_case_response(
            database, prior_case, actor_id=current.user_id
        )
        next_result = await _complete_case_response(
            database, next_case, actor_id=current.user_id
        )
    except ReviewCaseError as error:
        await database.rollback()
        _raise_review_error(error)
    except ValueError as error:
        await database.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    return ReviewResponseResult(
        prior_review_case=prior_result,
        next_review_case=next_result,
        next_draft_version=draft.draft_version,
    )


@router.post(
    "/review-cases/{review_case_id}/disputes",
    response_model=ReviewCaseResponse,
    dependencies=[Depends(require_governance_mutations)],
)
async def dispute_case(
    review_case_id: UUID,
    payload: DisputeOpenRequest,
    response: Response,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
    current: Annotated[CurrentSession, Depends(require_governance_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> ReviewCaseResponse:
    _no_store(response)
    try:
        review_case, _dispute = await open_dispute(
            database,
            review_case_id=review_case_id,
            actor_id=current.user_id,
            category=payload.category,
            public_reason=payload.public_reason,
            requested_remedy=payload.requested_remedy,
            expected_revision=payload.expected_revision,
            idempotency_key=idempotency_key,
            now=datetime.now(UTC),
        )
        await database.commit()
        result = await _complete_case_response(database, review_case, actor_id=current.user_id)
    except ReviewCaseError as error:
        await database.rollback()
        _raise_review_error(error)
    return result


@router.post(
    "/disputes/{dispute_id}/resolve",
    response_model=ReviewCaseResponse,
    dependencies=[Depends(require_governance_mutations)],
)
async def resolve_case_dispute(
    dispute_id: UUID,
    payload: DisputeResolveRequest,
    response: Response,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
    current: Annotated[CurrentSession, Depends(require_governance_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> ReviewCaseResponse:
    _no_store(response)
    try:
        review_case, _dispute = await resolve_dispute(
            database,
            dispute_id=dispute_id,
            actor_id=current.user_id,
            expected_case_revision=payload.expected_case_revision,
            expected_dispute_revision=payload.expected_dispute_revision,
            idempotency_key=idempotency_key,
            resolution=payload.resolution,
            now=datetime.now(UTC),
        )
        await database.commit()
        result = await _complete_case_response(database, review_case, actor_id=current.user_id)
    except ReviewCaseError as error:
        await database.rollback()
        _raise_review_error(error)
    return result


@router.post(
    "/disputes/{dispute_id}/appeal",
    response_model=ReviewCaseResponse,
    dependencies=[Depends(require_governance_mutations)],
)
async def appeal_case_dispute(
    dispute_id: UUID,
    payload: AppealOpenRequest,
    response: Response,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
    current: Annotated[CurrentSession, Depends(require_governance_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> ReviewCaseResponse:
    _no_store(response)
    try:
        review_case, _appeal = await open_appeal(
            database,
            dispute_id=dispute_id,
            actor_id=current.user_id,
            expected_case_revision=payload.expected_case_revision,
            expected_dispute_revision=payload.expected_dispute_revision,
            idempotency_key=idempotency_key,
            public_reason=payload.public_reason,
            requested_remedy=payload.requested_remedy,
            now=datetime.now(UTC),
        )
        await database.commit()
        result = await _complete_case_response(database, review_case, actor_id=current.user_id)
    except ReviewCaseError as error:
        await database.rollback()
        _raise_review_error(error)
    return result


@router.post(
    "/appeals/{appeal_id}/resolve",
    response_model=ReviewCaseResponse,
    dependencies=[Depends(require_governance_mutations)],
)
async def resolve_case_appeal(
    appeal_id: UUID,
    payload: AppealResolveRequest,
    response: Response,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
    current: Annotated[CurrentSession, Depends(require_governance_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> ReviewCaseResponse:
    _no_store(response)
    try:
        review_case, _appeal = await resolve_appeal(
            database,
            appeal_id=appeal_id,
            actor_id=current.user_id,
            expected_case_revision=payload.expected_case_revision,
            expected_appeal_revision=payload.expected_appeal_revision,
            idempotency_key=idempotency_key,
            resolution=payload.resolution,
            now=datetime.now(UTC),
        )
        await database.commit()
        result = await _complete_case_response(database, review_case, actor_id=current.user_id)
    except ReviewCaseError as error:
        await database.rollback()
        _raise_review_error(error)
    return result


__all__ = ["router"]
