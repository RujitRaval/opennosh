import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel

from opennosh_api.database import DatabaseHealthProbe, get_database_probe

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["connected", "unavailable"]
    seed: Literal["not_started"] = "not_started"


@router.get(
    "/healthz",
    response_model=HealthResponse,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": HealthResponse,
            "description": "The API is running but its database is unavailable.",
        }
    },
)
async def healthcheck(
    response: Response,
    database_probe: Annotated[DatabaseHealthProbe, Depends(get_database_probe)],
) -> HealthResponse:
    try:
        await database_probe.check()
    except Exception:
        logger.exception("Database health check failed")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(status="degraded", database="unavailable")

    return HealthResponse(status="ok", database="connected")
