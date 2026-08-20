from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.auth.client_address import client_address
from opennosh_api.auth.dependencies import get_app_settings
from opennosh_api.auth.rate_limit import enforce_rate_limit
from opennosh_api.database import get_database_session
from opennosh_api.exercises.schemas import ExerciseDetail, ExerciseExport, ExerciseSearchResponse
from opennosh_api.exercises.service import (
    SEARCH_FILTER_MAX_LENGTH,
    SEARCH_LIMIT_DEFAULT,
    SEARCH_LIMIT_MAX,
    SEARCH_OFFSET_MAX,
    SEARCH_QUERY_MAX_LENGTH,
    SEARCH_QUERY_MIN_LENGTH,
    ExerciseExportLimitError,
    ExerciseExportTimeoutError,
    ExerciseSearchTimeoutError,
    get_exercise,
    normalize_filter,
    normalize_search_query,
    prepare_exercise_export,
    search_exercises,
)
from opennosh_api.exports.router import _acquire_capacity
from opennosh_api.exports.streaming import ExportStreamingResponse
from opennosh_api.settings import Settings

router = APIRouter(prefix="/api/v1/exercises", tags=["exercises"])
export_router = APIRouter(prefix="/api/v1/export", tags=["exports"])


@router.get("/search", response_model=ExerciseSearchResponse)
async def search(
    request: Request,
    database: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    q: Annotated[
        str,
        Query(min_length=SEARCH_QUERY_MIN_LENGTH, max_length=SEARCH_QUERY_MAX_LENGTH),
    ],
    muscle: Annotated[str | None, Query(max_length=SEARCH_FILTER_MAX_LENGTH)] = None,
    equipment: Annotated[str | None, Query(max_length=SEARCH_FILTER_MAX_LENGTH)] = None,
    limit: Annotated[int, Query(ge=1, le=SEARCH_LIMIT_MAX)] = SEARCH_LIMIT_DEFAULT,
    offset: Annotated[int, Query(ge=0, le=SEARCH_OFFSET_MAX)] = 0,
) -> ExerciseSearchResponse:
    try:
        normalized_query = normalize_search_query(q)
        normalized_muscle = normalize_filter(muscle, label="muscle")
        normalized_equipment = normalize_filter(equipment, label="equipment")
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    rate_limit_key = client_address(request, settings)
    await enforce_rate_limit(
        database,
        scope="exercise-search-ip",
        key=rate_limit_key,
        attempts=settings.exercise_search_rate_limit_attempts,
        window_seconds=settings.exercise_search_rate_limit_window_seconds,
        retention_seconds=settings.auth_rate_limit_retention_seconds,
        detail="Too many exercise searches. Try again later.",
    )
    try:
        return await search_exercises(
            database,
            query=normalized_query,
            muscle=normalized_muscle,
            equipment=normalized_equipment,
            limit=limit,
            offset=offset,
            statement_timeout_ms=settings.exercise_search_statement_timeout_ms,
        )
    except ExerciseSearchTimeoutError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Exercise search timed out. Try a more specific query.",
        ) from error


@router.get("/{exercise_id}", response_model=ExerciseDetail)
async def detail(
    database: Annotated[AsyncSession, Depends(get_database_session)], exercise_id: UUID
) -> ExerciseDetail:
    exercise = await get_exercise(database, exercise_id)
    if exercise is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exercise not found")
    return exercise


@export_router.get("/exercises", response_model=ExerciseExport)
async def attributed_export(
    request: Request,
    database: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> ExportStreamingResponse:
    rate_limit_key = client_address(request, settings)
    await enforce_rate_limit(
        database,
        scope="exercise-export-ip",
        key=rate_limit_key,
        attempts=settings.exercise_export_rate_limit_attempts,
        window_seconds=settings.exercise_export_rate_limit_window_seconds,
        retention_seconds=settings.auth_rate_limit_retention_seconds,
        detail="Too many exercise exports. Try again later.",
    )
    lease = await _acquire_capacity(request, settings, private=False)
    try:
        body = await prepare_exercise_export(
            database, statement_timeout_ms=settings.exercise_export_statement_timeout_ms
        )
    except ExerciseExportLimitError as error:
        lease.release()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Exercise export is temporarily too large for the JSON endpoint.",
        ) from error
    except ExerciseExportTimeoutError as error:
        lease.release()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Exercise export timed out. Try again later.",
        ) from error
    except BaseException:
        lease.release()
        raise
    return ExportStreamingResponse(
        body,
        lease=lease,
        timeout_seconds=settings.public_export_response_timeout_seconds,
        media_type="application/json",
        headers={
            "Content-Disposition": 'attachment; filename="opennosh-wger-exercises.json"',
            "X-Content-Type-Options": "nosniff",
        },
    )
