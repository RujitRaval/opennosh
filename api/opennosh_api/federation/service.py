from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from opennosh_api.federation.contracts import (
    FederationEventType,
    FederationLifecycleState,
    FederationScope,
    InvitationSecret,
    MaintainerStatus,
    SignedFederationRelease,
    canonical_json,
    public_key_fingerprint,
    release_statement_digest,
    validate_key_id,
    verify_release_signature,
)
from opennosh_api.federation.models import (
    FederationAuditEvent,
    FederationInvitation,
    FederationMaintainer,
    FederationRelease,
    FederationRoleKey,
)
from opennosh_api.federation.repository import FederationRepository
from opennosh_api.publication.receipts import SignedPublicationReceipt, signed_receipt_digest


class InstallationVerifier(Protocol):
    async def verify(self, scope: FederationScope, *, installation_id: int) -> None: ...


class FederationError(RuntimeError):
    def __init__(self, code: str, *, exit_code: int = 4) -> None:
        super().__init__(code)
        self.code = code
        self.exit_code = exit_code


class FederationService:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        *,
        allowed_scopes: Sequence[FederationScope],
        allowed_public_origin: str,
        installation_verifier: InstallationVerifier,
    ) -> None:
        if not 1 <= len(allowed_scopes) <= 32:
            raise ValueError("Federation service requires 1 to 32 allowed scopes")
        if len(set(allowed_scopes)) != len(allowed_scopes):
            raise ValueError("Federation service requires distinct allowed scopes")
        self._factory = factory
        self._allowed_scopes = tuple(allowed_scopes)
        self._allowed_public_origin = allowed_public_origin.rstrip("/")
        self._installation_verifier = installation_verifier

    async def invite(
        self,
        scope: FederationScope,
        *,
        inviter_actor_id: UUID,
        expires_at: datetime,
        now: datetime | None = None,
    ) -> InvitationSecret:
        timestamp = _aware(now or datetime.now(UTC))
        if (
            expires_at.tzinfo is None
            or expires_at.utcoffset() is None
            or expires_at <= timestamp
            or expires_at > timestamp + timedelta(hours=24)
        ):
            raise FederationError("invitation_expiry_invalid", exit_code=2)
        self._require_allowed_scope(scope)
        token = secrets.token_urlsafe(32)
        token_hash = _token_hash(token)
        invitation = FederationInvitation(
            id=uuid4(),
            github_account_id=scope.github_account_id,
            github_login=scope.github_login,
            repository_id=scope.repository_id,
            repository=scope.repository,
            pack_id=scope.pack_id,
            inviter_actor_id=inviter_actor_id,
            token_hash=token_hash,
            expires_at=expires_at,
            created_at=timestamp,
        )
        async with self._factory() as session, session.begin():
            repository = FederationRepository(session)
            await repository.lock_invitation_scope(
                repository_id=scope.repository_id,
                pack_id=scope.pack_id,
            )
            await _require_active_steward(
                repository,
                actor_id=inviter_actor_id,
                pack_id=scope.pack_id,
                at=timestamp,
            )
            if (
                await repository.invitation_count(
                    repository_id=scope.repository_id,
                    pack_id=scope.pack_id,
                )
                != 0
            ):
                raise FederationError("federation_invitation_limit_reached")
            repository.add_invitation(invitation)
            # The audit row references this invitation. Flush explicitly because
            # the models intentionally avoid ORM relationships and SQLAlchemy
            # therefore cannot infer the insert dependency.
            await session.flush()
            repository.add_audit_event(
                _audit_event(
                    invitation_id=invitation.id,
                    actor_id=inviter_actor_id,
                    event_type=FederationEventType.INVITATION_CREATED,
                    reason="Configured invitation-only federation enrollment",
                    payload={
                        "scope": scope.model_dump(mode="json"),
                        "expires_at": expires_at.isoformat(),
                    },
                    now=timestamp,
                )
            )
        return InvitationSecret(
            invitation_id=invitation.id,
            token=token,
            expires_at=expires_at,
        )

    async def verify(
        self,
        *,
        token: str,
        scope: FederationScope,
        installation_id: int,
        key_id: str,
        public_key: str,
        public_key_fingerprint: str,
        now: datetime | None = None,
    ) -> MaintainerStatus:
        timestamp = _aware(now or datetime.now(UTC))
        self._require_allowed_scope(scope)
        validate_key_id(key_id)
        _require_role_key_binding(public_key, public_key_fingerprint)
        token_hash = _token_hash(token)
        async with self._factory() as session:
            invitation = await FederationRepository(session).invitation_by_hash(token_hash)
        if invitation is None or not hmac.compare_digest(invitation.token_hash, token_hash):
            raise FederationError("invitation_invalid", exit_code=3)
        _validate_invitation(invitation, scope, timestamp)
        await self._installation_verifier.verify(scope, installation_id=installation_id)

        async with self._factory() as session, session.begin():
            repository = FederationRepository(session)
            invitation = await repository.invitation_by_hash(token_hash, lock=True)
            if invitation is None or not hmac.compare_digest(invitation.token_hash, token_hash):
                raise FederationError("invitation_invalid", exit_code=3)
            _validate_invitation(invitation, scope, timestamp)
            await _require_active_steward(
                repository,
                actor_id=invitation.inviter_actor_id,
                pack_id=scope.pack_id,
                at=timestamp,
            )
            maintainer = FederationMaintainer(
                id=uuid4(),
                github_account_id=scope.github_account_id,
                github_login=scope.github_login,
                github_app_installation_id=installation_id,
                repository_id=scope.repository_id,
                repository=scope.repository,
                pack_id=scope.pack_id,
                current_role_key_id=key_id,
                current_role_key_fingerprint=public_key_fingerprint,
                state=FederationLifecycleState.REQUESTED.value,
                inviter_actor_id=invitation.inviter_actor_id,
                requested_at=timestamp,
                created_at=timestamp,
            )
            role_key = FederationRoleKey(
                id=uuid4(),
                maintainer_id=maintainer.id,
                key_id=key_id,
                public_key=public_key,
                public_key_fingerprint=public_key_fingerprint,
                activated_at=timestamp,
                rotated_by_actor_id=invitation.inviter_actor_id,
                created_at=timestamp,
            )
            invitation.consumed_at = timestamp
            repository.add_maintainer(maintainer)
            await session.flush()
            repository.add_role_key(role_key)
            repository.add_audit_event(
                _audit_event(
                    maintainer_id=maintainer.id,
                    invitation_id=invitation.id,
                    actor_id=invitation.inviter_actor_id,
                    event_type=FederationEventType.MAINTAINER_REQUESTED,
                    reason="Invitation scope accepted for provider verification",
                    payload={"state": FederationLifecycleState.REQUESTED.value},
                    now=timestamp,
                )
            )
            maintainer.state = FederationLifecycleState.VERIFIED.value
            maintainer.verified_at = timestamp
            repository.add_audit_event(
                _audit_event(
                    maintainer_id=maintainer.id,
                    invitation_id=invitation.id,
                    actor_id=invitation.inviter_actor_id,
                    event_type=FederationEventType.INVITATION_CONSUMED,
                    reason="Single-use invitation verified against GitHub App control",
                    payload={
                        "scope": scope.model_dump(mode="json"),
                        "installation_id": installation_id,
                    },
                    now=timestamp,
                )
            )
            repository.add_audit_event(
                _audit_event(
                    maintainer_id=maintainer.id,
                    invitation_id=invitation.id,
                    actor_id=invitation.inviter_actor_id,
                    event_type=FederationEventType.MAINTAINER_VERIFIED,
                    reason="Immutable GitHub identity and online role key verified",
                    payload={"key_id": key_id, "key_fingerprint": public_key_fingerprint},
                    now=timestamp,
                )
            )
        return _status(maintainer)

    async def activate(
        self,
        maintainer_id: UUID,
        *,
        actor_id: UUID,
        reason: str,
        now: datetime | None = None,
    ) -> MaintainerStatus:
        return await self._transition(
            maintainer_id,
            actor_id=actor_id,
            reason=reason,
            required=FederationLifecycleState.VERIFIED,
            target=FederationLifecycleState.ACTIVE,
            event_type=FederationEventType.MAINTAINER_ACTIVATED,
            timestamp_field="activated_at",
            now=now,
        )

    async def rotate_key(
        self,
        maintainer_id: UUID,
        *,
        key_id: str,
        public_key: str,
        public_key_fingerprint: str,
        actor_id: UUID,
        reason: str,
        now: datetime | None = None,
    ) -> MaintainerStatus:
        timestamp = _aware(now or datetime.now(UTC))
        _require_reason(reason)
        validate_key_id(key_id)
        _require_role_key_binding(public_key, public_key_fingerprint)
        async with self._factory() as session, session.begin():
            repository = FederationRepository(session)
            maintainer = await repository.maintainer(maintainer_id, lock=True)
            await _require_active_steward(
                repository,
                actor_id=actor_id,
                pack_id=maintainer.pack_id,
                at=timestamp,
            )
            if maintainer.state != FederationLifecycleState.ACTIVE.value:
                raise FederationError("maintainer_not_active")
            previous = await repository.active_role_key(maintainer_id, lock=True)
            if (
                previous.key_id == key_id
                or previous.public_key_fingerprint == public_key_fingerprint
            ):
                raise FederationError("role_key_rotation_not_independent", exit_code=3)
            previous.retired_at = timestamp
            replacement = FederationRoleKey(
                id=uuid4(),
                maintainer_id=maintainer.id,
                key_id=key_id,
                public_key=public_key,
                public_key_fingerprint=public_key_fingerprint,
                activated_at=timestamp,
                rotated_by_actor_id=actor_id,
                prior_key_id=previous.id,
                created_at=timestamp,
            )
            maintainer.current_role_key_id = key_id
            maintainer.current_role_key_fingerprint = public_key_fingerprint
            repository.add_role_key(replacement)
            repository.add_audit_event(
                _audit_event(
                    maintainer_id=maintainer.id,
                    actor_id=actor_id,
                    event_type=FederationEventType.ROLE_KEY_ROTATED,
                    reason=reason,
                    payload={
                        "prior_key_id": previous.key_id,
                        "prior_fingerprint": previous.public_key_fingerprint,
                        "key_id": key_id,
                        "key_fingerprint": public_key_fingerprint,
                    },
                    now=timestamp,
                )
            )
        return _status(maintainer)

    async def publish_release(
        self,
        release: SignedFederationRelease,
        *,
        actor_id: UUID,
        reason: str,
        now: datetime | None = None,
    ) -> str:
        timestamp = _aware(now or datetime.now(UTC))
        _require_reason(reason)
        statement = release.statement
        async with self._factory() as session, session.begin():
            repository = FederationRepository(session)
            maintainer = await repository.maintainer(statement.maintainer_id, lock=True)
            await _require_active_steward(
                repository,
                actor_id=actor_id,
                pack_id=maintainer.pack_id,
                at=timestamp,
            )
            if maintainer.state != FederationLifecycleState.ACTIVE.value:
                raise FederationError("maintainer_not_active", exit_code=3)
            if (
                maintainer.repository_id != statement.repository_id
                or maintainer.repository != statement.repository
                or maintainer.pack_id != statement.pack_id
            ):
                raise FederationError("release_scope_mismatch", exit_code=3)
            await repository.lock_release_scope(
                repository_id=statement.repository_id,
                pack_id=statement.pack_id,
            )
            digest = release_statement_digest(statement)
            key = await repository.active_role_key(maintainer.id, lock=True)
            if key.key_id != statement.key_id:
                raise FederationError("release_key_retired_or_untrusted", exit_code=3)
            try:
                verify_release_signature(release, encoded_public_key=key.public_key)
            except ValueError as error:
                raise FederationError("release_signature_invalid", exit_code=3) from error
            existing = await repository.release_for_version(
                repository_id=statement.repository_id,
                pack_id=statement.pack_id,
                release_version=statement.release_version,
            )
            if existing is not None:
                if existing.statement_digest == digest and existing.signature == release.signature:
                    return digest
                raise FederationError("release_version_conflict", exit_code=3)
            latest = await repository.latest_release(
                repository_id=statement.repository_id,
                pack_id=statement.pack_id,
            )
            bound = await repository.receipt_for_release(
                publication_id=statement.publication_id,
                pack_id=statement.pack_id,
                receipt_digest=statement.receipt_digest,
            )
            if bound is None:
                raise FederationError("governed_release_receipt_not_found", exit_code=3)
            receipt_record, accepted_event = bound
            if accepted_event.repository != f"github:{statement.repository}":
                raise FederationError("governed_release_repository_mismatch", exit_code=3)
            try:
                envelope = SignedPublicationReceipt.model_validate(receipt_record.envelope_json)
            except ValidationError as error:
                raise FederationError("governed_release_receipt_invalid", exit_code=5) from error
            receipt = envelope.receipt
            if (
                signed_receipt_digest(envelope) != statement.receipt_digest
                or receipt.published_at > statement.issued_at
                or statement.issued_at > timestamp + timedelta(minutes=5)
                or receipt.release_version != statement.release_version
                or receipt.signed_release_metadata_digest != statement.manifest_digest
                or receipt.publication_id != statement.publication_id
                or statement.public_url
                != (
                    f"{self._allowed_public_origin}/api/v1/public/releases/"
                    f"{statement.release_version}/manifest"
                )
            ):
                raise FederationError("governed_release_binding_mismatch", exit_code=3)
            if latest is not None and receipt.published_at <= latest.receipt_published_at:
                raise FederationError("release_rollback_detected", exit_code=3)
            repository.add_release(
                FederationRelease(
                    id=uuid4(),
                    maintainer_id=maintainer.id,
                    role_key_id=key.id,
                    accepted_event_id=accepted_event.id,
                    repository_id=statement.repository_id,
                    repository=statement.repository,
                    pack_id=statement.pack_id,
                    publication_id=statement.publication_id,
                    release_version=statement.release_version,
                    statement_json=statement.model_dump(mode="json"),
                    statement_digest=digest,
                    manifest_digest=statement.manifest_digest,
                    receipt_digest=statement.receipt_digest,
                    public_url=statement.public_url,
                    key_id=statement.key_id,
                    signature=release.signature,
                    issued_at=statement.issued_at,
                    receipt_published_at=receipt.published_at,
                    verified_at=timestamp,
                    created_at=timestamp,
                )
            )
            repository.add_audit_event(
                _audit_event(
                    maintainer_id=maintainer.id,
                    actor_id=actor_id,
                    event_type=FederationEventType.RELEASE_PUBLISHED,
                    reason=reason,
                    payload={
                        "statement_digest": digest,
                        "release_version": statement.release_version,
                        "manifest_digest": statement.manifest_digest,
                        "receipt_digest": statement.receipt_digest,
                        "publication_id": str(statement.publication_id),
                        "key_id": statement.key_id,
                    },
                    now=timestamp,
                )
            )
        return digest

    async def quarantine(
        self,
        maintainer_id: UUID,
        *,
        actor_id: UUID,
        reason: str,
        now: datetime | None = None,
    ) -> MaintainerStatus:
        return await self._terminal_transition(
            maintainer_id,
            actor_id=actor_id,
            reason=reason,
            target=FederationLifecycleState.QUARANTINED,
            event_type=FederationEventType.MAINTAINER_QUARANTINED,
            timestamp_field="quarantined_at",
            failure_code="federation_scope_quarantined",
            now=now,
        )

    async def revoke(
        self,
        maintainer_id: UUID,
        *,
        actor_id: UUID,
        reason: str,
        now: datetime | None = None,
    ) -> MaintainerStatus:
        return await self._terminal_transition(
            maintainer_id,
            actor_id=actor_id,
            reason=reason,
            target=FederationLifecycleState.REVOKED,
            event_type=FederationEventType.MAINTAINER_REVOKED,
            timestamp_field="revoked_at",
            failure_code="federation_scope_revoked",
            now=now,
        )

    async def status(self, maintainer_id: UUID) -> MaintainerStatus:
        async with self._factory() as session:
            return _status(await FederationRepository(session).maintainer(maintainer_id))

    async def record_rejected_attempt(
        self,
        *,
        actor_id: UUID,
        operation: str,
        code: str,
        maintainer_id: UUID | None = None,
        token: str | None = None,
        now: datetime | None = None,
    ) -> None:
        """Persist a redacted audit fact after an operator command is rejected.

        This runs in its own transaction because the command transaction that
        discovered the rejection must roll back. Raw invitation tokens and
        submitted key material are deliberately excluded from the audit payload.
        """

        timestamp = _aware(now or datetime.now(UTC))
        invitation_id: UUID | None = None
        token_hash: bytes | None = None
        if token is not None:
            try:
                token_hash = _token_hash(token)
            except FederationError:
                token_hash = None
        async with self._factory() as session, session.begin():
            repository = FederationRepository(session)
            if token_hash is not None:
                invitation = await repository.invitation_by_hash(token_hash)
                if invitation is not None:
                    invitation_id = invitation.id
            bound_maintainer_id: UUID | None = None
            if maintainer_id is not None:
                try:
                    bound_maintainer_id = (await repository.maintainer(maintainer_id)).id
                except LookupError:
                    bound_maintainer_id = None
            repository.add_audit_event(
                _audit_event(
                    maintainer_id=bound_maintainer_id,
                    invitation_id=invitation_id,
                    actor_id=actor_id,
                    event_type=FederationEventType.ADMIN_ATTEMPT_REJECTED,
                    reason=f"Rejected federation operator attempt: {code}",
                    payload={"operation": operation, "code": code},
                    now=timestamp,
                )
            )

    async def _transition(
        self,
        maintainer_id: UUID,
        *,
        actor_id: UUID,
        reason: str,
        required: FederationLifecycleState,
        target: FederationLifecycleState,
        event_type: FederationEventType,
        timestamp_field: str,
        now: datetime | None,
    ) -> MaintainerStatus:
        timestamp = _aware(now or datetime.now(UTC))
        _require_reason(reason)
        async with self._factory() as session, session.begin():
            repository = FederationRepository(session)
            maintainer = await repository.maintainer(maintainer_id, lock=True)
            await _require_active_steward(
                repository,
                actor_id=actor_id,
                pack_id=maintainer.pack_id,
                at=timestamp,
            )
            if maintainer.state != required.value:
                raise FederationError(
                    f"maintainer_transition_{maintainer.state}_to_{target.value}_invalid"
                )
            maintainer.state = target.value
            setattr(maintainer, timestamp_field, timestamp)
            repository.add_audit_event(
                _audit_event(
                    maintainer_id=maintainer.id,
                    actor_id=actor_id,
                    event_type=event_type,
                    reason=reason,
                    payload={"from": required.value, "to": target.value},
                    now=timestamp,
                )
            )
        return _status(maintainer)

    async def _terminal_transition(
        self,
        maintainer_id: UUID,
        *,
        actor_id: UUID,
        reason: str,
        target: FederationLifecycleState,
        event_type: FederationEventType,
        timestamp_field: str,
        failure_code: str,
        now: datetime | None,
    ) -> MaintainerStatus:
        timestamp = _aware(now or datetime.now(UTC))
        _require_reason(reason)
        async with self._factory() as session, session.begin():
            repository = FederationRepository(session)
            maintainer = await repository.maintainer(maintainer_id, lock=True)
            await _require_active_steward(
                repository,
                actor_id=actor_id,
                pack_id=maintainer.pack_id,
                at=timestamp,
            )
            if maintainer.state != FederationLifecycleState.ACTIVE.value:
                raise FederationError(
                    f"maintainer_transition_{maintainer.state}_to_{target.value}_invalid"
                )
            maintainer.state = target.value
            setattr(maintainer, timestamp_field, timestamp)
            blocked = await repository.block_scoped_publications(
                repository=maintainer.repository,
                pack_id=maintainer.pack_id,
                code=failure_code,
                now=timestamp,
            )
            repository.add_audit_event(
                _audit_event(
                    maintainer_id=maintainer.id,
                    actor_id=actor_id,
                    event_type=event_type,
                    reason=reason,
                    payload={
                        "from": FederationLifecycleState.ACTIVE.value,
                        "to": target.value,
                        "blocked_publications": blocked,
                    },
                    now=timestamp,
                )
            )
        return _status(maintainer)

    def _require_allowed_scope(self, scope: FederationScope) -> None:
        if scope not in self._allowed_scopes:
            raise FederationError("federation_scope_not_invited", exit_code=3)


def _token_hash(token: str) -> bytes:
    if len(token) < 32 or len(token) > 256 or any(character.isspace() for character in token):
        raise FederationError("invitation_invalid", exit_code=3)
    return hashlib.sha256(token.encode("utf-8")).digest()


def _require_role_key_binding(encoded_public_key: str, claimed_fingerprint: str) -> None:
    try:
        actual_fingerprint = public_key_fingerprint(encoded_public_key)
    except ValueError as error:
        raise FederationError("role_key_invalid", exit_code=3) from error
    if not hmac.compare_digest(actual_fingerprint, claimed_fingerprint):
        raise FederationError("role_key_fingerprint_mismatch", exit_code=3)


async def _require_active_steward(
    repository: FederationRepository,
    *,
    actor_id: UUID,
    pack_id: str,
    at: datetime,
) -> None:
    if not await repository.actor_is_active_human_steward(
        actor_id=actor_id,
        pack_id=pack_id,
        at=at,
    ):
        raise FederationError("federation_steward_not_active", exit_code=3)


def _validate_invitation(
    invitation: FederationInvitation,
    scope: FederationScope,
    now: datetime,
) -> None:
    if invitation.consumed_at is not None:
        raise FederationError("invitation_already_consumed")
    if invitation.expires_at <= now:
        raise FederationError("invitation_expired", exit_code=3)
    if (
        invitation.github_account_id != scope.github_account_id
        or invitation.repository_id != scope.repository_id
        or invitation.repository != scope.repository
        or invitation.pack_id != scope.pack_id
    ):
        raise FederationError("invitation_identity_mismatch", exit_code=3)


def _audit_event(
    *,
    actor_id: UUID,
    event_type: FederationEventType,
    reason: str,
    payload: object,
    now: datetime,
    maintainer_id: UUID | None = None,
    invitation_id: UUID | None = None,
) -> FederationAuditEvent:
    _require_reason(reason)
    return FederationAuditEvent(
        maintainer_id=maintainer_id,
        invitation_id=invitation_id,
        event_type=event_type.value,
        actor_id=actor_id,
        reason=reason,
        payload_digest=hashlib.sha256(canonical_json(payload)).hexdigest(),
        created_at=now,
    )


def _status(maintainer: FederationMaintainer) -> MaintainerStatus:
    return MaintainerStatus(
        maintainer_id=maintainer.id,
        state=FederationLifecycleState(maintainer.state),
        github_account_id=maintainer.github_account_id,
        github_login=maintainer.github_login,
        repository_id=maintainer.repository_id,
        repository=maintainer.repository,
        pack_id=maintainer.pack_id,
        current_role_key_id=maintainer.current_role_key_id,
        current_role_key_fingerprint=maintainer.current_role_key_fingerprint,
        requested_at=maintainer.requested_at,
        verified_at=maintainer.verified_at,
        activated_at=maintainer.activated_at,
        quarantined_at=maintainer.quarantined_at,
        revoked_at=maintainer.revoked_at,
    )


def _require_reason(reason: str) -> None:
    if not reason.strip() or len(reason) > 1000:
        raise FederationError("federation_reason_invalid", exit_code=2)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise FederationError("federation_time_must_include_timezone", exit_code=2)
    return value
