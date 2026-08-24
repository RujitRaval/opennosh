from hmac import compare_digest
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from opennosh_api.auth.client_address import PROXY_TOKEN_HEADER
from opennosh_api.database import DatabasePoolMetrics
from opennosh_api.public_commons.manifests import PublicCommonsSnapshotService
from opennosh_api.settings import Settings

router = APIRouter(prefix="/internal", tags=["operations"])


class DatabasePoolMetricsResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    deployment_id: str
    role: str
    pool_size: int
    active: int
    idle: int
    waiting: int
    timed_out_total: int
    acquisition_count: int
    acquisition_latency_ms_average: float
    acquisition_latency_ms_max: float


class PublicCommonsSnapshotMetricsResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)

    projection_reads: int
    projection_read_bytes: int
    projection_writes: int
    projection_write_bytes: int
    source_artifact_reads: int
    rebuilds: int
    stale_fallbacks: int
    unavailable_responses: int
    last_response_bytes: int


def require_operations_token(request: Request) -> None:
    settings: Settings = request.app.state.settings
    configured = settings.trusted_web_proxy_token
    supplied = request.headers.get(PROXY_TOKEN_HEADER)
    if (
        configured is None
        or supplied is None
        or not compare_digest(configured.get_secret_value(), supplied)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)


@router.get(
    "/metrics/database",
    response_model=DatabasePoolMetricsResponse,
    include_in_schema=False,
)
async def database_pool_metrics(
    request: Request,
    _authorized: Annotated[None, Depends(require_operations_token)],
) -> DatabasePoolMetricsResponse:
    metrics: DatabasePoolMetrics = request.app.state.database_pool_metrics
    return DatabasePoolMetricsResponse.model_validate(metrics.snapshot())


@router.get(
    "/metrics/public-commons",
    response_model=PublicCommonsSnapshotMetricsResponse,
    include_in_schema=False,
)
async def public_commons_snapshot_metrics(
    request: Request,
    _authorized: Annotated[None, Depends(require_operations_token)],
) -> PublicCommonsSnapshotMetricsResponse:
    service: PublicCommonsSnapshotService = request.app.state.public_commons_snapshot_service
    return PublicCommonsSnapshotMetricsResponse.model_validate(service.metrics)
