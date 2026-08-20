from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.auth.dependencies import (
    CurrentSession,
    get_app_settings,
    get_current_session,
    require_csrf,
)
from opennosh_api.database import get_database_session
from opennosh_api.models import TargetDayType
from opennosh_api.settings import Settings
from opennosh_api.targets.schemas import (
    TargetResponse,
    TargetScheduleResponse,
    TargetScheduleWrite,
)
from opennosh_api.targets.service import (
    TargetConfirmationRequired,
    list_targets,
    replace_targets,
    resolve_target,
)

router = APIRouter(prefix="/api/v1/targets", tags=["targets"])


@router.get("", response_model=TargetScheduleResponse)
async def list_all(
    current: Annotated[CurrentSession, Depends(get_current_session)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> TargetScheduleResponse:
    return await list_targets(
        database,
        current=current,
        target_kcal_floor=settings.target_kcal_floor,
    )


@router.put("", response_model=TargetScheduleResponse)
async def replace_all(
    payload: TargetScheduleWrite,
    current: Annotated[CurrentSession, Depends(require_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> TargetScheduleResponse:
    try:
        return await replace_targets(
            database,
            payload,
            current=current,
            target_kcal_floor=settings.target_kcal_floor,
        )
    except TargetConfirmationRequired as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error


@router.get("/resolve", response_model=TargetResponse)
async def resolve(
    current: Annotated[CurrentSession, Depends(get_current_session)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    day: Annotated[date, Query()],
    day_type: Annotated[TargetDayType, Query()],
) -> TargetResponse:
    target = await resolve_target(
        database,
        day=day,
        day_type=day_type,
        current=current,
        target_kcal_floor=settings.target_kcal_floor,
    )
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target not found")
    return target
