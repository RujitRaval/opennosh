from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Never
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.auth.dependencies import (
    CurrentSession,
    get_app_settings,
    get_current_session,
    require_csrf,
)
from opennosh_api.database import get_database_session
from opennosh_api.reuse.contracts import (
    ReuseDeclarationCreate,
    ReuseDeclarationListResponse,
    ReuseDeclarationPatch,
    ReuseDeclarationResponse,
    ReuseEventType,
    ReuseTransitionRequest,
)
from opennosh_api.reuse.models import ReuseDeclaration
from opennosh_api.reuse.service import (
    ReuseRegistryError,
    create_declaration,
    list_owned_declarations,
    patch_declaration,
    read_owned_declaration,
    transition_declaration,
)
from opennosh_api.settings import Settings

router = APIRouter(prefix="/api/v1/reuse/declarations", tags=["reuse"])


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


def _response(declaration: ReuseDeclaration) -> ReuseDeclarationResponse:
    return ReuseDeclarationResponse.model_validate(declaration)


def _no_store(response: Response, declaration: ReuseDeclaration | None = None) -> None:
    response.headers["Cache-Control"] = "no-store"
    if declaration is not None:
        response.headers["ETag"] = str(declaration.revision)


def _raise_registry_error(error: ReuseRegistryError) -> Never:
    if error.code in {"reuse_declaration_not_found", "reuse_audit_proof_unavailable"}:
        raise _disabled() from error
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=error.code) from error


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


__all__ = ["router"]
