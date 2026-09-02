from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.auth.dependencies import get_app_settings
from opennosh_api.database import get_database_session
from opennosh_api.missions.public_service import (
    PublicMissionCatalog,
    public_mission_catalog,
    unavailable_public_mission_catalog,
)
from opennosh_api.missions.repository import MissionRepository
from opennosh_api.settings import Settings

router = APIRouter(prefix="/api/v1/public", tags=["public"])


@router.get("/missions", response_model=PublicMissionCatalog)
async def missions(
    response: Response,
    database: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> PublicMissionCatalog:
    if not settings.mission_public_enabled:
        catalog = await public_mission_catalog(
            MissionRepository(database),
            enabled=False,
            limit=limit,
        )
    else:
        try:
            catalog = await public_mission_catalog(
                MissionRepository(database),
                enabled=True,
                limit=limit,
            )
        except (SQLAlchemyError, ValueError, TypeError):
            catalog = unavailable_public_mission_catalog()
    response.headers["Cache-Control"] = (
        "no-store"
        if catalog.reason is not None
        else "public, max-age=0, s-maxage=60, stale-if-error=300"
    )
    return catalog


__all__ = ["router"]
