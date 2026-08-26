from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, cast
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.auth.tokens import hash_token, tokens_match
from opennosh_api.database import get_database_session
from opennosh_api.models import AuthSession, User
from opennosh_api.settings import Settings


@dataclass(frozen=True)
class CurrentSession:
    user: User
    session: AuthSession

    @property
    def user_id(self) -> UUID:
        return self.user.id


def get_app_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


async def get_current_session(
    request: Request,
    database: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> CurrentSession:
    raw_token = request.cookies.get(settings.session_cookie_name)
    if raw_token is None:
        raise _unauthorized()

    statement = (
        select(AuthSession, User)
        .join(User, User.id == AuthSession.user_id)
        .where(
            AuthSession.token_hash == hash_token(raw_token),
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > datetime.now(UTC),
        )
    )
    row = (await database.execute(statement)).one_or_none()
    if row is None:
        raise _unauthorized()
    return CurrentSession(user=row[1], session=row[0])


async def get_optional_session(
    request: Request,
    database: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> CurrentSession | None:
    raw_token = request.cookies.get(settings.session_cookie_name)
    if raw_token is None:
        return None

    statement = (
        select(AuthSession, User)
        .join(User, User.id == AuthSession.user_id)
        .where(
            AuthSession.token_hash == hash_token(raw_token),
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > datetime.now(UTC),
        )
    )
    row = (await database.execute(statement)).one_or_none()
    if row is None:
        return None
    return CurrentSession(user=row[1], session=row[0])


async def require_csrf(
    request: Request,
    current: Annotated[CurrentSession, Depends(get_current_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> CurrentSession:
    header_token = request.headers.get("X-CSRF-Token")
    cookie_token = request.cookies.get(settings.csrf_cookie_name)
    if (
        header_token is None
        or cookie_token is None
        or not tokens_match(header_token, cookie_token)
        or not tokens_match(hash_token(header_token), current.session.csrf_token_hash)
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")
    return current


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
    )
