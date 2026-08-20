import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.auth.dependencies import (
    CurrentSession,
    get_app_settings,
    get_current_session,
)
from opennosh_api.auth.rate_limit import enforce_rate_limit
from opennosh_api.database import get_database_session
from opennosh_api.exports.schemas import CommunityFoodExport, PrivateDataExport
from opennosh_api.exports.service import (
    CommunityExportLimitError,
    ExportTimeoutError,
    prepare_community_export,
    prepare_private_export,
)
from opennosh_api.exports.streaming import (
    ExportCapacityError,
    ExportStreamingResponse,
    acquire_export_capacity,
)
from opennosh_api.settings import Settings

router = APIRouter(prefix="/api/v1/export", tags=["exports"])


def _download_headers(filename: str) -> dict[str, str]:
    return {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Content-Type-Options": "nosniff",
    }


async def _acquire_capacity(
    request: Request, settings: Settings, *, private: bool
) -> asyncio.Semaphore:
    semaphore: asyncio.Semaphore = (
        request.app.state.private_export_semaphore
        if private
        else request.app.state.public_export_semaphore
    )
    try:
        await acquire_export_capacity(
            semaphore, wait_seconds=settings.export_capacity_wait_seconds
        )
    except ExportCapacityError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Export capacity is busy. Try again later.",
        ) from error
    return semaphore


@router.get("/me", response_model=PrivateDataExport)
async def private_export(
    request: Request,
    database: Annotated[AsyncSession, Depends(get_database_session)],
    current: Annotated[CurrentSession, Depends(get_current_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> ExportStreamingResponse:
    await enforce_rate_limit(
        database,
        scope="private-export-user",
        key=str(current.user_id),
        attempts=settings.private_export_rate_limit_attempts,
        window_seconds=settings.private_export_rate_limit_window_seconds,
        retention_seconds=settings.auth_rate_limit_retention_seconds,
        detail="Too many private data exports. Try again later.",
    )
    lease = await _acquire_capacity(request, settings, private=True)
    try:
        body = await prepare_private_export(
            database,
            current=current,
            statement_timeout_ms=settings.private_export_statement_timeout_ms,
        )
    except ExportTimeoutError as error:
        lease.release()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Private data export timed out. Try again later.",
        ) from error
    except BaseException:
        lease.release()
        raise
    headers = _download_headers("opennosh-private-data.json")
    headers["Cache-Control"] = "no-store"
    return ExportStreamingResponse(
        body,
        lease=lease,
        timeout_seconds=settings.private_export_response_timeout_seconds,
        media_type="application/json",
        headers=headers,
    )


@router.get("/foods/community", response_model=CommunityFoodExport)
async def community_export(
    request: Request,
    database: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> ExportStreamingResponse:
    client_address = request.client.host if request.client is not None else "unknown"
    await enforce_rate_limit(
        database,
        scope="community-food-export-ip",
        key=client_address,
        attempts=settings.community_export_rate_limit_attempts,
        window_seconds=settings.community_export_rate_limit_window_seconds,
        retention_seconds=settings.auth_rate_limit_retention_seconds,
        detail="Too many community-food exports. Try again later.",
    )
    lease = await _acquire_capacity(request, settings, private=False)
    try:
        body = await prepare_community_export(
            database,
            statement_timeout_ms=settings.community_export_statement_timeout_ms,
        )
    except CommunityExportLimitError as error:
        lease.release()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The community-food catalogue is temporarily too large for export.",
        ) from error
    except ExportTimeoutError as error:
        lease.release()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Community-food export timed out. Try again later.",
        ) from error
    except BaseException:
        lease.release()
        raise
    return ExportStreamingResponse(
        body,
        lease=lease,
        timeout_seconds=settings.public_export_response_timeout_seconds,
        media_type="application/json",
        headers=_download_headers("opennosh-community-foods-cc0.json"),
    )
