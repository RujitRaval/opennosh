import asyncio
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.auth.client_address import client_address
from opennosh_api.auth.dependencies import (
    CurrentSession,
    get_app_settings,
    get_current_session,
    get_optional_session,
    require_csrf,
)
from opennosh_api.auth.passwords import (
    hash_password,
    perform_dummy_verification,
    verify_password,
)
from opennosh_api.auth.rate_limit import enforce_auth_rate_limit
from opennosh_api.auth.schemas import (
    AccountSettingsUpdate,
    AuthenticatedUser,
    Credentials,
    PasswordChange,
    PasswordConfirmation,
    PasswordRecovery,
    RecoveryCodeResponse,
    RegistrationResponse,
    SessionResponse,
    SessionState,
)
from opennosh_api.auth.tokens import generate_token, hash_token, tokens_match
from opennosh_api.database import get_database_session
from opennosh_api.models import AuthSession, User
from opennosh_api.settings import Settings

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])
SERVICE_ACTOR_EMAIL_DOMAIN = "@actors.opennosh.invalid"


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


def _authenticated_user(user: User) -> AuthenticatedUser:
    settings = user.settings_json if isinstance(user.settings_json, dict) else {}
    preferred_units = settings.get("preferred_units")
    return AuthenticatedUser(
        id=user.id,
        email=user.email,
        onboarding_completed=settings.get("onboarding_completed") is True,
        recovery_configured=user.recovery_token_hash is not None,
        preferred_units=preferred_units if preferred_units in {"metric", "us"} else "metric",
    )


def _session_response(user: User, csrf_token: str) -> SessionResponse:
    return SessionResponse(user=_authenticated_user(user), csrf_token=csrf_token)


@router.post("/register", response_model=RegistrationResponse, status_code=status.HTTP_201_CREATED)
async def register(
    credentials: Credentials,
    request: Request,
    response: Response,
    database: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> RegistrationResponse:
    await enforce_auth_rate_limit(
        database,
        scope="register-ip",
        key=client_address(request, settings),
        settings=settings,
    )
    if credentials.email.endswith(SERVICE_ACTOR_EMAIL_DOMAIN):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Registration is unavailable for this address",
        )
    password_hash = await asyncio.to_thread(hash_password, credentials.password)
    recovery_code = generate_token()
    user = User(
        email=credentials.email,
        password_hash=password_hash,
        recovery_token_hash=hash_token(recovery_code),
    )
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
    return RegistrationResponse(
        user=_authenticated_user(user),
        csrf_token=csrf_token,
        recovery_code=recovery_code,
    )


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
    if user is None or user.actor_kind != "person" or user.login_disabled_at is not None:
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
    return _authenticated_user(current.user)


@router.get("/session-state", response_model=SessionState)
async def read_session_state(
    response: Response,
    current: Annotated[CurrentSession | None, Depends(get_optional_session)],
) -> SessionState:
    response.headers["Cache-Control"] = "no-store"
    return SessionState(
        authenticated=current is not None,
        user=_authenticated_user(current.user) if current is not None else None,
    )


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


@router.post("/recover", response_model=RegistrationResponse)
async def recover_account(
    payload: PasswordRecovery,
    request: Request,
    response: Response,
    database: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> RegistrationResponse:
    await enforce_auth_rate_limit(
        database,
        scope="recover-ip",
        key=client_address(request, settings),
        settings=settings,
    )
    await enforce_auth_rate_limit(
        database,
        scope="recover-account",
        key=payload.email,
        settings=settings,
    )
    user = await database.scalar(select(User).where(User.email == payload.email).with_for_update())
    supplied_hash = hash_token(payload.recovery_code)
    if (
        user is None
        or user.actor_kind != "person"
        or user.login_disabled_at is not None
        or user.recovery_token_hash is None
        or not tokens_match(supplied_hash, user.recovery_token_hash)
    ):
        await asyncio.to_thread(perform_dummy_verification, payload.new_password)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email or recovery code is incorrect",
        )

    new_recovery_code = generate_token()
    user.password_hash = await asyncio.to_thread(hash_password, payload.new_password)
    user.recovery_token_hash = hash_token(new_recovery_code)
    await database.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    session_token, csrf_token = await _create_session(database, user=user, settings=settings)
    _set_session_cookies(
        response,
        session_token=session_token,
        csrf_token=csrf_token,
        settings=settings,
    )
    return RegistrationResponse(
        user=_authenticated_user(user),
        csrf_token=csrf_token,
        recovery_code=new_recovery_code,
    )


@router.put("/account/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    payload: PasswordChange,
    current: Annotated[CurrentSession, Depends(require_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> None:
    if not await asyncio.to_thread(
        verify_password, payload.current_password, current.user.password_hash
    ):
        raise _invalid_credentials()
    current.user.password_hash = await asyncio.to_thread(hash_password, payload.new_password)
    await database.execute(
        update(AuthSession)
        .where(
            AuthSession.user_id == current.user.id,
            AuthSession.id != current.session.id,
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )
    await database.commit()


@router.post("/account/recovery-code", response_model=RecoveryCodeResponse)
async def rotate_recovery_code(
    payload: PasswordConfirmation,
    response: Response,
    current: Annotated[CurrentSession, Depends(require_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> RecoveryCodeResponse:
    if not await asyncio.to_thread(verify_password, payload.password, current.user.password_hash):
        raise _invalid_credentials()
    recovery_code = generate_token()
    current.user.recovery_token_hash = hash_token(recovery_code)
    await database.commit()
    response.headers["Cache-Control"] = "no-store"
    return RecoveryCodeResponse(recovery_code=recovery_code)


@router.patch("/account/settings", response_model=AuthenticatedUser)
async def update_account_settings(
    payload: AccountSettingsUpdate,
    current: Annotated[CurrentSession, Depends(require_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> AuthenticatedUser:
    values = dict(current.user.settings_json)
    if payload.onboarding_completed is not None:
        values["onboarding_completed"] = payload.onboarding_completed
    if payload.preferred_units is not None:
        values["preferred_units"] = payload.preferred_units
    current.user.settings_json = values
    await database.commit()
    return _authenticated_user(current.user)


@router.delete("/account", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    payload: PasswordConfirmation,
    response: Response,
    current: Annotated[CurrentSession, Depends(require_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> None:
    if not await asyncio.to_thread(verify_password, payload.password, current.user.password_hash):
        raise _invalid_credentials()
    await database.execute(delete(User).where(User.id == current.user.id))
    await database.commit()
    _clear_session_cookies(response, settings)


def _invalid_credentials() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid email or password",
    )
