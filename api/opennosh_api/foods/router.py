import logging
from time import perf_counter
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.auth.client_address import client_address
from opennosh_api.auth.dependencies import CurrentSession, get_app_settings, require_csrf
from opennosh_api.auth.rate_limit import enforce_rate_limit
from opennosh_api.database import get_database_session
from opennosh_api.exports.router import _acquire_capacity
from opennosh_api.exports.streaming import ExportStreamingResponse
from opennosh_api.foods.cursors import (
    SEARCH_CURSOR_MAX_LENGTH,
    SearchCursorError,
    SearchCursorFailure,
    SearchCursorKeyRing,
)
from opennosh_api.foods.open_food_facts import (
    OpenFoodFactsExportLimitError,
    OpenFoodFactsExportTimeoutError,
    cache_product,
    get_cached_product,
    prepare_cached_product_export,
)
from opennosh_api.foods.schemas import (
    CustomFoodCreate,
    CustomFoodResponse,
    FoodCapabilities,
    FoodDetail,
    FoodSearchReadiness,
    FoodSearchResponse,
    FoodSource,
    OpenFoodFactsExport,
    OpenFoodFactsFood,
)
from opennosh_api.foods.service import (
    SEARCH_LIMIT_DEFAULT,
    SEARCH_LIMIT_MAX,
    SEARCH_QUERY_MAX_LENGTH,
    SEARCH_QUERY_MIN_LENGTH,
    FoodSearchProjectionBusyError,
    FoodSearchTimeoutError,
    create_custom_food,
    get_food_detail,
    normalize_locale,
    normalize_pack_ids,
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
from opennosh_api.problems.handlers import ProblemException
from opennosh_api.problems.schemas import ProblemCode, RecoveryAction
from opennosh_api.settings import Settings

router = APIRouter(prefix="/api/v1/foods", tags=["foods"])
export_router = APIRouter(prefix="/api/v1/export", tags=["exports"])
logger = logging.getLogger(__name__)

_READINESS_QUERY = "thepla"
_READINESS_EXPECTED_ID = "community:gujarati-plain-thepla"
_READINESS_EXPECTED_PACK = "gujarati-home-cooking"
_READINESS_EXPECTED_LICENSE = "CC0-1.0"
_READINESS_EXPECTED_SOURCE_LICENSE = "contributor-original"
_READINESS_EXPECTED_PROVENANCE = "published_recipe_calculation"


@router.get("/capabilities", response_model=FoodCapabilities)
async def capabilities(
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> FoodCapabilities:
    return FoodCapabilities(
        barcode_lookup_enabled=settings.open_food_facts_enabled,
        federation_search_enabled=settings.federation_search_enabled,
    )


@router.get(
    "/readiness",
    response_model=FoodSearchReadiness,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Search is unavailable."}},
)
async def readiness(
    request: Request,
    database: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> FoodSearchReadiness:
    started = perf_counter()
    try:
        result = await search_foods(
            database,
            query=_READINESS_QUERY,
            locale="en",
            source=FoodSource.COMMUNITY,
            limit=10,
            cursor=None,
            key_ring=SearchCursorKeyRing.from_secret(
                settings.food_search_cursor_signing_keys
            ),
            cursor_lifetime_seconds=settings.food_search_cursor_lifetime_seconds,
            snapshot_refresh_seconds=settings.food_search_snapshot_refresh_seconds,
            snapshot_retention_seconds=settings.food_search_snapshot_retention_seconds,
            snapshot_build_timeout_ms=settings.food_search_snapshot_build_timeout_ms,
            statement_timeout_ms=settings.food_search_statement_timeout_ms,
            federation_enabled=False,
        )
    except (FoodSearchProjectionBusyError, FoodSearchTimeoutError) as error:
        logger.warning(
            "food_search_readiness_failed request_id=%s error=%s",
            request.state.request_id,
            type(error).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Food search readiness failed.",
        ) from error

    expected = next((item for item in result.items if item.id == _READINESS_EXPECTED_ID), None)
    if (
        expected is None
        or expected.source is not FoodSource.COMMUNITY
        or expected.attribution.license != _READINESS_EXPECTED_LICENSE
        or expected.attribution.source_license != _READINESS_EXPECTED_SOURCE_LICENSE
        or expected.attribution.pack_id != _READINESS_EXPECTED_PACK
        or expected.attribution.provenance != _READINESS_EXPECTED_PROVENANCE
    ):
        logger.warning(
            "food_search_readiness_failed request_id=%s error=expected_record_missing",
            request.state.request_id,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Food search readiness failed.",
        )
    latency_ms = (perf_counter() - started) * 1_000
    return FoodSearchReadiness(
        query=_READINESS_QUERY,
        expected_id=_READINESS_EXPECTED_ID,
        latency_ms=latency_ms,
        result=expected,
    )


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
    pack: Annotated[list[str] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=SEARCH_LIMIT_MAX)] = SEARCH_LIMIT_DEFAULT,
    cursor: Annotated[
        str | None,
        Query(json_schema_extra={"maxLength": SEARCH_CURSOR_MAX_LENGTH}),
    ] = None,
) -> FoodSearchResponse:
    if "offset" in request.query_params:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Food search uses cursor pagination; offset is not supported.",
        )
    try:
        normalized_query = normalize_search_query(q)
        normalized_locale = normalize_locale(locale)
        normalized_pack_ids = normalize_pack_ids(pack)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    if (source is FoodSource.FEDERATION or normalized_pack_ids) and not (
        settings.federation_search_enabled
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Federated food search is disabled.",
        )
    rate_limit_key = client_address(request, settings)
    await enforce_rate_limit(
        database,
        scope="food-search-ip",
        key=rate_limit_key,
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
            cursor=cursor,
            key_ring=SearchCursorKeyRing.from_secret(settings.food_search_cursor_signing_keys),
            cursor_lifetime_seconds=settings.food_search_cursor_lifetime_seconds,
            snapshot_refresh_seconds=settings.food_search_snapshot_refresh_seconds,
            snapshot_retention_seconds=settings.food_search_snapshot_retention_seconds,
            snapshot_build_timeout_ms=settings.food_search_snapshot_build_timeout_ms,
            statement_timeout_ms=settings.food_search_statement_timeout_ms,
            federation_enabled=settings.federation_search_enabled,
            selected_pack_ids=normalized_pack_ids,
        )
    except SearchCursorError as error:
        first_page_params = {
            "q": normalized_query,
            "limit": str(limit),
            **({"locale": normalized_locale} if normalized_locale is not None else {}),
            **({"source": source.value} if source is not None else {}),
            **({"pack": list(normalized_pack_ids)} if normalized_pack_ids else {}),
        }
        restart = RecoveryAction(
            id="restart_search",
            label="Restart search",
            href=f"/api/v1/foods/search?{urlencode(first_page_params, doseq=True)}",
        )
        if error.failure is SearchCursorFailure.INVALID:
            raise ProblemException(
                status=status.HTTP_400_BAD_REQUEST,
                code=ProblemCode.SEARCH_CURSOR_INVALID,
                detail=error.detail,
                recovery_actions=(restart,),
            ) from error
        raise ProblemException(
            status=status.HTTP_409_CONFLICT,
            code=ProblemCode.SEARCH_CURSOR_RESTART,
            detail=error.detail,
            recovery_actions=(restart,),
        ) from error
    except FoodSearchProjectionBusyError as error:
        logger.warning(
            "food_search_projection_busy request_id=%s query_length=%d",
            request.state.request_id,
            len(normalized_query),
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Food search is being prepared. Try again shortly.",
        ) from error
    except FoodSearchTimeoutError as error:
        logger.warning(
            "food_search_timeout request_id=%s query_length=%d source=%s",
            request.state.request_id,
            len(normalized_query),
            source.value if source is not None else "all",
        )
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
    rate_limit_key = client_address(request, settings)
    await enforce_rate_limit(
        database,
        scope="open-food-facts-lookup-ip",
        key=rate_limit_key,
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


@router.post(
    "/custom",
    response_model=CustomFoodResponse,
    status_code=status.HTTP_201_CREATED,
)
async def custom_food_create(
    payload: CustomFoodCreate,
    current: Annotated[CurrentSession, Depends(require_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> CustomFoodResponse:
    return await create_custom_food(database, payload, current)


@export_router.get("/foods/odbl", response_model=OpenFoodFactsExport)
@export_router.get("/foods/openfoodfacts", response_model=OpenFoodFactsExport)
async def open_food_facts_export(
    request: Request,
    database: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> ExportStreamingResponse:
    rate_limit_key = client_address(request, settings)
    await enforce_rate_limit(
        database,
        scope="open-food-facts-export-ip",
        key=rate_limit_key,
        attempts=settings.open_food_facts_export_rate_limit_attempts,
        window_seconds=settings.open_food_facts_export_rate_limit_window_seconds,
        retention_seconds=settings.auth_rate_limit_retention_seconds,
        detail="Too many Open Food Facts exports. Try again later.",
    )
    lease = await _acquire_capacity(request, settings, private=False)
    try:
        body = await prepare_cached_product_export(
            database,
            statement_timeout_ms=settings.open_food_facts_export_statement_timeout_ms,
        )
    except OpenFoodFactsExportLimitError as error:
        lease.release()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The Open Food Facts cache is temporarily too large for JSON export.",
        ) from error
    except OpenFoodFactsExportTimeoutError as error:
        lease.release()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Open Food Facts export timed out. Try again later.",
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
            "Content-Disposition": ('attachment; filename="opennosh-open-food-facts-odbl.json"'),
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/{source}/{source_id}", response_model=FoodDetail)
async def detail(
    database: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    source: FoodSource,
    source_id: Annotated[
        str,
        Path(min_length=1, max_length=200, pattern=r"^[a-z0-9:-]+$"),
    ],
) -> FoodDetail:
    if source is FoodSource.FEDERATION and not settings.federation_search_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Federated food search is disabled.",
        )
    food = await get_food_detail(database, source, source_id)
    if food is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Food not found")
    return food
