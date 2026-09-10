"""Explicit pack-scoped owner review; grants are writable only by the administrator."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.governance.models import GovernanceOwnerAuthorization


async def active_owner_authorization(
    session: AsyncSession,
    *,
    pack_id: str,
    actor_id: UUID,
    now: datetime,
) -> GovernanceOwnerAuthorization | None:
    result: GovernanceOwnerAuthorization | None = await session.scalar(
        select(GovernanceOwnerAuthorization).where(
            GovernanceOwnerAuthorization.pack_id == pack_id,
            GovernanceOwnerAuthorization.actor_id == actor_id,
            GovernanceOwnerAuthorization.granted_at <= now,
            (
                GovernanceOwnerAuthorization.revoked_at.is_(None)
                | (GovernanceOwnerAuthorization.revoked_at > now)
            ),
        )
    )

    return result
