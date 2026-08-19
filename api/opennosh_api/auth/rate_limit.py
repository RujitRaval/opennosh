from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.auth.tokens import hash_token
from opennosh_api.models import AuthRateLimit
from opennosh_api.settings import Settings

_RECORD_ATTEMPT = text(
    """
    INSERT INTO auth_rate_limits (
        scope, key_hash, window_started_at, attempt_count, updated_at
    ) VALUES (
        :scope, :key_hash, :now, 1, :now
    )
    ON CONFLICT (scope, key_hash) DO UPDATE SET
        window_started_at = CASE
            WHEN auth_rate_limits.window_started_at <=
                 :now - (:window_seconds * INTERVAL '1 second')
            THEN :now
            ELSE auth_rate_limits.window_started_at
        END,
        attempt_count = CASE
            WHEN auth_rate_limits.window_started_at <=
                 :now - (:window_seconds * INTERVAL '1 second')
            THEN 1
            ELSE auth_rate_limits.attempt_count + 1
        END,
        updated_at = :now
    RETURNING attempt_count, window_started_at
    """
)


async def enforce_auth_rate_limit(
    session: AsyncSession,
    *,
    scope: str,
    key: str,
    settings: Settings,
) -> None:
    now = datetime.now(UTC)
    await session.execute(
        delete(AuthRateLimit).where(
            AuthRateLimit.updated_at
            <= now - timedelta(seconds=settings.auth_rate_limit_retention_seconds)
        )
    )
    result = await session.execute(
        _RECORD_ATTEMPT,
        {
            "scope": scope,
            "key_hash": hash_token(key),
            "now": now,
            "window_seconds": settings.auth_rate_limit_window_seconds,
        },
    )
    attempt_count, window_started_at = result.one()
    await session.commit()
    if attempt_count > settings.auth_rate_limit_attempts:
        elapsed = max(0, int((now - window_started_at).total_seconds()))
        retry_after = max(1, settings.auth_rate_limit_window_seconds - elapsed)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many authentication attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )
