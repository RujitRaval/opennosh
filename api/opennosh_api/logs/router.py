from datetime import date
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.auth.dependencies import CurrentSession, get_current_session, require_csrf
from opennosh_api.database import get_database_session
from opennosh_api.logs.schemas import (
    DailyTotalsResponse,
    LogEntryCreate,
    LogEntryListResponse,
    LogEntryResponse,
)
from opennosh_api.logs.service import (
    LOG_LIST_LIMIT_DEFAULT,
    LOG_LIST_LIMIT_MAX,
    LOG_LIST_OFFSET_MAX,
    TIMEZONE_MAX_LENGTH,
    FoodLogInputError,
    create_log_entry,
    daily_totals,
    delete_log_entry,
    get_log_entry,
    list_log_entries,
    resolve_timezone,
)

router = APIRouter(prefix="/api/v1/logs", tags=["food logs"])


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _timezone(name: str | None, current: CurrentSession) -> ZoneInfo:
    try:
        return resolve_timezone(name, current.user.settings_json)
    except FoodLogInputError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error


@router.post("", response_model=LogEntryResponse, status_code=status.HTTP_201_CREATED)
async def create(
    payload: LogEntryCreate,
    response: Response,
    current: Annotated[CurrentSession, Depends(require_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> LogEntryResponse:
    try:
        entry = await create_log_entry(database, payload, current)
    except FoodLogInputError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Food not found")
    _no_store(response)
    return entry


@router.get("", response_model=LogEntryListResponse)
async def list_entries(
    response: Response,
    current: Annotated[CurrentSession, Depends(get_current_session)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    day: Annotated[date, Query()],
    timezone: Annotated[str | None, Query(max_length=TIMEZONE_MAX_LENGTH)] = None,
    limit: Annotated[int, Query(ge=1, le=LOG_LIST_LIMIT_MAX)] = LOG_LIST_LIMIT_DEFAULT,
    offset: Annotated[int, Query(ge=0, le=LOG_LIST_OFFSET_MAX)] = 0,
) -> LogEntryListResponse:
    result = await list_log_entries(
        database,
        day=day,
        timezone=_timezone(timezone, current),
        current=current,
        limit=limit,
        offset=offset,
    )
    _no_store(response)
    return result


@router.get("/daily-totals", response_model=DailyTotalsResponse)
async def totals(
    response: Response,
    current: Annotated[CurrentSession, Depends(get_current_session)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    day: Annotated[date, Query()],
    timezone: Annotated[str | None, Query(max_length=TIMEZONE_MAX_LENGTH)] = None,
) -> DailyTotalsResponse:
    result = await daily_totals(
        database,
        day=day,
        timezone=_timezone(timezone, current),
        current=current,
    )
    _no_store(response)
    return result


@router.get("/{entry_id}", response_model=LogEntryResponse)
async def detail(
    entry_id: UUID,
    response: Response,
    current: Annotated[CurrentSession, Depends(get_current_session)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> LogEntryResponse:
    entry = await get_log_entry(database, entry_id, current)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Log entry not found")
    _no_store(response)
    return entry


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(
    entry_id: UUID,
    response: Response,
    current: Annotated[CurrentSession, Depends(require_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> None:
    if not await delete_log_entry(database, entry_id, current):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Log entry not found")
    _no_store(response)
