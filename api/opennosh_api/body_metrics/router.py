from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.auth.dependencies import CurrentSession, get_current_session, require_csrf
from opennosh_api.body_metrics.constants import (
    BODY_METRIC_LIST_LIMIT_DEFAULT,
    BODY_METRIC_LIST_LIMIT_MAX,
    BODY_METRIC_LIST_OFFSET_MAX,
)
from opennosh_api.body_metrics.schemas import (
    BodyMetricListResponse,
    BodyMetricResponse,
    BodyMetricTrendResponse,
    BodyMetricWrite,
)
from opennosh_api.body_metrics.service import (
    BodyMetricInputError,
    body_metric_trends,
    create_body_metric,
    delete_body_metric,
    list_body_metrics,
)
from opennosh_api.database import get_database_session

router = APIRouter(prefix="/api/v1/body-metrics", tags=["body metrics"])


@router.post("", response_model=BodyMetricResponse, status_code=status.HTTP_201_CREATED)
async def create(
    payload: BodyMetricWrite,
    current: Annotated[CurrentSession, Depends(require_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> BodyMetricResponse:
    return await create_body_metric(database, payload, current)


@router.get("", response_model=BodyMetricListResponse)
async def list_all(
    current: Annotated[CurrentSession, Depends(get_current_session)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    from_date: Annotated[date, Query(alias="from")],
    to_date: Annotated[date, Query(alias="to")],
    limit: Annotated[
        int, Query(ge=1, le=BODY_METRIC_LIST_LIMIT_MAX)
    ] = BODY_METRIC_LIST_LIMIT_DEFAULT,
    offset: Annotated[int, Query(ge=0, le=BODY_METRIC_LIST_OFFSET_MAX)] = 0,
) -> BodyMetricListResponse:
    try:
        return await list_body_metrics(
            database,
            from_date=from_date,
            to_date=to_date,
            current=current,
            limit=limit,
            offset=offset,
        )
    except BodyMetricInputError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error


@router.get("/trends", response_model=BodyMetricTrendResponse)
async def trends(
    current: Annotated[CurrentSession, Depends(get_current_session)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    from_date: Annotated[date, Query(alias="from")],
    to_date: Annotated[date, Query(alias="to")],
) -> BodyMetricTrendResponse:
    try:
        return await body_metric_trends(
            database,
            from_date=from_date,
            to_date=to_date,
            current=current,
        )
    except BodyMetricInputError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error


@router.delete("/{metric_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(
    metric_id: UUID,
    current: Annotated[CurrentSession, Depends(require_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> None:
    if not await delete_body_metric(database, metric_id, current):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Body metric not found",
        )
