from typing import Annotated, Literal, cast

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from opennosh_api.settings import Settings

router = APIRouter(prefix="/api/v1/public", tags=["public"])


class BuildVersionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    version: Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$")]
    commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")] | None


@router.get("/build-version", response_model=BuildVersionResponse)
def build_version(request: Request, response: Response) -> BuildVersionResponse:
    """Expose the exact public API build without allowing intermediary caching."""

    settings = cast(Settings, request.app.state.settings)
    response.headers["Cache-Control"] = "no-store"
    if settings.render_git_commit is not None:
        response.headers["X-OpenNosh-Build-Commit"] = settings.render_git_commit
    return BuildVersionResponse(
        version=request.app.version,
        commit=settings.render_git_commit,
    )
