import hashlib
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Header, Request, Response, status
from fastapi.responses import JSONResponse

from opennosh_api.public_commons.manifests import PublicCommonsSnapshotService
from opennosh_api.public_commons.schemas import PublicCommonsSnapshot

router = APIRouter(prefix="/api/v1/public", tags=["public"])


def get_snapshot_service(request: Request) -> PublicCommonsSnapshotService:
    return cast(PublicCommonsSnapshotService, request.app.state.public_commons_snapshot_service)


@router.get(
    "/commons-snapshot",
    response_model=PublicCommonsSnapshot,
    responses={status.HTTP_304_NOT_MODIFIED: {"description": "Snapshot unchanged"}},
)
async def commons_snapshot(
    service: Annotated[PublicCommonsSnapshotService, Depends(get_snapshot_service)],
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    snapshot = await service.resolve()
    etag_value = hashlib.sha256(snapshot.model_dump_json().encode()).hexdigest()
    etag = f'"{etag_value}"'
    headers = {
        "Cache-Control": "public, max-age=0, s-maxage=300, stale-if-error=86400",
        "ETag": etag,
        "Vary": "Accept-Encoding",
    }
    if if_none_match == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    return JSONResponse(content=snapshot.model_dump(mode="json"), headers=headers)
