import asyncio
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.auth.client_address import client_address
from opennosh_api.auth.dependencies import (
    CurrentSession,
    get_app_settings,
    get_current_session,
    require_csrf,
)
from opennosh_api.auth.passwords import (
    hash_password,
    perform_dummy_verification,
    verify_password,
)
from opennosh_api.auth.rate_limit import enforce_auth_rate_limit
from opennosh_api.auth.schemas import AuthenticatedUser, Credentials, SessionResponse
from opennosh_api.auth.tokens import generate_token, hash_token
from opennosh_api.database import get_database_session
from opennosh_api.models import AuthSession, User
from opennosh_api.settings import Settings

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])


def _set_session_cookies(
    response: Response,
    *,
    session_token: str,
    csrf_token: str,
    settings: Settings,
) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        session_token,
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        settings.csrf_cookie_name,
        csrf_token,
        secure=settings.session_cookie_secure,
        httponly=False,
        samesite="strict",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"


def _clear_session_cookies(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        settings.session_cookie_name,
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
        path="/",
    )
    response.delete_cookie(
        settings.csrf_cookie_name,
        secure=settings.session_cookie_secure,
        httponly=False,
        samesite="strict",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"


async def _create_session(
    database: AsyncSession,
    *,
    user: User,
    settings: Settings,
) -> tuple[str, str]:
    session_token = generate_token()
    csrf_token = generate_token()
    database.add(
        AuthSession(
            user_id=user.id,
            token_hash=hash_token(session_token),
            csrf_token_hash=hash_token(csrf_token),
            expires_at=datetime.now(UTC) + timedelta(seconds=settings.session_lifetime_seconds),
        )
    )
    await database.commit()
    return session_token, csrf_token


def _session_response(user: User, csrf_token: str) -> SessionResponse:
    return SessionResponse(
        user=AuthenticatedUser(id=user.id, email=user.email),
        csrf_token=csrf_token,
    )


@router.post("/register", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def register(
    credentials: Credentials,
    request: Request,
    response: Response,
    database: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> SessionResponse:
    await enforce_auth_rate_limit(
        database,
        scope="register-ip",
        key=client_address(request, settings),
        settings=settings,
    )
    password_hash = await asyncio.to_thread(hash_password, credentials.password)
    user = User(email=credentials.email, password_hash=password_hash)
    database.add(user)
    try:
        await database.flush()
    except IntegrityError as error:
        await database.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        ) from error

    session_token, csrf_token = await _create_session(database, user=user, settings=settings)
    _set_session_cookies(
        response,
        session_token=session_token,
        csrf_token=csrf_token,
        settings=settings,
    )
    return _session_response(user, csrf_token)


@router.post("/login", response_model=SessionResponse)
async def login(
    credentials: Credentials,
    request: Request,
    response: Response,
    database: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> SessionResponse:
    await enforce_auth_rate_limit(
        database,
        scope="login-ip",
        key=client_address(request, settings),
        settings=settings,
    )
    await enforce_auth_rate_limit(
        database,
        scope="login-account",
        key=credentials.email,
        settings=settings,
    )
    user = await database.scalar(select(User).where(User.email == credentials.email))
    if user is None:
        await asyncio.to_thread(perform_dummy_verification, credentials.password)
        raise _invalid_credentials()
    if not await asyncio.to_thread(verify_password, credentials.password, user.password_hash):
        raise _invalid_credentials()

    session_token, csrf_token = await _create_session(database, user=user, settings=settings)
    _set_session_cookies(
        response,
        session_token=session_token,
        csrf_token=csrf_token,
        settings=settings,
    )
    return _session_response(user, csrf_token)


@router.get("/session", response_model=AuthenticatedUser)
async def read_session(
    response: Response,
    current: Annotated[CurrentSession, Depends(get_current_session)],
) -> AuthenticatedUser:
    response.headers["Cache-Control"] = "no-store"
    return AuthenticatedUser(id=current.user.id, email=current.user.email)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    current: Annotated[CurrentSession, Depends(require_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> None:
    await database.execute(
        update(AuthSession)
        .where(AuthSession.id == current.session.id)
        .values(revoked_at=datetime.now(UTC))
    )
    await database.commit()
    _clear_session_cookies(response, settings)


def _invalid_credentials() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid email or password",
    )
