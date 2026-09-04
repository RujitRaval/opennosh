from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.governance.contracts import GovernanceRole
from opennosh_api.governance.models import GovernanceRecusal, GovernanceRoleAssignment
from opennosh_api.reuse.contracts import (
    ReuseDeclarationCreate,
    ReuseDeclarationFields,
    ReuseDeclarationPatch,
    ReuseDeclarationState,
    ReuseDependencyInput,
    ReuseEventType,
    ReuseEvidenceStatus,
    ReuseRegionLevel,
    ReuseVerificationEvidence,
    normalized_key,
)
from opennosh_api.reuse.models import ReuseDeclaration, ReuseDeclarationEvent, ReuseDependency


class ReuseRegistryError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


REUSE_GOVERNANCE_SCOPE = "opennosh-reuse-registry"
REUSE_EVIDENCE_MAX_AGE = timedelta(days=30)


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Reuse registry time must include a timezone")


def _key_hash(value: UUID) -> str:
    return hashlib.sha256(str(value).encode()).hexdigest()


def _request_hash(value: object) -> str:
    material = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(material.encode()).hexdigest()


async def _idempotent_result(
    session: AsyncSession,
    *,
    actor_id: UUID,
    idempotency_key_hash: str,
    request_hash: str,
) -> ReuseDeclaration | None:
    event = await session.scalar(
        select(ReuseDeclarationEvent).where(
            ReuseDeclarationEvent.actor_id == actor_id,
            ReuseDeclarationEvent.idempotency_key_hash == idempotency_key_hash,
        )
    )
    if event is None:
        return None
    if event.request_hash != request_hash:
        raise ReuseRegistryError("reuse_idempotency_payload_mismatch")
    declaration = await session.scalar(
        select(ReuseDeclaration).where(ReuseDeclaration.id == event.declaration_id)
    )
    if declaration is None:
        raise ReuseRegistryError("reuse_audit_proof_unavailable")
    return declaration


async def _owned_declaration(
    session: AsyncSession,
    *,
    declaration_id: UUID,
    owner_actor_id: UUID,
    for_update: bool,
) -> ReuseDeclaration:
    statement = select(ReuseDeclaration).where(
        ReuseDeclaration.id == declaration_id,
        ReuseDeclaration.owner_actor_id == owner_actor_id,
    )
    if for_update:
        statement = statement.with_for_update()
    declaration = await session.scalar(statement)
    if declaration is None:
        raise ReuseRegistryError("reuse_declaration_not_found")
    return declaration


def _append_event(
    session: AsyncSession,
    *,
    declaration: ReuseDeclaration,
    actor_id: UUID,
    event_type: ReuseEventType,
    idempotency_key_hash: str,
    request_hash: str,
    reason: str | None,
    evidence_json: dict[str, object] | None = None,
    now: datetime,
    event_id_generator: Callable[[], UUID],
) -> ReuseDeclarationEvent:
    event = ReuseDeclarationEvent(
        id=event_id_generator(),
        declaration_id=declaration.id,
        actor_id=actor_id,
        event_type=event_type.value,
        declaration_revision=declaration.revision,
        idempotency_key_hash=idempotency_key_hash,
        request_hash=request_hash,
        evidence_json={} if evidence_json is None else evidence_json,
        reason=reason,
        occurred_at=now,
    )
    session.add(event)
    return event


DependencyProofResolver = Callable[[ReuseDependencyInput], Awaitable[bool]]


async def _validate_dependency_proofs(
    dependencies: tuple[ReuseDependencyInput, ...],
    *,
    resolver: DependencyProofResolver | None,
) -> None:
    if not dependencies:
        return
    if resolver is None:
        raise ReuseRegistryError("reuse_dependency_proof_unavailable")
    for dependency in dependencies:
        if not await resolver(dependency):
            raise ReuseRegistryError("reuse_dependency_proof_invalid")


async def _require_registry_steward(
    session: AsyncSession,
    *,
    actor_id: UUID,
    now: datetime,
) -> None:
    role_id = await session.scalar(
        select(GovernanceRoleAssignment.id).where(
            GovernanceRoleAssignment.pack_id == REUSE_GOVERNANCE_SCOPE,
            GovernanceRoleAssignment.actor_id == actor_id,
            GovernanceRoleAssignment.role == GovernanceRole.STEWARD.value,
            GovernanceRoleAssignment.granted_at <= now,
            (
                GovernanceRoleAssignment.revoked_at.is_(None)
                | (GovernanceRoleAssignment.revoked_at > now)
            ),
        )
    )
    if role_id is None:
        raise ReuseRegistryError("reuse_steward_role_not_active")
    recusal_id = await session.scalar(
        select(GovernanceRecusal.id).where(
            GovernanceRecusal.pack_id == REUSE_GOVERNANCE_SCOPE,
            GovernanceRecusal.actor_id == actor_id,
            GovernanceRecusal.recused_at <= now,
        )
    )
    if recusal_id is not None:
        raise ReuseRegistryError("reuse_steward_recused")


async def list_reviewable_declarations(
    session: AsyncSession,
    *,
    steward_actor_id: UUID,
    now: datetime,
    limit: int,
) -> tuple[ReuseDeclaration, ...]:
    _require_aware(now)
    await _require_registry_steward(session, actor_id=steward_actor_id, now=now)
    rows = await session.scalars(
        select(ReuseDeclaration)
        .where(
            ReuseDeclaration.state == ReuseDeclarationState.VERIFICATION_PENDING.value,
            ReuseDeclaration.owner_actor_id != steward_actor_id,
        )
        .order_by(ReuseDeclaration.updated_at, ReuseDeclaration.id)
        .limit(limit)
    )
    return tuple(rows)


def _validate_verification_evidence(
    evidence: ReuseVerificationEvidence,
    *,
    now: datetime,
) -> None:
    if evidence.status is not ReuseEvidenceStatus.ACCESSIBLE:
        raise ReuseRegistryError("reuse_verification_evidence_inaccessible")
    if evidence.observed_at > now:
        raise ReuseRegistryError("reuse_verification_evidence_from_future")
    if evidence.observed_at < now - REUSE_EVIDENCE_MAX_AGE:
        raise ReuseRegistryError("reuse_verification_evidence_stale")


async def review_declaration(
    session: AsyncSession,
    *,
    declaration_id: UUID,
    steward_actor_id: UUID,
    expected_revision: int,
    action: ReuseEventType,
    idempotency_key: UUID,
    reason: str,
    evidence: ReuseVerificationEvidence | None,
    dependencies: tuple[ReuseDependencyInput, ...] = (),
    dependency_resolver: DependencyProofResolver | None = None,
    now: datetime,
    event_id_generator: Callable[[], UUID] = uuid4,
) -> ReuseDeclaration:
    _require_aware(now)
    if action not in {
        ReuseEventType.VERIFIED,
        ReuseEventType.CHANGES_REQUESTED,
        ReuseEventType.REJECTED,
    }:
        raise ValueError("Unsupported reuse review transition")
    if action is ReuseEventType.VERIFIED:
        if evidence is None:
            raise ReuseRegistryError("reuse_verification_evidence_unavailable")
        _validate_verification_evidence(evidence, now=now)
    elif evidence is not None:
        raise ValueError("Non-verification reviews cannot attach verification evidence")
    elif dependencies:
        raise ValueError("Non-verification reviews cannot attach dependencies")

    await _require_registry_steward(session, actor_id=steward_actor_id, now=now)
    key_hash = _key_hash(idempotency_key)
    request_hash = _request_hash(
        {
            "action": action.value,
            "actor_id": steward_actor_id,
            "declaration_id": declaration_id,
            "expected_revision": expected_revision,
            "reason": reason,
            "evidence": None if evidence is None else evidence.model_dump(mode="json"),
            "dependencies": [item.model_dump(mode="json") for item in dependencies],
        }
    )
    existing = await _idempotent_result(
        session,
        actor_id=steward_actor_id,
        idempotency_key_hash=key_hash,
        request_hash=request_hash,
    )
    if existing is not None:
        return existing

    declaration = await session.scalar(
        select(ReuseDeclaration).where(ReuseDeclaration.id == declaration_id).with_for_update()
    )
    if declaration is None:
        raise ReuseRegistryError("reuse_declaration_not_found")
    if declaration.owner_actor_id == steward_actor_id:
        raise ReuseRegistryError("reuse_self_review_prohibited")
    if declaration.revision != expected_revision:
        raise ReuseRegistryError("reuse_revision_conflict")
    if declaration.state != ReuseDeclarationState.VERIFICATION_PENDING.value:
        raise ReuseRegistryError("reuse_review_transition_not_allowed")
    if action is ReuseEventType.VERIFIED:
        await _validate_dependency_proofs(dependencies, resolver=dependency_resolver)

    declaration.state = (
        ReuseDeclarationState.VERIFIED.value
        if action is ReuseEventType.VERIFIED
        else ReuseDeclarationState.COMMUNITY_DECLARED.value
    )
    declaration.revision += 1
    declaration.updated_at = now
    event = _append_event(
        session,
        declaration=declaration,
        actor_id=steward_actor_id,
        event_type=action,
        idempotency_key_hash=key_hash,
        request_hash=request_hash,
        reason=reason,
        evidence_json={} if evidence is None else evidence.model_dump(mode="json"),
        now=now,
        event_id_generator=event_id_generator,
    )
    await session.flush()
    if dependencies:
        for dependency in dependencies:
            existing_dependency = await session.scalar(
                select(ReuseDependency).where(
                    ReuseDependency.declaration_id == declaration.id,
                    ReuseDependency.source_pack_id == dependency.source_pack_id,
                    ReuseDependency.source_release_id == dependency.source_release_id,
                    ReuseDependency.dependency_kind == dependency.dependency_kind.value,
                )
            )
            if existing_dependency is None:
                session.add(
                    ReuseDependency(
                        id=event_id_generator(),
                        declaration_id=declaration.id,
                        source_pack_id=dependency.source_pack_id,
                        source_release_id=dependency.source_release_id,
                        source_artifact_digest=dependency.source_artifact_digest,
                        dependency_kind=dependency.dependency_kind.value,
                        evidence_event_id=event.id,
                        created_at=now,
                    )
                )
            else:
                await session.execute(
                    update(ReuseDependency)
                    .where(ReuseDependency.id == existing_dependency.id)
                    .values(
                        source_artifact_digest=dependency.source_artifact_digest,
                        evidence_event_id=event.id,
                    )
                )
        await session.flush()
    return declaration


async def list_public_dependencies(
    session: AsyncSession,
    *,
    limit: int = 100,
) -> tuple[tuple[ReuseDependency, ReuseDeclaration, ReuseDeclarationEvent], ...]:
    result = await session.execute(
        select(ReuseDependency, ReuseDeclaration, ReuseDeclarationEvent)
        .join(ReuseDeclaration, ReuseDeclaration.id == ReuseDependency.declaration_id)
        .join(ReuseDeclarationEvent, ReuseDeclarationEvent.id == ReuseDependency.evidence_event_id)
        .where(
            ReuseDeclaration.state == ReuseDeclarationState.VERIFIED.value,
            ReuseDeclarationEvent.declaration_id == ReuseDeclaration.id,
            ReuseDeclarationEvent.declaration_revision == ReuseDeclaration.revision,
            ReuseDeclarationEvent.event_type == ReuseEventType.VERIFIED.value,
        )
        .order_by(
            ReuseDependency.source_pack_id,
            ReuseDependency.source_release_id,
            ReuseDependency.dependency_kind,
            ReuseDeclaration.project_key,
            ReuseDeclaration.id,
        )
        .limit(limit)
    )
    return tuple((row[0], row[1], row[2]) for row in result.all())


async def list_public_declarations(
    session: AsyncSession,
    *,
    limit: int = 100,
) -> tuple[tuple[ReuseDeclaration, ReuseDeclarationEvent | None], ...]:
    result = await session.execute(
        select(ReuseDeclaration, ReuseDeclarationEvent)
        .outerjoin(
            ReuseDeclarationEvent,
            (ReuseDeclarationEvent.declaration_id == ReuseDeclaration.id)
            & (ReuseDeclarationEvent.declaration_revision == ReuseDeclaration.revision)
            & (ReuseDeclarationEvent.event_type == ReuseEventType.VERIFIED.value),
        )
        .where(ReuseDeclaration.state != ReuseDeclarationState.WITHDRAWN.value)
        .order_by(ReuseDeclaration.updated_at.desc(), ReuseDeclaration.id)
        .limit(limit)
    )
    return tuple((row[0], row[1]) for row in result.all())


async def read_public_declaration(
    session: AsyncSession,
    *,
    declaration_id: UUID,
) -> tuple[ReuseDeclaration, ReuseDeclarationEvent | None]:
    row = (
        await session.execute(
            select(ReuseDeclaration, ReuseDeclarationEvent)
            .outerjoin(
                ReuseDeclarationEvent,
                (ReuseDeclarationEvent.declaration_id == ReuseDeclaration.id)
                & (ReuseDeclarationEvent.declaration_revision == ReuseDeclaration.revision)
                & (ReuseDeclarationEvent.event_type == ReuseEventType.VERIFIED.value),
            )
            .where(
                ReuseDeclaration.id == declaration_id,
                ReuseDeclaration.state != ReuseDeclarationState.WITHDRAWN.value,
            )
        )
    ).one_or_none()
    if row is None:
        raise ReuseRegistryError("reuse_declaration_not_found")
    return row[0], row[1]


async def create_declaration(
    session: AsyncSession,
    *,
    owner_actor_id: UUID,
    request: ReuseDeclarationCreate,
    idempotency_key: UUID,
    now: datetime,
    declaration_id_generator: Callable[[], UUID] = uuid4,
    event_id_generator: Callable[[], UUID] = uuid4,
) -> tuple[ReuseDeclaration, bool]:
    _require_aware(now)
    key_hash = _key_hash(idempotency_key)
    request_hash = _request_hash(
        {
            "action": "create",
            "actor_id": owner_actor_id,
            "request": request.model_dump(mode="json"),
        }
    )
    existing = await _idempotent_result(
        session,
        actor_id=owner_actor_id,
        idempotency_key_hash=key_hash,
        request_hash=request_hash,
    )
    if existing is not None:
        return existing, False

    organization_key = normalized_key(request.organization_name)
    project_key = normalized_key(request.project_name)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
        {"scope": f"opennosh:reuse:{owner_actor_id}:{organization_key}:{project_key}"},
    )
    duplicate = await session.scalar(
        select(ReuseDeclaration.id).where(
            ReuseDeclaration.owner_actor_id == owner_actor_id,
            ReuseDeclaration.organization_key == organization_key,
            ReuseDeclaration.project_key == project_key,
        )
    )
    if duplicate is not None:
        raise ReuseRegistryError("reuse_declaration_already_exists")

    declaration = ReuseDeclaration(
        id=declaration_id_generator(),
        owner_actor_id=owner_actor_id,
        organization_name=request.organization_name,
        organization_key=organization_key,
        project_name=request.project_name,
        project_key=project_key,
        project_url=request.project_url,
        use_case=request.use_case,
        region_level=request.region_level.value if request.region_level is not None else None,
        region_code=request.region_code,
        state=ReuseDeclarationState.COMMUNITY_DECLARED.value,
        revision=1,
        created_at=now,
        updated_at=now,
        withdrawn_at=None,
    )
    session.add(declaration)
    _append_event(
        session,
        declaration=declaration,
        actor_id=owner_actor_id,
        event_type=ReuseEventType.DECLARED,
        idempotency_key_hash=key_hash,
        request_hash=request_hash,
        reason=None,
        now=now,
        event_id_generator=event_id_generator,
    )
    await session.flush()
    return declaration, True


async def list_owned_declarations(
    session: AsyncSession,
    *,
    owner_actor_id: UUID,
    limit: int,
) -> tuple[ReuseDeclaration, ...]:
    rows = await session.scalars(
        select(ReuseDeclaration)
        .where(ReuseDeclaration.owner_actor_id == owner_actor_id)
        .order_by(ReuseDeclaration.updated_at.desc(), ReuseDeclaration.id)
        .limit(limit)
    )
    return tuple(rows)


async def patch_declaration(
    session: AsyncSession,
    *,
    declaration_id: UUID,
    owner_actor_id: UUID,
    expected_revision: int,
    request: ReuseDeclarationPatch,
    idempotency_key: UUID,
    now: datetime,
    event_id_generator: Callable[[], UUID] = uuid4,
) -> ReuseDeclaration:
    _require_aware(now)
    key_hash = _key_hash(idempotency_key)
    request_hash = _request_hash(
        {
            "action": "edit",
            "actor_id": owner_actor_id,
            "declaration_id": declaration_id,
            "expected_revision": expected_revision,
            "request": request.model_dump(mode="json"),
        }
    )
    existing = await _idempotent_result(
        session,
        actor_id=owner_actor_id,
        idempotency_key_hash=key_hash,
        request_hash=request_hash,
    )
    if existing is not None:
        return existing
    declaration = await _owned_declaration(
        session,
        declaration_id=declaration_id,
        owner_actor_id=owner_actor_id,
        for_update=True,
    )
    if declaration.revision != expected_revision:
        raise ReuseRegistryError("reuse_revision_conflict")
    if declaration.state == ReuseDeclarationState.WITHDRAWN.value:
        raise ReuseRegistryError("reuse_withdrawn_requires_restore")

    fields = ReuseDeclarationFields(
        organization_name=request.organization_name or declaration.organization_name,
        project_name=request.project_name or declaration.project_name,
        project_url=(
            None
            if request.clear_project_url
            else request.project_url
            if request.project_url is not None
            else declaration.project_url
        ),
        use_case=request.use_case or declaration.use_case,
        region_level=(
            None
            if request.clear_region
            else request.region_level
            if request.region_level is not None
            else ReuseRegionLevel(declaration.region_level)
            if declaration.region_level is not None
            else None
        ),
        region_code=(
            None
            if request.clear_region
            else request.region_code
            if request.region_code is not None
            else declaration.region_code
        ),
    )
    declaration.organization_name = fields.organization_name
    declaration.organization_key = normalized_key(fields.organization_name)
    declaration.project_name = fields.project_name
    declaration.project_key = normalized_key(fields.project_name)
    declaration.project_url = fields.project_url
    declaration.use_case = fields.use_case
    declaration.region_level = (
        fields.region_level.value if fields.region_level is not None else None
    )
    declaration.region_code = fields.region_code
    declaration.state = ReuseDeclarationState.COMMUNITY_DECLARED.value
    declaration.revision += 1
    declaration.updated_at = now
    _append_event(
        session,
        declaration=declaration,
        actor_id=owner_actor_id,
        event_type=ReuseEventType.EDITED,
        idempotency_key_hash=key_hash,
        request_hash=request_hash,
        reason=None,
        now=now,
        event_id_generator=event_id_generator,
    )
    await session.flush()
    return declaration


async def transition_declaration(
    session: AsyncSession,
    *,
    declaration_id: UUID,
    owner_actor_id: UUID,
    expected_revision: int,
    action: ReuseEventType,
    idempotency_key: UUID,
    reason: str | None,
    now: datetime,
    event_id_generator: Callable[[], UUID] = uuid4,
) -> ReuseDeclaration:
    _require_aware(now)
    if action not in {
        ReuseEventType.SUBMITTED,
        ReuseEventType.WITHDRAWN,
        ReuseEventType.RESTORED,
    }:
        raise ValueError("Unsupported owner reuse transition")
    key_hash = _key_hash(idempotency_key)
    request_hash = _request_hash(
        {
            "action": action.value,
            "actor_id": owner_actor_id,
            "declaration_id": declaration_id,
            "expected_revision": expected_revision,
            "reason": reason,
        }
    )
    existing = await _idempotent_result(
        session,
        actor_id=owner_actor_id,
        idempotency_key_hash=key_hash,
        request_hash=request_hash,
    )
    if existing is not None:
        return existing
    declaration = await _owned_declaration(
        session,
        declaration_id=declaration_id,
        owner_actor_id=owner_actor_id,
        for_update=True,
    )
    if declaration.revision != expected_revision:
        raise ReuseRegistryError("reuse_revision_conflict")

    current = ReuseDeclarationState(declaration.state)
    if action is ReuseEventType.SUBMITTED and current is ReuseDeclarationState.COMMUNITY_DECLARED:
        next_state = ReuseDeclarationState.VERIFICATION_PENDING
        withdrawn_at = None
    elif action is ReuseEventType.WITHDRAWN and current is not ReuseDeclarationState.WITHDRAWN:
        next_state = ReuseDeclarationState.WITHDRAWN
        withdrawn_at = now
    elif action is ReuseEventType.RESTORED and current is ReuseDeclarationState.WITHDRAWN:
        next_state = ReuseDeclarationState.COMMUNITY_DECLARED
        withdrawn_at = None
    else:
        raise ReuseRegistryError("reuse_transition_not_allowed")

    declaration.state = next_state.value
    declaration.withdrawn_at = withdrawn_at
    declaration.revision += 1
    declaration.updated_at = now
    _append_event(
        session,
        declaration=declaration,
        actor_id=owner_actor_id,
        event_type=action,
        idempotency_key_hash=key_hash,
        request_hash=request_hash,
        reason=reason,
        now=now,
        event_id_generator=event_id_generator,
    )
    await session.flush()
    return declaration


async def read_owned_declaration(
    session: AsyncSession,
    *,
    declaration_id: UUID,
    owner_actor_id: UUID,
) -> ReuseDeclaration:
    return await _owned_declaration(
        session,
        declaration_id=declaration_id,
        owner_actor_id=owner_actor_id,
        for_update=False,
    )


__all__ = [
    "REUSE_EVIDENCE_MAX_AGE",
    "REUSE_GOVERNANCE_SCOPE",
    "ReuseRegistryError",
    "create_declaration",
    "list_owned_declarations",
    "list_public_dependencies",
    "list_public_declarations",
    "list_reviewable_declarations",
    "patch_declaration",
    "read_owned_declaration",
    "read_public_declaration",
    "review_declaration",
    "transition_declaration",
]
