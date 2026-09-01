import asyncio
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
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
    EvidenceUploadAttachRequest,
    EvidenceUploadCompleteRequest,
    EvidenceUploadCreateRequest,
    EvidenceUploadCreateResponse,
    EvidenceUploadInstructionResponse,
    EvidenceUploadSessionResponse,
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
from opennosh_api.evidence.service import attach_sanitized_upload
from opennosh_api.evidence.storage import EvidenceUploadBroker
from opennosh_api.evidence.uploads import (
    EvidenceUploadConflictError,
    EvidenceUploadCreation,
    EvidenceUploadExpiredError,
    EvidenceUploadNotFoundError,
    EvidenceUploadPolicyError,
    EvidenceUploadQuotaError,
    EvidenceUploadSessionView,
    EvidenceUploadUnavailableError,
    complete_upload_session,
    create_upload_session,
    get_upload_session,
    validate_upload_declaration,
)
from opennosh_api.jobs.pgqueuer import PgQueuerJobQueue
from opennosh_api.problems.handlers import ProblemException
from opennosh_api.problems.schemas import ProblemCode, ProblemDetails
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
    public_state = None if record.public_state is None else EvidencePublicState(record.public_state)
    return ContributionEvidenceStatus(
        evidence_id=record.id,
        evidence_class=EvidenceClass(record.evidence_class),
        source_draft_version=record.source_draft_version,
        public_state=public_state,
        preservation_pending=(public_state is None and record.preservation_failure_code is None),
        preservation_failed=record.preservation_failure_code is not None,
        preservation_failure_code=record.preservation_failure_code,
    )


def get_evidence_upload_broker(request: Request) -> EvidenceUploadBroker | None:
    value = getattr(request.app.state, "evidence_upload_broker", None)
    return value if isinstance(value, EvidenceUploadBroker) else None


def _uploads_disabled() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="The requested resource was not found.",
    )


def _upload_problem(*, status_code: int, code: ProblemCode, detail: str) -> ProblemException:
    return ProblemException(status=status_code, code=code, detail=detail)


def _upload_session_response(view: EvidenceUploadSessionView) -> EvidenceUploadSessionResponse:
    return EvidenceUploadSessionResponse(
        upload_id=view.upload_id,
        state=view.state,
        source_draft_version=view.source_draft_version,
        media_type=view.declared_media_type,  # type: ignore[arg-type]
        declared_byte_length=view.declared_byte_length,
        observed_byte_length=view.observed_byte_length,
        observed_sha256=view.observed_sha256,
        expires_at=view.expires_at,
        uploaded_at=view.uploaded_at,
        failure_code=view.failure_code,
        evidence_id=view.evidence_id,
        sanitized_at=view.sanitized_at,
        attached_at=view.attached_at,
        preserved_at=view.preserved_at,
    )


def _upload_create_response(
    created: EvidenceUploadCreation, *, max_byte_length: int
) -> EvidenceUploadCreateResponse:
    instruction = created.instruction
    return EvidenceUploadCreateResponse(
        upload_id=created.session.upload_id,
        state=created.session.state,
        upload=(
            None
            if instruction is None
            else EvidenceUploadInstructionResponse(
                method=instruction.method,
                url=instruction.url,
                headers=dict(instruction.headers),
            )
        ),
        completion_capability=created.completion_capability,
        max_byte_length=max_byte_length,
        expires_at=created.session.expires_at,
    )


def _require_uploads(
    settings: Settings,
    broker: EvidenceUploadBroker | None,
) -> EvidenceUploadBroker:
    if not settings.evidence_uploads_enabled or not settings.evidence_sanitization_enabled:
        raise _uploads_disabled()
    if broker is None:
        raise _upload_problem(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code=ProblemCode.EVIDENCE_UPLOAD_UNAVAILABLE,
            detail="Evidence upload storage is temporarily unavailable.",
        )
    return broker


def require_evidence_uploads(
    settings: Annotated[Settings, Depends(get_app_settings)],
    broker: Annotated[EvidenceUploadBroker | None, Depends(get_evidence_upload_broker)],
) -> EvidenceUploadBroker:
    """Resolve the dormant surface before request validation errors are exposed."""

    return _require_uploads(settings, broker)


def get_evidence_observation_semaphore(request: Request) -> asyncio.Semaphore:
    value = getattr(request.app.state, "evidence_upload_observation_semaphore", None)
    if not isinstance(value, asyncio.Semaphore):
        raise RuntimeError("Evidence observation semaphore is not configured")
    return value


async def _enforce_evidence_upload_limits(
    database: AsyncSession,
    settings: Settings,
    *,
    user_id: UUID,
    draft_id: UUID,
    operation: str,
    account_attempts: int,
    draft_attempts: int,
) -> None:
    detail = "Too many evidence upload requests. Try again later."
    await enforce_rate_limit(
        database,
        scope=f"evidence-upload-{operation}-acct",
        key=str(user_id),
        attempts=account_attempts,
        window_seconds=settings.evidence_upload_rate_limit_window_seconds,
        retention_seconds=settings.auth_rate_limit_retention_seconds,
        detail=detail,
    )
    await enforce_rate_limit(
        database,
        scope=f"evidence-upload-{operation}-draft",
        key=f"{user_id}:{draft_id}",
        attempts=draft_attempts,
        window_seconds=settings.evidence_upload_rate_limit_window_seconds,
        retention_seconds=settings.auth_rate_limit_retention_seconds,
        detail=detail,
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
    settings: Annotated[Settings, Depends(get_app_settings)],
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
            open_governance_review=settings.governance_mutations_enabled,
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
        record = await get_evidence_status(database, draft_id=draft_id, user_id=current.user_id)
    except ContributionNotFoundError as error:
        raise _not_found() from error
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Typed evidence is not attached to this draft version.",
        )
    return _evidence_status(record)


@router.post(
    "/{draft_id}/evidence-uploads",
    response_model=EvidenceUploadCreateResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_200_OK: {
            "model": EvidenceUploadCreateResponse,
            "description": "The idempotent upload session already exists.",
        }
    },
)
async def create_evidence_upload(
    draft_id: UUID,
    payload: EvidenceUploadCreateRequest,
    response: Response,
    current: Annotated[CurrentSession, Depends(require_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    upload_broker: Annotated[EvidenceUploadBroker, Depends(require_evidence_uploads)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
) -> EvidenceUploadCreateResponse:
    _no_store(response)
    try:
        validate_upload_declaration(
            payload.media_type,
            payload.byte_length,
            max_bytes=settings.evidence_upload_max_bytes,
        )
        await _enforce_evidence_upload_limits(
            database,
            settings,
            user_id=current.user_id,
            draft_id=draft_id,
            operation="issue",
            account_attempts=settings.evidence_upload_issue_account_attempts,
            draft_attempts=settings.evidence_upload_issue_draft_attempts,
        )
        created = await create_upload_session(
            database,
            upload_broker,
            draft_id=draft_id,
            user_id=current.user_id,
            source_draft_version=payload.source_draft_version,
            media_type=payload.media_type,
            byte_length=payload.byte_length,
            idempotency_key=idempotency_key,
            now=datetime.now(UTC),
            ttl_seconds=settings.evidence_upload_ttl_seconds,
            max_bytes=settings.evidence_upload_max_bytes,
            outstanding_account_limit=(
                settings.evidence_upload_outstanding_account_limit
            ),
            outstanding_draft_limit=settings.evidence_upload_outstanding_draft_limit,
        )
    except EvidenceUploadNotFoundError as error:
        raise _uploads_disabled() from error
    except EvidenceUploadPolicyError as error:
        raise _upload_problem(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code=ProblemCode.VALIDATION_FAILED,
            detail="The evidence upload declaration exceeds the configured policy.",
        ) from error
    except EvidenceUploadConflictError as error:
        raise _upload_problem(
            status_code=status.HTTP_409_CONFLICT,
            code=ProblemCode.EVIDENCE_UPLOAD_CONFLICT,
            detail="The evidence upload conflicts with the latest draft state.",
        ) from error
    except EvidenceUploadUnavailableError as error:
        raise _upload_problem(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code=ProblemCode.EVIDENCE_UPLOAD_UNAVAILABLE,
            detail="Evidence upload storage is temporarily unavailable.",
        ) from error
    except EvidenceUploadQuotaError as error:
        raise ProblemException(
            status=status.HTTP_429_TOO_MANY_REQUESTS,
            code=ProblemCode.RATE_LIMITED,
            detail="Too many unfinished evidence uploads. Finish or wait for one to expire.",
            retry_after=60,
        ) from error
    if created.replayed:
        response.status_code = status.HTTP_200_OK
    return _upload_create_response(created, max_byte_length=settings.evidence_upload_max_bytes)


@router.post(
    "/{draft_id}/evidence-uploads/{upload_id}/complete",
    response_model=EvidenceUploadSessionResponse,
    responses={
        status.HTTP_410_GONE: {
            "model": ProblemDetails,
            "description": "The evidence upload capability has expired.",
        }
    },
)
async def complete_evidence_upload(
    draft_id: UUID,
    upload_id: UUID,
    payload: EvidenceUploadCompleteRequest,
    response: Response,
    current: Annotated[CurrentSession, Depends(require_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    upload_broker: Annotated[EvidenceUploadBroker, Depends(require_evidence_uploads)],
    observation_semaphore: Annotated[
        asyncio.Semaphore,
        Depends(get_evidence_observation_semaphore),
    ],
) -> EvidenceUploadSessionResponse:
    _no_store(response)
    try:
        await _enforce_evidence_upload_limits(
            database,
            settings,
            user_id=current.user_id,
            draft_id=draft_id,
            operation="complete",
            account_attempts=settings.evidence_upload_complete_account_attempts,
            draft_attempts=settings.evidence_upload_complete_draft_attempts,
        )
        session = await complete_upload_session(
            database,
            upload_broker,
            upload_id=upload_id,
            draft_id=draft_id,
            user_id=current.user_id,
            completion_capability=payload.completion_capability,
            now=datetime.now(UTC),
            max_bytes=settings.evidence_upload_max_bytes,
            queue=PgQueuerJobQueue(),
            observation_semaphore=observation_semaphore,
        )
    except EvidenceUploadNotFoundError as error:
        raise _uploads_disabled() from error
    except EvidenceUploadExpiredError as error:
        raise _upload_problem(
            status_code=status.HTTP_410_GONE,
            code=ProblemCode.EVIDENCE_UPLOAD_EXPIRED,
            detail="The evidence upload capability has expired.",
        ) from error
    except EvidenceUploadConflictError as error:
        raise _upload_problem(
            status_code=status.HTTP_409_CONFLICT,
            code=ProblemCode.EVIDENCE_UPLOAD_CONFLICT,
            detail="The evidence upload conflicts with the latest draft state.",
        ) from error
    except EvidenceUploadUnavailableError as error:
        raise _upload_problem(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code=ProblemCode.EVIDENCE_UPLOAD_UNAVAILABLE,
            detail="Evidence upload storage is temporarily unavailable.",
        ) from error
    return _upload_session_response(session)


@router.post(
    "/{draft_id}/evidence-uploads/{upload_id}/attach",
    response_model=EvidenceUploadSessionResponse,
)
async def attach_evidence_upload(
    draft_id: UUID,
    upload_id: UUID,
    payload: EvidenceUploadAttachRequest,
    response: Response,
    current: Annotated[CurrentSession, Depends(require_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    upload_broker: Annotated[EvidenceUploadBroker, Depends(require_evidence_uploads)],
) -> EvidenceUploadSessionResponse:
    _no_store(response)
    del upload_broker
    try:
        await _enforce_evidence_upload_limits(
            database,
            settings,
            user_id=current.user_id,
            draft_id=draft_id,
            operation="attach",
            account_attempts=settings.evidence_upload_attach_account_attempts,
            draft_attempts=settings.evidence_upload_attach_draft_attempts,
        )
        session = await attach_sanitized_upload(
            database,
            PgQueuerJobQueue(),
            upload_id=upload_id,
            draft_id=draft_id,
            user_id=current.user_id,
            source_draft_version=payload.source_draft_version,
            source_description=payload.source_description,
            rights_acknowledged=payload.rights_acknowledged,
            redaction_state=payload.redaction_state,
            now=datetime.now(UTC),
        )
    except EvidenceUploadNotFoundError as error:
        raise _uploads_disabled() from error
    except (EvidenceUploadConflictError, EvidenceConflictError) as error:
        raise _upload_problem(
            status_code=status.HTTP_409_CONFLICT,
            code=ProblemCode.EVIDENCE_UPLOAD_CONFLICT,
            detail="The evidence upload conflicts with the latest draft state.",
        ) from error
    return _upload_session_response(session)


@router.get(
    "/{draft_id}/evidence-uploads/{upload_id}",
    response_model=EvidenceUploadSessionResponse,
)
async def read_evidence_upload(
    draft_id: UUID,
    upload_id: UUID,
    response: Response,
    current: Annotated[CurrentSession, Depends(get_current_session)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    upload_broker: Annotated[EvidenceUploadBroker, Depends(require_evidence_uploads)],
) -> EvidenceUploadSessionResponse:
    _no_store(response)
    del upload_broker
    try:
        session = await get_upload_session(
            database,
            upload_id=upload_id,
            draft_id=draft_id,
            user_id=current.user_id,
            now=datetime.now(UTC),
        )
    except EvidenceUploadNotFoundError as error:
        raise _uploads_disabled() from error
    return _upload_session_response(session)
