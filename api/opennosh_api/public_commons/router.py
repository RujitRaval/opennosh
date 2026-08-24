from typing import Annotated, cast

from fastapi import APIRouter, Depends, Header, Request, Response, status

from opennosh_api.public_commons.manifests import PublicCommonsSnapshotService
from opennosh_api.public_commons.schemas import PublicCommonsSnapshot

router = APIRouter(prefix="/api/v1/public", tags=["public"])


def get_snapshot_service(request: Request) -> PublicCommonsSnapshotService:
    return cast(PublicCommonsSnapshotService, request.app.state.public_commons_snapshot_service)


def _etag_matches(if_none_match: str | None, etag: str) -> bool:
    if if_none_match is None:
        return False
    candidates = (candidate.strip() for candidate in if_none_match.split(","))
    return any(candidate == "*" or candidate.removeprefix("W/") == etag for candidate in candidates)


@router.get(
    "/commons-snapshot",
    response_model=PublicCommonsSnapshot,
    responses={status.HTTP_304_NOT_MODIFIED: {"description": "Snapshot unchanged"}},
)
async def commons_snapshot(
    service: Annotated[PublicCommonsSnapshotService, Depends(get_snapshot_service)],
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    resolution = await service.resolve_response()
    headers = {
        "Cache-Control": (
            "public, max-age=0, s-maxage=300, stale-while-revalidate=60, "
            "stale-if-error=86400"
        ),
        "ETag": resolution.etag,
        "Server-Timing": f'public-commons;desc="{resolution.cache_status}"',
        "Vary": "Accept-Encoding",
        "X-OpenNosh-Snapshot-Bytes": str(resolution.response_bytes),
    }
    if _etag_matches(if_none_match, resolution.etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    return Response(
        content=resolution.snapshot.model_dump_json(),
        media_type="application/json",
        headers=headers,
    )
