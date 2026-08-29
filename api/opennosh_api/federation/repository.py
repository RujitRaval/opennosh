from __future__ import annotations

from datetime import datetime
from typing import Protocol, cast
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.federation.models import (
    FederationAuditEvent,
    FederationInvitation,
    FederationMaintainer,
    FederationRoleKey,
)
from opennosh_api.publication.models import AcceptedEvent, PublicationReceiptRecord


class FederationRepository:
    """Transaction-scoped persistence for the invitation-only federation slice."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock_single_invitation_slot(self) -> None:
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
            {"scope": "opennosh:federation:first-invitation"},
        )

    async def invitation_count(self) -> int:
        return int(
            await self._session.scalar(text("SELECT count(*) FROM federation_invitations")) or 0
        )

    def add_invitation(self, invitation: FederationInvitation) -> None:
        self._session.add(invitation)

    async def invitation_by_hash(
        self,
        token_hash: bytes,
        *,
        lock: bool = False,
    ) -> FederationInvitation | None:
        statement = select(FederationInvitation).where(
            FederationInvitation.token_hash == token_hash
        )
        if lock:
            statement = statement.with_for_update()
        return cast(FederationInvitation | None, await self._session.scalar(statement))

    async def maintainer(self, maintainer_id: UUID, *, lock: bool = False) -> FederationMaintainer:
        statement = select(FederationMaintainer).where(FederationMaintainer.id == maintainer_id)
        if lock:
            statement = statement.with_for_update()
        maintainer = await self._session.scalar(statement)
        if maintainer is None:
            raise LookupError("federation_maintainer_not_found")
        return maintainer

    async def active_role_key(
        self,
        maintainer_id: UUID,
        *,
        lock: bool = False,
    ) -> FederationRoleKey:
        statement = select(FederationRoleKey).where(
            FederationRoleKey.maintainer_id == maintainer_id,
            FederationRoleKey.retired_at.is_(None),
        )
        if lock:
            statement = statement.with_for_update()
        key = await self._session.scalar(statement)
        if key is None:
            raise LookupError("federation_active_role_key_not_found")
        return key

    def add_maintainer(self, maintainer: FederationMaintainer) -> None:
        self._session.add(maintainer)

    def add_role_key(self, key: FederationRoleKey) -> None:
        self._session.add(key)

    def add_audit_event(self, event: FederationAuditEvent) -> None:
        self._session.add(event)

    async def actor_is_active_human_steward(
        self,
        *,
        actor_id: UUID,
        pack_id: str,
        at: datetime,
    ) -> bool:
        return bool(
            await self._session.scalar(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM users AS actor
                        JOIN governance_role_assignments AS role
                          ON role.actor_id = actor.id
                        WHERE actor.id = :actor_id
                          AND actor.actor_kind = 'person'
                          AND actor.login_disabled_at IS NULL
                          AND role.pack_id = :pack_id
                          AND role.role = 'steward'
                          AND role.granted_at <= :at
                          AND (role.revoked_at IS NULL OR role.revoked_at > :at)
                    )
                    """
                ),
                {"actor_id": actor_id, "pack_id": pack_id, "at": at},
            )
        )

    async def block_scoped_publications(
        self,
        *,
        repository: str,
        pack_id: str,
        code: str,
        now: datetime,
    ) -> int:
        result = await self._session.execute(
            text(
                """
                UPDATE publication_intents
                SET state = 'publish_blocked',
                    last_failure_code = :code,
                    workflow_revision = workflow_revision + 1,
                    updated_at = :now
                WHERE forge_target = :forge_target
                  AND pack_id = :pack_id
                  AND state IN ('pending','running','retrying','publish_retrying')
                RETURNING id
                """
            ),
            {
                "code": code,
                "now": now,
                "forge_target": f"github:{repository}",
                "pack_id": pack_id,
            },
        )
        return len(result.scalars().all())

    async def receipt_for_release(
        self,
        *,
        publication_id: UUID,
        pack_id: str,
        receipt_digest: str,
    ) -> tuple[PublicationReceiptRecord, AcceptedEvent] | None:
        row = (
            await self._session.execute(
                select(PublicationReceiptRecord, AcceptedEvent)
                .join(
                    AcceptedEvent,
                    AcceptedEvent.receipt_digest == PublicationReceiptRecord.receipt_digest,
                )
                .where(
                    PublicationReceiptRecord.publication_id == publication_id,
                    PublicationReceiptRecord.receipt_digest == receipt_digest,
                    PublicationReceiptRecord.pack_id == pack_id,
                    AcceptedEvent.pack_id == pack_id,
                )
            )
        ).one_or_none()
        if row is None:
            return None
        return row[0], row[1]

    async def release_event_count(self, maintainer_id: UUID) -> int:
        return int(
            await self._session.scalar(
                select(text("count(*)"))
                .select_from(FederationAuditEvent)
                .where(
                    FederationAuditEvent.maintainer_id == maintainer_id,
                    FederationAuditEvent.event_type == "release_published",
                )
            )
            or 0
        )


class FederationClaimConnection(Protocol):
    async def fetchval(self, query: str, *args: object) -> object: ...


async def federation_scope_allows_claim(
    connection: FederationClaimConnection,
    *,
    repository: str,
    pack_id: str,
) -> bool:
    """Return false when an enrolled scope is not active.

    This helper accepts an asyncpg connection without importing asyncpg into the
    federation model layer.
    """

    blocked = await connection.fetchval(
        """
        SELECT EXISTS (
            SELECT 1
            FROM federation_maintainers
            WHERE repository = $1
              AND pack_id = $2
              AND state != 'active'
        )
        """,
        repository,
        pack_id,
    )
    return not bool(blocked)
