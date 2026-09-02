import hashlib
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status

from opennosh_api.foods.schemas import FoodSource
from opennosh_api.problems.handlers import ProblemException
from opennosh_api.problems.schemas import ProblemCode
from opennosh_api.public.artifacts import (
    ArtifactNotFoundError,
    ArtifactUnavailableError,
    PublicArtifactReadService,
    PublicFoodRecordResponse,
    PublicReleaseMetadata,
)
from opennosh_api.public_commons.manifests import SignedEnvelope

router = APIRouter(prefix="/api/v1/public", tags=["public"])
_RELEASE = r"^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$"
_SOURCE_ID = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
_PACK_VERSION = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$"
_PROVENANCE_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:"
PublicFoodSource = Literal[FoodSource.USDA, FoodSource.COMMUNITY]


def get_artifact_read_service(request: Request) -> PublicArtifactReadService:
    return cast(PublicArtifactReadService, request.app.state.public_artifact_read_service)


def _release_headers(
    metadata: PublicReleaseMetadata, payload: bytes, *, immutable: bool
) -> dict[str, str]:
    headers = {
        "Cache-Control": (
            "public, max-age=31536000, immutable"
            if immutable
            else "public, max-age=0, s-maxage=60, stale-if-error=86400"
        ),
        "ETag": f'"sha256-{hashlib.sha256(payload).hexdigest()}"',
        "Vary": "Accept-Encoding",
        "X-OpenNosh-Release-Version": metadata.release_version,
        "X-OpenNosh-Release-State": metadata.state,
        "X-OpenNosh-Stale-Age": str(metadata.stale_age_seconds),
    }
    if metadata.state == "stale":
        headers["Warning"] = '110 - "Response is stale but remains cryptographically verified"'
    return headers


def _raise_public_error(error: Exception) -> None:
    if isinstance(error, ArtifactNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    raise ProblemException(
        status=status.HTTP_503_SERVICE_UNAVAILABLE,
        code=ProblemCode.SERVICE_UNAVAILABLE,
        detail="No verified public artifact is available.",
        retry_after=60,
    ) from error


@router.get("/foods/{source}/{source_id}", response_model=PublicFoodRecordResponse)
async def latest_food(
    source: PublicFoodSource,
    source_id: Annotated[str, Path(pattern=_SOURCE_ID)],
    service: Annotated[PublicArtifactReadService, Depends(get_artifact_read_service)],
    version: Annotated[str | None, Query(pattern=_RELEASE)] = None,
) -> Response:
    try:
        result = await service.food(source, source_id, release_version=version)
    except (ArtifactNotFoundError, ArtifactUnavailableError) as error:
        _raise_public_error(error)
    payload = result.model_dump_json().encode()
    return Response(
        content=payload,
        media_type="application/json",
        headers=_release_headers(result.release, payload, immutable=version is not None),
    )


@router.get(
    "/releases/{release_version}/foods/{source}/{source_id}",
    response_model=PublicFoodRecordResponse,
)
async def exact_food(
    release_version: Annotated[str, Path(pattern=_RELEASE)],
    source: PublicFoodSource,
    source_id: Annotated[str, Path(pattern=_SOURCE_ID)],
    service: Annotated[PublicArtifactReadService, Depends(get_artifact_read_service)],
) -> Response:
    try:
        result = await service.food(source, source_id, release_version=release_version)
    except (ArtifactNotFoundError, ArtifactUnavailableError) as error:
        _raise_public_error(error)
    payload = result.model_dump_json().encode()
    return Response(
        content=payload,
        media_type="application/json",
        headers=_release_headers(result.release, payload, immutable=True),
    )


@router.get(
    "/releases/{release_version}/foods/{source}/{source_id}/provenance",
    response_class=Response,
    responses={
        status.HTTP_200_OK: {
            "content": {"text/html": {"schema": {"type": "string"}}},
            "description": "Verified immutable provenance document.",
        }
    },
)
async def exact_provenance(
    release_version: Annotated[str, Path(pattern=_RELEASE)],
    source: PublicFoodSource,
    source_id: Annotated[str, Path(pattern=_SOURCE_ID)],
    service: Annotated[PublicArtifactReadService, Depends(get_artifact_read_service)],
) -> Response:
    try:
        payload, release = await service.provenance(
            source, source_id, release_version=release_version
        )
    except (ArtifactNotFoundError, ArtifactUnavailableError) as error:
        _raise_public_error(error)
    return Response(
        content=payload,
        media_type="text/html",
        headers={
            **_release_headers(release.metadata, payload, immutable=True),
            "Content-Security-Policy": _PROVENANCE_CSP,
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get(
    "/releases/{release_version}/manifest",
    response_class=Response,
    response_model=SignedEnvelope,
    responses={
        status.HTTP_200_OK: {
            "content": {
                "application/vnd.opennosh.release+json": {
                    "schema": {"$ref": "#/components/schemas/SignedEnvelope"}
                }
            },
            "description": "Canonical signed release manifest envelope.",
        }
    },
)
async def exact_manifest(
    release_version: Annotated[str, Path(pattern=_RELEASE)],
    service: Annotated[PublicArtifactReadService, Depends(get_artifact_read_service)],
) -> Response:
    try:
        payload, release = await service.signed_manifest(release_version)
    except (ArtifactNotFoundError, ArtifactUnavailableError) as error:
        _raise_public_error(error)
    return Response(
        content=payload,
        media_type="application/vnd.opennosh.release+json",
        headers=_release_headers(release.metadata, payload, immutable=True),
    )


@router.get(
    "/releases/{release_version}/packs/{pack_id}/{pack_version}/download",
    response_class=Response,
    responses={
        status.HTTP_200_OK: {
            "content": {
                "application/zip": {"schema": {"type": "string", "format": "binary"}},
                "application/vnd.opennosh.pack+zip": {
                    "schema": {"type": "string", "format": "binary"}
                },
            },
            "description": "Verified immutable pack download.",
        }
    },
)
async def exact_pack_download(
    release_version: Annotated[str, Path(pattern=_RELEASE)],
    pack_id: Annotated[str, Path(pattern=_SOURCE_ID)],
    pack_version: Annotated[str, Path(pattern=_PACK_VERSION)],
    service: Annotated[PublicArtifactReadService, Depends(get_artifact_read_service)],
) -> Response:
    try:
        payload, item, release = await service.pack(
            pack_id, pack_version, release_version=release_version
        )
    except (ArtifactNotFoundError, ArtifactUnavailableError) as error:
        _raise_public_error(error)
    return Response(
        content=payload,
        media_type=item.download.media_type,
        headers={
            **_release_headers(release.metadata, payload, immutable=True),
            "Content-Disposition": f'attachment; filename="{pack_id}-{pack_version}.zip"',
            "X-Content-Type-Options": "nosniff",
        },
    )
