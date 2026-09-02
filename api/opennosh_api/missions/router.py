import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated, Never
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.auth.dependencies import (
    CurrentSession,
    get_app_settings,
    require_csrf,
)
from opennosh_api.database import get_database_session
from opennosh_api.missions.activity_service import (
    PublicMissionActivityMap,
    public_mission_activity_map,
    unavailable_public_mission_activity_map,
)
from opennosh_api.missions.contracts import (
    MissionLifecycleAction,
    MissionLifecycleResponse,
    MissionPauseTransitionRequest,
    MissionProposalRequest,
    MissionReleaseTransitionRequest,
    MissionTransitionRequest,
)
from opennosh_api.missions.models import MissionLifecycleEvent
from opennosh_api.missions.policy import MissionLifecycleError
from opennosh_api.missions.public_service import (
    PublicMissionCatalog,
    public_mission_catalog,
    unavailable_public_mission_catalog,
)
from opennosh_api.missions.repository import MissionRepository
from opennosh_api.missions.service import (
    ProposeMission,
    TransitionMission,
    lifecycle_state,
    propose_mission,
    transition_mission,
)
from opennosh_api.settings import Settings

router = APIRouter(prefix="/api/v1/public", tags=["public"])
logger = logging.getLogger(__name__)
organizer_router = APIRouter(prefix="/api/v1/missions", tags=["missions"])


def _disabled() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="The requested resource was not found.",
    )


def require_mission_mutations(
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> None:
    if not (settings.mission_public_enabled and settings.mission_mutations_enabled):
        raise _disabled()


def require_mission_steward_csrf(
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


def _lifecycle_response(event: MissionLifecycleEvent) -> MissionLifecycleResponse:
    return MissionLifecycleResponse(
        mission_id=event.mission_id,
        definition_id=event.definition_id,
        event_id=event.id,
        sequence=event.sequence,
        action=MissionLifecycleAction(event.action),
        state=lifecycle_state(event),
        public_reason=event.public_reason,
        next_review_at=event.next_review_at,
        release_receipt_digest=event.release_receipt_digest,
        occurred_at=event.occurred_at,
    )


def _raise_mission_error(error: MissionLifecycleError) -> Never:
    if error.code in {
        "mission_actor_not_active_steward",
        "mission_definition_not_found",
        "mission_not_found",
        "mission_release_receipt_not_found",
    }:
        raise _disabled() from error
    if error.code in {
        "mission_acceptance_target_not_met",
        "mission_already_exists",
        "mission_definition_not_current",
        "mission_event_time_invalid",
        "mission_idempotency_conflict",
        "mission_next_review_not_future",
        "mission_progress_stale",
        "mission_progress_unavailable",
        "mission_release_receipt_invalid",
        "mission_revision_conflict",
        "mission_transition_not_allowed",
    }:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=error.code) from error
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=error.code) from error


@router.get("/missions", response_model=PublicMissionCatalog)
async def missions(
    response: Response,
    database: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> PublicMissionCatalog:
    if not settings.mission_public_enabled:
        catalog = await public_mission_catalog(
            MissionRepository(database),
            enabled=False,
            limit=limit,
        )
    else:
        try:
            catalog = await public_mission_catalog(
                MissionRepository(database),
                enabled=True,
                limit=limit,
            )
        except (SQLAlchemyError, ValueError, TypeError):
            catalog = unavailable_public_mission_catalog()
    response.headers["Cache-Control"] = (
        "no-store"
        if catalog.reason is not None
        else "public, max-age=0, s-maxage=60, stale-if-error=300"
    )
    return catalog


@router.get("/missions/activity", response_model=PublicMissionActivityMap)
async def mission_activity(
    response: Response,
    database: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> PublicMissionActivityMap:
    if not settings.mission_activity_map_enabled:
        activity = await public_mission_activity_map(
            MissionRepository(database),
            enabled=False,
        )
    else:
        try:
            activity = await public_mission_activity_map(
                MissionRepository(database),
                enabled=True,
            )
        except (SQLAlchemyError, ValueError, TypeError) as error:
            failure_code = (
                str(error)
                if isinstance(error, ValueError)
                and str(error).startswith("public_mission_activity_")
                else type(error).__name__
            )
            logger.warning(
                "Public mission activity proof unavailable",
                extra={"failure_code": failure_code},
            )
            activity = unavailable_public_mission_activity_map()
    response.headers["Cache-Control"] = (
        "no-store"
        if activity.reason is not None
        else "public, max-age=0, s-maxage=60, stale-if-error=300"
    )
    return activity


@organizer_router.post(
    "",
    response_model=MissionLifecycleResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_mission_mutations)],
)
async def propose(
    request: MissionProposalRequest,
    response: Response,
    current: Annotated[CurrentSession, Depends(require_mission_steward_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> MissionLifecycleResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        _definition, event = await propose_mission(
            MissionRepository(database),
            ProposeMission(
                mission_id=request.mission_id,
                definition_id=request.definition_id,
                event_id=request.event_id,
                actor_id=current.user_id,
                responsible_steward_actor_id=request.responsible_steward_actor_id,
                definition=request.definition,
                public_reason=request.public_reason,
            ),
            now=datetime.now(UTC),
        )
        await database.commit()
    except MissionLifecycleError as error:
        await database.rollback()
        _raise_mission_error(error)
    return _lifecycle_response(event)


@organizer_router.post(
    "/{mission_id}/transitions",
    response_model=MissionLifecycleResponse,
    dependencies=[Depends(require_mission_mutations)],
)
async def transition(
    mission_id: UUID,
    request: MissionTransitionRequest,
    response: Response,
    current: Annotated[CurrentSession, Depends(require_mission_steward_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> MissionLifecycleResponse:
    if (
        request.action is MissionLifecycleAction.COMPLETE
        and not settings.mission_projection_enabled
    ) or (
        request.action is MissionLifecycleAction.RELEASE
        and not settings.mission_pack_release_enabled
    ):
        raise _disabled()
    response.headers["Cache-Control"] = "no-store"
    next_review_at = (
        request.next_review_at if isinstance(request, MissionPauseTransitionRequest) else None
    )
    release_receipt_digest = (
        request.release_receipt_digest
        if isinstance(request, MissionReleaseTransitionRequest)
        else None
    )
    try:
        event = await transition_mission(
            MissionRepository(database),
            TransitionMission(
                mission_id=mission_id,
                definition_id=request.definition_id,
                event_id=request.event_id,
                expected_prior_event_id=request.expected_prior_event_id,
                actor_id=current.user_id,
                action=request.action,
                public_reason=request.public_reason,
                next_review_at=next_review_at,
                release_receipt_digest=release_receipt_digest,
            ),
            now=datetime.now(UTC),
        )
        await database.commit()
    except MissionLifecycleError as error:
        await database.rollback()
        _raise_mission_error(error)
    return _lifecycle_response(event)


__all__ = ["organizer_router", "router"]
