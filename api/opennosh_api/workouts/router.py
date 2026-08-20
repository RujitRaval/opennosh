from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.auth.dependencies import CurrentSession, get_current_session, require_csrf
from opennosh_api.database import get_database_session
from opennosh_api.models import LoadUnit
from opennosh_api.workouts.constants import (
    WORKOUT_LIST_LIMIT_DEFAULT,
    WORKOUT_LIST_LIMIT_MAX,
    WORKOUT_LIST_OFFSET_MAX,
)
from opennosh_api.workouts.schemas import (
    WorkoutCreate,
    WorkoutListResponse,
    WorkoutResponse,
    WorkoutSetWrite,
    WorkoutUpdate,
    WorkoutVolumeResponse,
)
from opennosh_api.workouts.service import (
    ExerciseNotFound,
    WorkoutInputError,
    WorkoutNotFound,
    add_workout_set,
    create_workout,
    delete_workout,
    delete_workout_set,
    get_workout,
    list_workouts,
    update_workout,
    update_workout_set,
    workout_volume,
)

router = APIRouter(prefix="/api/v1/workouts", tags=["workouts"])


def _input_error(error: WorkoutInputError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error))


def _nested_not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workout or set not found")


@router.post("", response_model=WorkoutResponse, status_code=status.HTTP_201_CREATED)
async def create(
    payload: WorkoutCreate,
    current: Annotated[CurrentSession, Depends(require_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> WorkoutResponse:
    try:
        return await create_workout(database, payload, current)
    except ExerciseNotFound as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error


@router.get("", response_model=WorkoutListResponse)
async def list_all(
    current: Annotated[CurrentSession, Depends(get_current_session)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    from_date: Annotated[date, Query(alias="from")],
    to_date: Annotated[date, Query(alias="to")],
    limit: Annotated[int, Query(ge=1, le=WORKOUT_LIST_LIMIT_MAX)] = WORKOUT_LIST_LIMIT_DEFAULT,
    offset: Annotated[int, Query(ge=0, le=WORKOUT_LIST_OFFSET_MAX)] = 0,
) -> WorkoutListResponse:
    try:
        return await list_workouts(
            database,
            from_date=from_date,
            to_date=to_date,
            current=current,
            limit=limit,
            offset=offset,
        )
    except WorkoutInputError as error:
        raise _input_error(error) from error


@router.get("/volume", response_model=WorkoutVolumeResponse)
async def volume(
    current: Annotated[CurrentSession, Depends(get_current_session)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    from_date: Annotated[date, Query(alias="from")],
    to_date: Annotated[date, Query(alias="to")],
    exercise_id: Annotated[UUID, Query()],
    load_unit: Annotated[LoadUnit | None, Query()] = None,
) -> WorkoutVolumeResponse:
    try:
        return await workout_volume(
            database,
            from_date=from_date,
            to_date=to_date,
            exercise_id=exercise_id,
            requested_unit=load_unit,
            current=current,
        )
    except WorkoutInputError as error:
        raise _input_error(error) from error


@router.get("/{workout_id}", response_model=WorkoutResponse)
async def detail(
    workout_id: UUID,
    current: Annotated[CurrentSession, Depends(get_current_session)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> WorkoutResponse:
    workout = await get_workout(database, workout_id, current)
    if workout is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workout not found")
    return workout


@router.put("/{workout_id}", response_model=WorkoutResponse)
async def update(
    workout_id: UUID,
    payload: WorkoutUpdate,
    current: Annotated[CurrentSession, Depends(require_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> WorkoutResponse:
    workout = await update_workout(database, workout_id, payload, current)
    if workout is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workout not found")
    return workout


@router.delete("/{workout_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_one(
    workout_id: UUID,
    current: Annotated[CurrentSession, Depends(require_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> None:
    if not await delete_workout(database, workout_id, current):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workout not found")


@router.post("/{workout_id}/sets", response_model=WorkoutResponse)
async def add_set(
    workout_id: UUID,
    payload: WorkoutSetWrite,
    current: Annotated[CurrentSession, Depends(require_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> WorkoutResponse:
    try:
        return await add_workout_set(database, workout_id, payload, current)
    except WorkoutNotFound as error:
        raise _nested_not_found() from error
    except ExerciseNotFound as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except WorkoutInputError as error:
        raise _input_error(error) from error


@router.put("/{workout_id}/sets/{set_id}", response_model=WorkoutResponse)
async def update_set(
    workout_id: UUID,
    set_id: UUID,
    payload: WorkoutSetWrite,
    current: Annotated[CurrentSession, Depends(require_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> WorkoutResponse:
    try:
        return await update_workout_set(database, workout_id, set_id, payload, current)
    except WorkoutNotFound as error:
        raise _nested_not_found() from error
    except ExerciseNotFound as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error


@router.delete("/{workout_id}/sets/{set_id}", response_model=WorkoutResponse)
async def delete_set(
    workout_id: UUID,
    set_id: UUID,
    current: Annotated[CurrentSession, Depends(require_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> WorkoutResponse:
    try:
        return await delete_workout_set(database, workout_id, set_id, current)
    except WorkoutNotFound as error:
        raise _nested_not_found() from error
