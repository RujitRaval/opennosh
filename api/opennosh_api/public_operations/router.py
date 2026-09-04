from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import Annotated, Never, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.auth.dependencies import get_app_settings
from opennosh_api.database import get_database_session
from opennosh_api.public_operations.contracts import (
    PublicIncidentListResponse,
    PublicStatusResponse,
)
from opennosh_api.public_operations.manifest import PublicStatusManifest
from opennosh_api.public_operations.service import current_public_status, list_public_incidents
from opennosh_api.settings import Settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/public", tags=["public-operations"])


def require_public_status(
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> None:
    if not settings.public_status_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


def get_public_status_manifest(request: Request) -> PublicStatusManifest:
    return cast(PublicStatusManifest, request.app.state.public_status_manifest)


def _unavailable(error: Exception) -> Never:
    logger.warning(
        "Public operations evidence unavailable",
        extra={"failure_code": type(error).__name__},
    )
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="public_operations_evidence_unavailable",
        headers={"Cache-Control": "no-store", "Retry-After": "60"},
    ) from error


def _cache(response: Response, payload: bytes) -> None:
    response.headers["Cache-Control"] = "public, max-age=30, stale-while-revalidate=120"
    response.headers["ETag"] = f'"sha256-{hashlib.sha256(payload).hexdigest()}"'


@router.get(
    "/status",
    response_model=PublicStatusResponse,
    dependencies=[Depends(require_public_status)],
)
async def public_status(
    response: Response,
    database: Annotated[AsyncSession, Depends(get_database_session)],
    manifest: Annotated[PublicStatusManifest, Depends(get_public_status_manifest)],
) -> PublicStatusResponse:
    try:
        result = await current_public_status(database, manifest=manifest, now=datetime.now(UTC))
    except (SQLAlchemyError, ValidationError, TypeError, ValueError) as error:
        _unavailable(error)
    _cache(response, result.model_dump_json().encode())
    return result


@router.get(
    "/incidents",
    response_model=PublicIncidentListResponse,
    dependencies=[Depends(require_public_status)],
)
async def public_incidents(
    response: Response,
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> PublicIncidentListResponse:
    try:
        result = await list_public_incidents(database)
    except (SQLAlchemyError, ValidationError, TypeError, ValueError) as error:
        _unavailable(error)
    _cache(response, result.model_dump_json().encode())
    return result


__all__ = [
    "get_public_status_manifest",
    "public_incidents",
    "public_status",
    "require_public_status",
    "router",
]
