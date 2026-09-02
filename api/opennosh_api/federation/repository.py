from __future__ import annotations

from datetime import datetime
from typing import Protocol, cast
from uuid import UUID

from sqlalchemy import exists, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.federation.models import (
    FederationAuditEvent,
    FederationInvitation,
    FederationMaintainer,
    FederationPackInstallationEvent,
    FederationProjectionActivation,
    FederationProjectionCheckpoint,
    FederationProjectionFood,
    FederationProjectionRelease,
    FederationRelease,
    FederationReleaseStatusEvent,
    FederationRoleKey,
    FederationVerifiedRelease,
)
from opennosh_api.publication.models import AcceptedEvent, PublicationReceiptRecord


class FederationRepository:
    """Transaction-scoped persistence for the invitation-only federation slice."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock_invitation_scope(self, *, repository_id: int, pack_id: str) -> None:
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
            {"scope": f"opennosh:federation:invitation:{repository_id}:{pack_id}"},
        )

    async def invitation_count(self, *, repository_id: int, pack_id: str) -> int:
        return int(
            await self._session.scalar(
                select(text("count(*)"))
                .select_from(FederationInvitation)
                .where(
                    FederationInvitation.repository_id == repository_id,
                    FederationInvitation.pack_id == pack_id,
                )
            )
            or 0
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

    async def lock_release_scope(self, *, repository_id: int, pack_id: str) -> None:
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
            {"scope": f"opennosh:federation:release:{repository_id}:{pack_id}"},
        )

    async def release_for_version(
        self,
        *,
        repository_id: int,
        pack_id: str,
        release_version: str,
    ) -> FederationRelease | None:
        statement = select(FederationRelease).where(
            FederationRelease.repository_id == repository_id,
            FederationRelease.pack_id == pack_id,
            FederationRelease.release_version == release_version,
        )
        return cast(FederationRelease | None, await self._session.scalar(statement))

    async def latest_release(
        self,
        *,
        repository_id: int,
        pack_id: str,
    ) -> FederationRelease | None:
        statement = (
            select(FederationRelease)
            .where(
                FederationRelease.repository_id == repository_id,
                FederationRelease.pack_id == pack_id,
            )
            .order_by(FederationRelease.receipt_published_at.desc())
            .limit(1)
        )
        return cast(FederationRelease | None, await self._session.scalar(statement))

    def add_release(self, release: FederationRelease) -> None:
        self._session.add(release)

    async def release_by_statement_digest(self, statement_digest: str) -> FederationRelease:
        release = await self._session.scalar(
            select(FederationRelease).where(FederationRelease.statement_digest == statement_digest)
        )
        if release is None:
            raise LookupError("federation_release_not_found")
        return release

    async def role_key(self, role_key_id: UUID) -> FederationRoleKey:
        key = await self._session.scalar(
            select(FederationRoleKey).where(FederationRoleKey.id == role_key_id)
        )
        if key is None:
            raise LookupError("federation_role_key_not_found")
        return key

    async def verified_release(self, release_id: UUID) -> FederationVerifiedRelease | None:
        return cast(
            FederationVerifiedRelease | None,
            await self._session.scalar(
                select(FederationVerifiedRelease).where(
                    FederationVerifiedRelease.release_id == release_id
                )
            ),
        )

    def add_verified_release(self, release: FederationVerifiedRelease) -> None:
        self._session.add(release)

    def add_release_status(self, event: FederationReleaseStatusEvent) -> None:
        self._session.add(event)

    async def lock_installation_scope(self, *, repository_id: int, pack_id: str) -> None:
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
            {"scope": f"opennosh:federation:installation:{repository_id}:{pack_id}"},
        )

    async def latest_installation(
        self, *, repository_id: int, pack_id: str
    ) -> FederationPackInstallationEvent | None:
        return cast(
            FederationPackInstallationEvent | None,
            await self._session.scalar(
                select(FederationPackInstallationEvent)
                .where(
                    FederationPackInstallationEvent.repository_id == repository_id,
                    FederationPackInstallationEvent.pack_id == pack_id,
                )
                .order_by(FederationPackInstallationEvent.generation.desc())
                .limit(1)
            ),
        )

    def add_installation(self, event: FederationPackInstallationEvent) -> None:
        self._session.add(event)

    async def installation_release(
        self, verified_release_id: UUID
    ) -> tuple[FederationVerifiedRelease, FederationRelease, FederationMaintainer]:
        row = (
            await self._session.execute(
                select(FederationVerifiedRelease, FederationRelease, FederationMaintainer)
                .join(
                    FederationRelease, FederationRelease.id == FederationVerifiedRelease.release_id
                )
                .join(
                    FederationMaintainer, FederationMaintainer.id == FederationRelease.maintainer_id
                )
                .where(FederationVerifiedRelease.id == verified_release_id)
            )
        ).one_or_none()
        if row is None:
            raise LookupError("federation_verified_release_not_found")
        return row[0], row[1], row[2]

    async def installed_verified_releases(
        self,
    ) -> tuple[tuple[FederationVerifiedRelease, FederationRelease, FederationMaintainer], ...]:
        latest = (
            select(
                FederationPackInstallationEvent.repository_id,
                FederationPackInstallationEvent.pack_id,
                FederationPackInstallationEvent.verified_release_id,
                FederationPackInstallationEvent.action,
            )
            .distinct(
                FederationPackInstallationEvent.repository_id,
                FederationPackInstallationEvent.pack_id,
            )
            .order_by(
                FederationPackInstallationEvent.repository_id,
                FederationPackInstallationEvent.pack_id,
                FederationPackInstallationEvent.generation.desc(),
            )
            .subquery()
        )
        quarantined = exists().where(
            FederationReleaseStatusEvent.release_id == FederationRelease.id,
            FederationReleaseStatusEvent.state == "quarantined",
        )
        rows = (
            await self._session.execute(
                select(FederationVerifiedRelease, FederationRelease, FederationMaintainer)
                .join(latest, latest.c.verified_release_id == FederationVerifiedRelease.id)
                .join(
                    FederationRelease, FederationRelease.id == FederationVerifiedRelease.release_id
                )
                .join(
                    FederationMaintainer, FederationMaintainer.id == FederationRelease.maintainer_id
                )
                .where(
                    latest.c.action != "remove",
                    FederationMaintainer.state == "active",
                    ~quarantined,
                )
                .order_by(FederationRelease.repository_id, FederationRelease.pack_id)
            )
        ).all()
        return tuple((row[0], row[1], row[2]) for row in rows)

    async def release_is_quarantined(self, release_id: UUID) -> bool:
        return bool(
            await self._session.scalar(
                select(
                    exists().where(
                        FederationReleaseStatusEvent.release_id == release_id,
                        FederationReleaseStatusEvent.state == "quarantined",
                    )
                )
            )
        )

    async def lock_projection(self) -> None:
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext('opennosh:federation:projection'))")
        )

    async def eligible_verified_releases(
        self,
    ) -> tuple[tuple[FederationVerifiedRelease, FederationRelease, FederationMaintainer], ...]:
        quarantined = exists().where(
            FederationReleaseStatusEvent.release_id == FederationRelease.id,
            FederationReleaseStatusEvent.state == "quarantined",
        )
        rows = (
            await self._session.execute(
                select(FederationVerifiedRelease, FederationRelease, FederationMaintainer)
                .join(
                    FederationRelease,
                    FederationRelease.id == FederationVerifiedRelease.release_id,
                )
                .join(
                    FederationMaintainer,
                    FederationMaintainer.id == FederationRelease.maintainer_id,
                )
                .where(
                    FederationMaintainer.state == "active",
                    ~quarantined,
                )
                .order_by(
                    FederationRelease.repository_id,
                    FederationRelease.pack_id,
                    FederationRelease.receipt_published_at.desc(),
                    FederationRelease.id,
                )
            )
        ).all()
        selected: list[
            tuple[FederationVerifiedRelease, FederationRelease, FederationMaintainer]
        ] = []
        scopes: set[tuple[int, str]] = set()
        for verified, release, maintainer in rows:
            scope = (release.repository_id, release.pack_id)
            if scope not in scopes:
                selected.append((verified, release, maintainer))
                scopes.add(scope)
        return tuple(selected)

    async def projection_checkpoint(
        self, release_set_digest: str, *, mode: str = "registry"
    ) -> FederationProjectionCheckpoint | None:
        return cast(
            FederationProjectionCheckpoint | None,
            await self._session.scalar(
                select(FederationProjectionCheckpoint).where(
                    FederationProjectionCheckpoint.release_set_digest == release_set_digest,
                    FederationProjectionCheckpoint.mode == mode,
                )
            ),
        )

    async def projection_checkpoint_by_id(
        self, checkpoint_id: UUID
    ) -> FederationProjectionCheckpoint:
        checkpoint = await self._session.get(FederationProjectionCheckpoint, checkpoint_id)
        if checkpoint is None:
            raise LookupError("federation_projection_checkpoint_not_found")
        return checkpoint

    async def latest_projection_activation(self) -> FederationProjectionActivation | None:
        return cast(
            FederationProjectionActivation | None,
            await self._session.scalar(
                select(FederationProjectionActivation)
                .order_by(
                    FederationProjectionActivation.activated_at.desc(),
                    FederationProjectionActivation.id.desc(),
                )
                .limit(1)
            ),
        )

    def add_projection_checkpoint(self, checkpoint: FederationProjectionCheckpoint) -> None:
        self._session.add(checkpoint)

    def add_projection_release(self, release: FederationProjectionRelease) -> None:
        self._session.add(release)

    def add_projection_food(self, food: FederationProjectionFood) -> None:
        self._session.add(food)

    def add_projection_activation(self, activation: FederationProjectionActivation) -> None:
        self._session.add(activation)

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
