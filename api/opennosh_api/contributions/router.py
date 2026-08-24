from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.auth.dependencies import CurrentSession, get_current_session, require_csrf
from opennosh_api.contributions.schemas import (
    ContributionCapability,
    ContributionDraftCreate,
    ContributionDraftPatch,
    ContributionSubmit,
)
from opennosh_api.contributions.service import (
    ContributionConflictError,
    ContributionNotFoundError,
    ContributionValidationError,
    create_draft,
    get_draft,
    patch_draft,
    submit_draft,
)
from opennosh_api.database import get_database_session

router = APIRouter(prefix="/api/v1/contribution-drafts", tags=["contributions"])


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail="Contribution draft not found."
    )


def _conflict(error: ContributionConflictError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"The contribution changed. Latest draft version: {error.capability.draft_version}.",
    )


def _invalid(error: ContributionValidationError) -> HTTPException:
    first = error.blockers[0].message if error.blockers else "The contribution is not ready."
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=first)


@router.post("", response_model=ContributionCapability, status_code=status.HTTP_201_CREATED)
async def create(
    payload: ContributionDraftCreate,
    current: Annotated[CurrentSession, Depends(require_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> ContributionCapability:
    return await create_draft(
        database, user_id=current.user_id, client_draft_id=payload.client_draft_id
    )


@router.get("/{draft_id}", response_model=ContributionCapability)
async def read(
    draft_id: UUID,
    current: Annotated[CurrentSession, Depends(get_current_session)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    requested_stage: Annotated[str | None, Query(max_length=80)] = None,
) -> ContributionCapability:
    try:
        return await get_draft(
            database,
            draft_id=draft_id,
            user_id=current.user_id,
            requested_stage=requested_stage,
        )
    except ContributionNotFoundError as error:
        raise _not_found() from error


@router.patch("/{draft_id}", response_model=ContributionCapability)
async def patch(
    draft_id: UUID,
    payload: ContributionDraftPatch,
    current: Annotated[CurrentSession, Depends(require_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> ContributionCapability:
    try:
        return await patch_draft(
            database, draft_id=draft_id, user_id=current.user_id, payload=payload
        )
    except ContributionNotFoundError as error:
        raise _not_found() from error
    except ContributionConflictError as error:
        raise _conflict(error) from error
    except ContributionValidationError as error:
        raise _invalid(error) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error


@router.post("/{draft_id}/submit", response_model=ContributionCapability)
async def submit(
    draft_id: UUID,
    payload: ContributionSubmit,
    current: Annotated[CurrentSession, Depends(require_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> ContributionCapability:
    try:
        return await submit_draft(
            database, draft_id=draft_id, user_id=current.user_id, payload=payload
        )
    except ContributionNotFoundError as error:
        raise _not_found() from error
    except ContributionConflictError as error:
        raise _conflict(error) from error
    except ContributionValidationError as error:
        raise _invalid(error) from error
