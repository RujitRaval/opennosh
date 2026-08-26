from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.auth.dependencies import (
    CurrentSession,
    get_app_settings,
    get_current_session,
    require_csrf,
)
from opennosh_api.auth.rate_limit import enforce_rate_limit
from opennosh_api.contributions.schemas import (
    ContributionCapability,
    ContributionDraftCreate,
    ContributionDraftPatch,
    ContributionEvidenceAttach,
    ContributionEvidenceStatus,
    ContributionSubmit,
)
from opennosh_api.contributions.service import (
    ContributionConflictError,
    ContributionNotFoundError,
    ContributionValidationError,
    attach_evidence,
    create_draft,
    get_draft,
    get_evidence_status,
    patch_draft,
    submit_draft,
)
from opennosh_api.database import get_database_session
from opennosh_api.evidence.contracts import EvidenceClass, EvidencePublicState
from opennosh_api.evidence.models import EvidenceManifestRecord
from opennosh_api.evidence.repository import EvidenceConflictError
from opennosh_api.jobs.pgqueuer import PgQueuerJobQueue
from opennosh_api.settings import Settings

router = APIRouter(prefix="/api/v1/contribution-drafts", tags=["contributions"])


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


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


def _evidence_status(record: EvidenceManifestRecord) -> ContributionEvidenceStatus:
    public_state = (
        None if record.public_state is None else EvidencePublicState(record.public_state)
    )
    return ContributionEvidenceStatus(
        evidence_id=record.id,
        evidence_class=EvidenceClass(record.evidence_class),
        source_draft_version=record.source_draft_version,
        public_state=public_state,
        preservation_pending=(
            public_state is None and record.preservation_failure_code is None
        ),
        preservation_failed=record.preservation_failure_code is not None,
        preservation_failure_code=record.preservation_failure_code,
    )


@router.post("", response_model=ContributionCapability, status_code=status.HTTP_201_CREATED)
async def create(
    payload: ContributionDraftCreate,
    response: Response,
    current: Annotated[CurrentSession, Depends(require_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> ContributionCapability:
    _no_store(response)
    return await create_draft(
        database, user_id=current.user_id, client_draft_id=payload.client_draft_id
    )


@router.get("/{draft_id}", response_model=ContributionCapability)
async def read(
    draft_id: UUID,
    response: Response,
    current: Annotated[CurrentSession, Depends(get_current_session)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    requested_stage: Annotated[str | None, Query(max_length=80)] = None,
) -> ContributionCapability:
    _no_store(response)
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
    response: Response,
    current: Annotated[CurrentSession, Depends(require_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> ContributionCapability:
    _no_store(response)
    try:
        await enforce_rate_limit(
            database,
            scope="contribution-patch-user",
            key=str(current.user_id),
            attempts=settings.contribution_patch_account_rate_limit_attempts,
            window_seconds=settings.contribution_patch_rate_limit_window_seconds,
            retention_seconds=settings.auth_rate_limit_retention_seconds,
            detail="Too many contribution updates. Keep editing; sync will retry shortly.",
        )
        await enforce_rate_limit(
            database,
            scope="contribution-patch-user-draft",
            key=f"{current.user_id}:{draft_id}",
            attempts=settings.contribution_patch_rate_limit_attempts,
            window_seconds=settings.contribution_patch_rate_limit_window_seconds,
            retention_seconds=settings.auth_rate_limit_retention_seconds,
            detail="Too many contribution updates. Keep editing; sync will retry shortly.",
        )
        return await patch_draft(
            database,
            draft_id=draft_id,
            user_id=current.user_id,
            payload=payload,
            operation_retention_seconds=settings.contribution_operation_retention_seconds,
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
    response: Response,
    current: Annotated[CurrentSession, Depends(require_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> ContributionCapability:
    _no_store(response)
    try:
        return await submit_draft(
            database,
            PgQueuerJobQueue(),
            draft_id=draft_id,
            user_id=current.user_id,
            payload=payload,
            now=datetime.now(UTC),
        )
    except ContributionNotFoundError as error:
        raise _not_found() from error
    except ContributionConflictError as error:
        raise _conflict(error) from error
    except ContributionValidationError as error:
        raise _invalid(error) from error
    except EvidenceConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@router.put("/{draft_id}/evidence", response_model=ContributionEvidenceStatus)
async def attach_typed_evidence(
    draft_id: UUID,
    payload: ContributionEvidenceAttach,
    response: Response,
    current: Annotated[CurrentSession, Depends(require_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> ContributionEvidenceStatus:
    _no_store(response)
    try:
        record = await attach_evidence(
            database,
            PgQueuerJobQueue(),
            draft_id=draft_id,
            user_id=current.user_id,
            expected_draft_version=payload.expected_draft_version,
            manifest=payload.manifest,
            now=datetime.now(UTC),
        )
        return _evidence_status(record)
    except ContributionNotFoundError as error:
        raise _not_found() from error
    except ContributionConflictError as error:
        raise _conflict(error) from error
    except ContributionValidationError as error:
        raise _invalid(error) from error
    except EvidenceConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@router.get("/{draft_id}/evidence", response_model=ContributionEvidenceStatus)
async def read_typed_evidence(
    draft_id: UUID,
    response: Response,
    current: Annotated[CurrentSession, Depends(get_current_session)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> ContributionEvidenceStatus:
    _no_store(response)
    try:
        record = await get_evidence_status(
            database, draft_id=draft_id, user_id=current.user_id
        )
    except ContributionNotFoundError as error:
        raise _not_found() from error
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Typed evidence is not attached to this draft version.",
        )
    return _evidence_status(record)
