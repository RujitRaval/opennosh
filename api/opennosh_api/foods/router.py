from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.auth.dependencies import get_app_settings
from opennosh_api.auth.rate_limit import enforce_rate_limit
from opennosh_api.database import get_database_session
from opennosh_api.foods.open_food_facts import (
    OpenFoodFactsExportLimitError,
    OpenFoodFactsExportTimeoutError,
    cache_product,
    export_cached_products,
    get_cached_product,
)
from opennosh_api.foods.schemas import (
    FoodDetail,
    FoodSearchResponse,
    FoodSource,
    OpenFoodFactsExport,
    OpenFoodFactsFood,
)
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
from opennosh_api.integrations.open_food_facts import (
    OpenFoodFactsClient,
    OpenFoodFactsNotFoundError,
    OpenFoodFactsRateLimitedError,
    OpenFoodFactsTimeoutError,
    OpenFoodFactsUpstreamError,
    normalize_barcode,
)
from opennosh_api.settings import Settings

router = APIRouter(prefix="/api/v1/foods", tags=["foods"])
export_router = APIRouter(prefix="/api/v1/export", tags=["exports"])


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


@router.get("/barcode/{barcode}", response_model=OpenFoodFactsFood)
async def barcode_lookup(
    request: Request,
    database: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    barcode: Annotated[str, Path(min_length=8, max_length=14)],
) -> OpenFoodFactsFood:
    if not settings.open_food_facts_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Open Food Facts barcode lookup is disabled.",
        )
    try:
        normalized_barcode = normalize_barcode(barcode)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    client_address = request.client.host if request.client is not None else "unknown"
    await enforce_rate_limit(
        database,
        scope="open-food-facts-lookup-ip",
        key=client_address,
        attempts=settings.open_food_facts_lookup_rate_limit_attempts,
        window_seconds=settings.open_food_facts_lookup_rate_limit_window_seconds,
        retention_seconds=settings.auth_rate_limit_retention_seconds,
        detail="Too many barcode lookups. Try again later.",
    )
    cached = await get_cached_product(database, normalized_barcode)
    if cached is not None:
        return cached
    await database.rollback()
    await enforce_rate_limit(
        database,
        scope="open-food-facts-upstream-global",
        key="shared-egress",
        attempts=settings.open_food_facts_upstream_rate_limit_attempts,
        window_seconds=settings.open_food_facts_upstream_rate_limit_window_seconds,
        retention_seconds=settings.auth_rate_limit_retention_seconds,
        detail="Open Food Facts lookup capacity is exhausted. Try again later.",
    )
    client: OpenFoodFactsClient = request.app.state.open_food_facts_client
    try:
        product = await client.fetch(normalized_barcode)
    except OpenFoodFactsNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Barcode not found in Open Food Facts.",
        ) from error
    except OpenFoodFactsTimeoutError as error:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Open Food Facts lookup timed out.",
        ) from error
    except OpenFoodFactsRateLimitedError as error:
        retry_after = error.retry_after
        headers = (
            {"Retry-After": retry_after}
            if retry_after is not None
            and retry_after.isascii()
            and retry_after.isdigit()
            and 1 <= int(retry_after) <= 86_400
            else None
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Open Food Facts is rate limited. Try again later.",
            headers=headers,
        ) from error
    except OpenFoodFactsUpstreamError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Open Food Facts returned an unusable response.",
        ) from error
    return await cache_product(database, product)


@export_router.get("/foods/openfoodfacts", response_model=OpenFoodFactsExport)
async def open_food_facts_export(
    request: Request,
    database: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> OpenFoodFactsExport:
    client_address = request.client.host if request.client is not None else "unknown"
    await enforce_rate_limit(
        database,
        scope="open-food-facts-export-ip",
        key=client_address,
        attempts=settings.open_food_facts_export_rate_limit_attempts,
        window_seconds=settings.open_food_facts_export_rate_limit_window_seconds,
        retention_seconds=settings.auth_rate_limit_retention_seconds,
        detail="Too many Open Food Facts exports. Try again later.",
    )
    try:
        return await export_cached_products(
            database,
            statement_timeout_ms=settings.open_food_facts_export_statement_timeout_ms,
        )
    except OpenFoodFactsExportLimitError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The Open Food Facts cache is temporarily too large for JSON export.",
        ) from error
    except OpenFoodFactsExportTimeoutError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Open Food Facts export timed out. Try again later.",
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
