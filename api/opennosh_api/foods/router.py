from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.auth.dependencies import get_app_settings
from opennosh_api.auth.rate_limit import enforce_rate_limit
from opennosh_api.database import get_database_session
from opennosh_api.foods.schemas import FoodDetail, FoodSearchResponse, FoodSource
from opennosh_api.foods.service import (
    SEARCH_LIMIT_DEFAULT,
    SEARCH_LIMIT_MAX,
    SEARCH_OFFSET_MAX,
    SEARCH_QUERY_MAX_LENGTH,
    SEARCH_QUERY_MIN_LENGTH,
    FoodSearchTimeoutError,
    get_food_detail,
    normalize_locale,
    normalize_search_query,
    search_foods,
)
from opennosh_api.settings import Settings

router = APIRouter(prefix="/api/v1/foods", tags=["foods"])


@router.get("/search", response_model=FoodSearchResponse)
async def search(
    request: Request,
    database: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    q: Annotated[
        str,
        Query(min_length=SEARCH_QUERY_MIN_LENGTH, max_length=SEARCH_QUERY_MAX_LENGTH),
    ],
    locale: Annotated[str | None, Query(max_length=35)] = None,
    source: Annotated[FoodSource | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=SEARCH_LIMIT_MAX)] = SEARCH_LIMIT_DEFAULT,
    offset: Annotated[int, Query(ge=0, le=SEARCH_OFFSET_MAX)] = 0,
) -> FoodSearchResponse:
    try:
        normalized_query = normalize_search_query(q)
        normalized_locale = normalize_locale(locale)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    client_address = request.client.host if request.client is not None else "unknown"
    await enforce_rate_limit(
        database,
        scope="food-search-ip",
        key=client_address,
        attempts=settings.food_search_rate_limit_attempts,
        window_seconds=settings.food_search_rate_limit_window_seconds,
        retention_seconds=settings.auth_rate_limit_retention_seconds,
        detail="Too many food searches. Try again later.",
    )
    try:
        return await search_foods(
            database,
            query=normalized_query,
            locale=normalized_locale,
            source=source,
            limit=limit,
            offset=offset,
            statement_timeout_ms=settings.food_search_statement_timeout_ms,
        )
    except FoodSearchTimeoutError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Food search timed out. Try a more specific query.",
        ) from error


@router.get("/{source}/{source_id}", response_model=FoodDetail)
async def detail(
    database: Annotated[AsyncSession, Depends(get_database_session)],
    source: FoodSource,
    source_id: Annotated[
        str,
        Path(min_length=1, max_length=160, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"),
    ],
) -> FoodDetail:
    food = await get_food_detail(database, source, source_id)
    if food is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Food not found")
    return food
