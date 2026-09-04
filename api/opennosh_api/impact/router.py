from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.auth.dependencies import get_app_settings
from opennosh_api.database import get_database_session
from opennosh_api.impact.contracts import PublicImpactSnapshot, unavailable_impact_snapshot
from opennosh_api.impact.service import latest_impact_snapshot
from opennosh_api.settings import Settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/public", tags=["public-impact"])


@router.get("/impact", response_model=PublicImpactSnapshot)
async def public_impact(
    response: Response,
    database: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> PublicImpactSnapshot:
    snapshot: PublicImpactSnapshot | None = None
    if not settings.impact_public_enabled:
        snapshot = unavailable_impact_snapshot("disabled")
    else:
        try:
            snapshot = await latest_impact_snapshot(database)
        except (SQLAlchemyError, ValueError, TypeError) as error:
            logger.warning(
                "Public impact proof unavailable",
                extra={"failure_code": type(error).__name__},
            )
        if snapshot is None:
            snapshot = unavailable_impact_snapshot("proof_unavailable")

    response.headers["Cache-Control"] = (
        "no-store"
        if snapshot.reason is not None
        else "public, max-age=0, s-maxage=300, stale-if-error=900"
    )
    response.headers["ETag"] = f'"{snapshot.digest}"'
    return snapshot


__all__ = ["router"]
