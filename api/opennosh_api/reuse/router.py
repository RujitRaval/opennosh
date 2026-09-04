from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Annotated, Never, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.auth.dependencies import (
    CurrentSession,
    get_app_settings,
    get_current_session,
    require_csrf,
)
from opennosh_api.database import get_database_session
from opennosh_api.public.artifacts import (
    ArtifactNotFoundError,
    ArtifactUnavailableError,
    PublicArtifactReadService,
)
from opennosh_api.reuse.contracts import (
    ReuseDeclarationCreate,
    ReuseDeclarationListResponse,
    ReuseDeclarationPatch,
    ReuseDeclarationResponse,
    ReuseDependencyInput,
    ReuseDependencyKind,
    ReuseEventType,
    ReusePublicDeclarationResponse,
    ReusePublicDependencyEdge,
    ReusePublicDependencyListResponse,
    ReusePublicEvidenceResponse,
    ReusePublicLabel,
    ReusePublicListResponse,
    ReuseRegionLevel,
    ReuseReviewDecisionRequest,
    ReuseReviewQueueResponse,
    ReuseTransitionRequest,
    ReuseVerificationEvidence,
    ReuseVerificationRequest,
)
from opennosh_api.reuse.models import ReuseDeclaration, ReuseDeclarationEvent, ReuseDependency
from opennosh_api.reuse.service import (
    ReuseRegistryError,
    create_declaration,
    list_owned_declarations,
    list_public_declarations,
    list_public_dependencies,
    list_reviewable_declarations,
    patch_declaration,
    read_owned_declaration,
    read_public_declaration,
    review_declaration,
    transition_declaration,
)
from opennosh_api.settings import Settings

router = APIRouter(prefix="/api/v1/reuse/declarations", tags=["reuse"])
governance_router = APIRouter(prefix="/api/v1/governance/reuse", tags=["governance"])
public_router = APIRouter(prefix="/api/v1/public/reuse", tags=["public-reuse"])


def _disabled() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="The requested resource was not found.",
    )


def require_registry_mutations(
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> None:
    if not settings.reuse_registry_mutations_enabled:
        raise _disabled()


def require_reuse_verification(
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> None:
    if not settings.reuse_verification_enabled:
        raise _disabled()


def require_public_reuse(
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> None:
    if not settings.reuse_public_enabled:
        raise _disabled()


def require_reuse_review_csrf(
    current: Annotated[CurrentSession, Depends(require_csrf)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> CurrentSession:
    if current.session.created_at < datetime.now(UTC) - timedelta(
        seconds=settings.governance_fresh_auth_seconds
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="fresh_auth_required",
        )
    return current


def _response(declaration: ReuseDeclaration) -> ReuseDeclarationResponse:
    return ReuseDeclarationResponse.model_validate(declaration)


def _no_store(response: Response, declaration: ReuseDeclaration | None = None) -> None:
    response.headers["Cache-Control"] = "no-store"
    if declaration is not None:
        response.headers["ETag"] = str(declaration.revision)


def _raise_registry_error(error: ReuseRegistryError) -> Never:
    if error.code in {"reuse_declaration_not_found", "reuse_audit_proof_unavailable"}:
        raise _disabled() from error
    if error.code == "reuse_steward_role_not_active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=error.code) from error
    if error.code == "reuse_dependency_proof_unavailable":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=error.code,
            headers={"Retry-After": "60"},
        ) from error
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=error.code) from error


def get_reuse_artifact_service(request: Request) -> PublicArtifactReadService:
    return cast(PublicArtifactReadService, request.app.state.public_artifact_read_service)


async def _dependency_is_verified(
    service: PublicArtifactReadService,
    dependency: ReuseDependencyInput,
) -> bool:
    try:
        release = await service.resolve_release(release_version=dependency.source_release_id)
    except ArtifactNotFoundError:
        return False
    except ArtifactUnavailableError as error:
        raise ReuseRegistryError("reuse_dependency_proof_unavailable") from error
    return any(
        pack.pack_id == dependency.source_pack_id
        and pack.download.digest == dependency.source_artifact_digest
        for pack in release.manifest.packs
    )


def _public_label(declaration: ReuseDeclaration) -> ReusePublicLabel:
    if declaration.state == "verified":
        return ReusePublicLabel.VERIFIED
    if declaration.state == "verification_pending":
        return ReusePublicLabel.UNVERIFIED
    return ReusePublicLabel.COMMUNITY_DECLARED


def _public_response(
    declaration: ReuseDeclaration,
    event: ReuseDeclarationEvent | None,
) -> ReusePublicDeclarationResponse:
    evidence = None
    if declaration.state == "verified":
        if event is None:
            raise ReuseRegistryError("reuse_audit_proof_unavailable")
        verified = ReuseVerificationEvidence.model_validate(event.evidence_json)
        evidence = ReusePublicEvidenceResponse(
            source_url=verified.source_url,
            observed_at=verified.observed_at,
            content_sha256=verified.content_sha256,
        )
    return ReusePublicDeclarationResponse(
        id=declaration.id,
        organization_name=declaration.organization_name,
        project_name=declaration.project_name,
        project_url=declaration.project_url,
        use_case=declaration.use_case,
        region_level=(
            None if declaration.region_level is None else ReuseRegionLevel(declaration.region_level)
        ),
        region_code=declaration.region_code,
        verification_label=_public_label(declaration),
        revision=declaration.revision,
        updated_at=declaration.updated_at,
        evidence=evidence,
    )


def _public_cache(response: Response, declaration: ReuseDeclaration | None = None) -> None:
    response.headers["Cache-Control"] = "public, max-age=60, stale-while-revalidate=300"
    if declaration is not None:
        response.headers["ETag"] = str(declaration.revision)


async def _commit_or_raise(database: AsyncSession) -> None:
    try:
        await database.commit()
    except IntegrityError as error:
        await database.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="reuse_registry_constraint_conflict",
        ) from error


@router.post(
    "",
    response_model=ReuseDeclarationResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_200_OK: {
            "model": ReuseDeclarationResponse,
            "description": "The idempotent declaration already exists.",
        }
    },
    dependencies=[Depends(require_registry_mutations)],
)
async def create_reuse_declaration(
    request: ReuseDeclarationCreate,
    response: Response,
    current: Annotated[CurrentSession, Depends(require_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
) -> ReuseDeclarationResponse:
    _no_store(response)
    try:
        declaration, created = await create_declaration(
            database,
            owner_actor_id=current.user_id,
            request=request,
            idempotency_key=idempotency_key,
            now=datetime.now(UTC),
        )
        await _commit_or_raise(database)
    except ReuseRegistryError as error:
        await database.rollback()
        _raise_registry_error(error)
    except IntegrityError as error:
        await database.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="reuse_registry_constraint_conflict",
        ) from error
    if not created:
        response.status_code = status.HTTP_200_OK
    _no_store(response, declaration)
    return _response(declaration)


@router.get(
    "/mine",
    response_model=ReuseDeclarationListResponse,
    dependencies=[Depends(require_registry_mutations)],
)
async def my_reuse_declarations(
    response: Response,
    current: Annotated[CurrentSession, Depends(get_current_session)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ReuseDeclarationListResponse:
    _no_store(response)
    declarations = await list_owned_declarations(
        database,
        owner_actor_id=current.user_id,
        limit=limit,
    )
    return ReuseDeclarationListResponse(
        declarations=tuple(_response(declaration) for declaration in declarations)
    )


@router.get(
    "/{declaration_id}",
    response_model=ReuseDeclarationResponse,
    dependencies=[Depends(require_registry_mutations)],
)
async def get_reuse_declaration(
    declaration_id: UUID,
    response: Response,
    current: Annotated[CurrentSession, Depends(get_current_session)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> ReuseDeclarationResponse:
    try:
        declaration = await read_owned_declaration(
            database,
            declaration_id=declaration_id,
            owner_actor_id=current.user_id,
        )
    except ReuseRegistryError as error:
        _raise_registry_error(error)
    _no_store(response, declaration)
    return _response(declaration)


@router.patch(
    "/{declaration_id}",
    response_model=ReuseDeclarationResponse,
    dependencies=[Depends(require_registry_mutations)],
)
async def edit_reuse_declaration(
    declaration_id: UUID,
    request: ReuseDeclarationPatch,
    response: Response,
    current: Annotated[CurrentSession, Depends(require_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    expected_revision: Annotated[int, Header(alias="If-Match", ge=1)],
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
) -> ReuseDeclarationResponse:
    try:
        declaration = await patch_declaration(
            database,
            declaration_id=declaration_id,
            owner_actor_id=current.user_id,
            expected_revision=expected_revision,
            request=request,
            idempotency_key=idempotency_key,
            now=datetime.now(UTC),
        )
        await _commit_or_raise(database)
    except ReuseRegistryError as error:
        await database.rollback()
        _raise_registry_error(error)
    except IntegrityError as error:
        await database.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="reuse_registry_constraint_conflict",
        ) from error
    _no_store(response, declaration)
    return _response(declaration)


async def _transition(
    *,
    declaration_id: UUID,
    request: ReuseTransitionRequest,
    response: Response,
    current: CurrentSession,
    database: AsyncSession,
    expected_revision: int,
    idempotency_key: UUID,
    action: ReuseEventType,
) -> ReuseDeclarationResponse:
    try:
        declaration = await transition_declaration(
            database,
            declaration_id=declaration_id,
            owner_actor_id=current.user_id,
            expected_revision=expected_revision,
            action=action,
            idempotency_key=idempotency_key,
            reason=request.reason,
            now=datetime.now(UTC),
        )
        await _commit_or_raise(database)
    except ReuseRegistryError as error:
        await database.rollback()
        _raise_registry_error(error)
    except IntegrityError as error:
        await database.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="reuse_registry_constraint_conflict",
        ) from error
    _no_store(response, declaration)
    return _response(declaration)


@router.post(
    "/{declaration_id}/submit",
    response_model=ReuseDeclarationResponse,
    dependencies=[Depends(require_registry_mutations)],
)
async def submit_reuse_declaration(
    declaration_id: UUID,
    request: ReuseTransitionRequest,
    response: Response,
    current: Annotated[CurrentSession, Depends(require_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    expected_revision: Annotated[int, Header(alias="If-Match", ge=1)],
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
) -> ReuseDeclarationResponse:
    return await _transition(
        declaration_id=declaration_id,
        request=request,
        response=response,
        current=current,
        database=database,
        expected_revision=expected_revision,
        idempotency_key=idempotency_key,
        action=ReuseEventType.SUBMITTED,
    )


@router.delete(
    "/{declaration_id}",
    response_model=ReuseDeclarationResponse,
    dependencies=[Depends(require_registry_mutations)],
)
async def withdraw_reuse_declaration(
    declaration_id: UUID,
    request: ReuseTransitionRequest,
    response: Response,
    current: Annotated[CurrentSession, Depends(require_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    expected_revision: Annotated[int, Header(alias="If-Match", ge=1)],
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
) -> ReuseDeclarationResponse:
    return await _transition(
        declaration_id=declaration_id,
        request=request,
        response=response,
        current=current,
        database=database,
        expected_revision=expected_revision,
        idempotency_key=idempotency_key,
        action=ReuseEventType.WITHDRAWN,
    )


@router.post(
    "/{declaration_id}/restore",
    response_model=ReuseDeclarationResponse,
    dependencies=[Depends(require_registry_mutations)],
)
async def restore_reuse_declaration(
    declaration_id: UUID,
    request: ReuseTransitionRequest,
    response: Response,
    current: Annotated[CurrentSession, Depends(require_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    expected_revision: Annotated[int, Header(alias="If-Match", ge=1)],
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
) -> ReuseDeclarationResponse:
    return await _transition(
        declaration_id=declaration_id,
        request=request,
        response=response,
        current=current,
        database=database,
        expected_revision=expected_revision,
        idempotency_key=idempotency_key,
        action=ReuseEventType.RESTORED,
    )


@governance_router.get(
    "/reviews",
    response_model=ReuseReviewQueueResponse,
    dependencies=[Depends(require_reuse_verification)],
)
async def reuse_review_queue(
    response: Response,
    current: Annotated[CurrentSession, Depends(get_current_session)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> ReuseReviewQueueResponse:
    _no_store(response)
    try:
        declarations = await list_reviewable_declarations(
            database,
            steward_actor_id=current.user_id,
            now=datetime.now(UTC),
            limit=100,
        )
    except ReuseRegistryError as error:
        _raise_registry_error(error)
    return ReuseReviewQueueResponse(
        declarations=tuple(_response(declaration) for declaration in declarations)
    )


async def _review_transition(
    *,
    declaration_id: UUID,
    response: Response,
    current: CurrentSession,
    database: AsyncSession,
    expected_revision: int,
    idempotency_key: UUID,
    action: ReuseEventType,
    reason: str,
    evidence: ReuseVerificationEvidence | None,
    dependencies: tuple[ReuseDependencyInput, ...] = (),
    artifact_service: PublicArtifactReadService | None = None,
) -> ReuseDeclarationResponse:
    try:
        declaration = await review_declaration(
            database,
            declaration_id=declaration_id,
            steward_actor_id=current.user_id,
            expected_revision=expected_revision,
            action=action,
            idempotency_key=idempotency_key,
            reason=reason,
            evidence=evidence,
            dependencies=dependencies,
            dependency_resolver=(
                None
                if artifact_service is None
                else lambda dependency: _dependency_is_verified(artifact_service, dependency)
            ),
            now=datetime.now(UTC),
        )
        await _commit_or_raise(database)
    except ReuseRegistryError as error:
        await database.rollback()
        _raise_registry_error(error)
    except IntegrityError as error:
        await database.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="reuse_registry_constraint_conflict",
        ) from error
    _no_store(response, declaration)
    return _response(declaration)


@governance_router.post(
    "/reviews/{declaration_id}/verify",
    response_model=ReuseDeclarationResponse,
    dependencies=[Depends(require_reuse_verification)],
)
async def verify_reuse_declaration(
    declaration_id: UUID,
    request: ReuseVerificationRequest,
    response: Response,
    current: Annotated[CurrentSession, Depends(require_reuse_review_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    artifact_service: Annotated[
        PublicArtifactReadService, Depends(get_reuse_artifact_service)
    ],
    expected_revision: Annotated[int, Header(alias="If-Match", ge=1)],
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
) -> ReuseDeclarationResponse:
    return await _review_transition(
        declaration_id=declaration_id,
        response=response,
        current=current,
        database=database,
        expected_revision=expected_revision,
        idempotency_key=idempotency_key,
        action=ReuseEventType.VERIFIED,
        reason=request.reason,
        evidence=request.evidence,
        dependencies=request.dependencies,
        artifact_service=artifact_service,
    )


async def _nonapproval_review(
    *,
    declaration_id: UUID,
    request: ReuseReviewDecisionRequest,
    response: Response,
    current: CurrentSession,
    database: AsyncSession,
    expected_revision: int,
    idempotency_key: UUID,
    action: ReuseEventType,
) -> ReuseDeclarationResponse:
    return await _review_transition(
        declaration_id=declaration_id,
        response=response,
        current=current,
        database=database,
        expected_revision=expected_revision,
        idempotency_key=idempotency_key,
        action=action,
        reason=request.reason,
        evidence=None,
    )


@governance_router.post(
    "/reviews/{declaration_id}/request-changes",
    response_model=ReuseDeclarationResponse,
    dependencies=[Depends(require_reuse_verification)],
)
async def request_reuse_changes(
    declaration_id: UUID,
    request: ReuseReviewDecisionRequest,
    response: Response,
    current: Annotated[CurrentSession, Depends(require_reuse_review_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    expected_revision: Annotated[int, Header(alias="If-Match", ge=1)],
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
) -> ReuseDeclarationResponse:
    return await _nonapproval_review(
        declaration_id=declaration_id,
        request=request,
        response=response,
        current=current,
        database=database,
        expected_revision=expected_revision,
        idempotency_key=idempotency_key,
        action=ReuseEventType.CHANGES_REQUESTED,
    )


@governance_router.post(
    "/reviews/{declaration_id}/reject",
    response_model=ReuseDeclarationResponse,
    dependencies=[Depends(require_reuse_verification)],
)
async def reject_reuse_declaration(
    declaration_id: UUID,
    request: ReuseReviewDecisionRequest,
    response: Response,
    current: Annotated[CurrentSession, Depends(require_reuse_review_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    expected_revision: Annotated[int, Header(alias="If-Match", ge=1)],
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
) -> ReuseDeclarationResponse:
    return await _nonapproval_review(
        declaration_id=declaration_id,
        request=request,
        response=response,
        current=current,
        database=database,
        expected_revision=expected_revision,
        idempotency_key=idempotency_key,
        action=ReuseEventType.REJECTED,
    )


@public_router.get(
    "",
    response_model=ReusePublicListResponse,
    dependencies=[Depends(require_public_reuse)],
)
async def public_reuse_registry(
    response: Response,
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> ReusePublicListResponse:
    _public_cache(response)
    declarations = await list_public_declarations(database)
    return ReusePublicListResponse(
        declarations=tuple(
            _public_response(declaration, event) for declaration, event in declarations
        )
    )


def _dependency_input(row: ReuseDependency) -> ReuseDependencyInput:
    return ReuseDependencyInput(
        source_pack_id=row.source_pack_id,
        source_release_id=row.source_release_id,
        source_artifact_digest=row.source_artifact_digest,
        dependency_kind=ReuseDependencyKind(row.dependency_kind),
    )


@public_router.get(
    "/dependencies",
    response_model=ReusePublicDependencyListResponse,
    dependencies=[Depends(require_public_reuse)],
)
async def public_reuse_dependencies(
    response: Response,
    database: Annotated[AsyncSession, Depends(get_database_session)],
    artifact_service: Annotated[
        PublicArtifactReadService, Depends(get_reuse_artifact_service)
    ],
) -> ReusePublicDependencyListResponse:
    rows = await list_public_dependencies(database)
    edges: list[ReusePublicDependencyEdge] = []
    for dependency, declaration, event in rows:
        proof = _dependency_input(dependency)
        try:
            valid = await _dependency_is_verified(artifact_service, proof)
        except ReuseRegistryError as error:
            _raise_registry_error(error)
        if not valid:
            _raise_registry_error(ReuseRegistryError("reuse_dependency_proof_unavailable"))
        evidence = ReuseVerificationEvidence.model_validate(event.evidence_json)
        edges.append(
            ReusePublicDependencyEdge(
                declaration_id=declaration.id,
                project_label=declaration.project_name,
                source_pack_id=dependency.source_pack_id,
                source_release_id=dependency.source_release_id,
                source_artifact_digest=dependency.source_artifact_digest,
                dependency_kind=ReuseDependencyKind(dependency.dependency_kind),
                evidence_observed_on=evidence.observed_at.date(),
            )
        )
    result = ReusePublicDependencyListResponse(dependencies=tuple(edges))
    payload = result.model_dump_json().encode()
    _public_cache(response)
    response.headers["ETag"] = f'"sha256-{hashlib.sha256(payload).hexdigest()}"'
    return result


@public_router.get(
    "/{declaration_id}",
    response_model=ReusePublicDeclarationResponse,
    dependencies=[Depends(require_public_reuse)],
)
async def public_reuse_declaration(
    declaration_id: UUID,
    response: Response,
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> ReusePublicDeclarationResponse:
    try:
        declaration, event = await read_public_declaration(
            database,
            declaration_id=declaration_id,
        )
    except ReuseRegistryError as error:
        _raise_registry_error(error)
    _public_cache(response, declaration)
    return _public_response(declaration, event)


__all__ = ["governance_router", "public_router", "router"]
