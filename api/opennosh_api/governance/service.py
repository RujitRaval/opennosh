from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.contributions.models import ContributionDraft
from opennosh_api.evidence.contracts import manifest_digest
from opennosh_api.evidence.policy import EvidenceDurabilityError
from opennosh_api.evidence.repository import require_verified_evidence
from opennosh_api.governance.contracts import (
    CANONICAL_FORGE_TARGET,
    PROTECTED_STATUS_CHECKS,
    ApprovedChangeSet,
    GovernanceDecisionOutcome,
    GovernanceRole,
)
from opennosh_api.governance.models import (
    GovernanceDecision,
    GovernanceMergeAuthorization,
    GovernancePublicationIntervention,
    GovernancePublicationPause,
    GovernanceRecusal,
    GovernanceRoleAssignment,
)
from opennosh_api.jobs import JobQueue
from opennosh_api.publication.models import PublicationIntent
from opennosh_api.publication.service import (
    CreatePublicationIntent,
    create_publication_intent,
)


class GovernanceDecisionError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ApproveContribution:
    source_draft_id: UUID
    deciding_actor_id: UUID
    approved_changes: ApprovedChangeSet
    record_id: str
    expected_base_commit: str
    required_checks: tuple[str, ...]
    forge_target: str
    reason: str

    def __post_init__(self) -> None:
        if not self.record_id or len(self.record_id) > 160:
            raise ValueError("Governed approval requires a bounded record ID")
        if not self.reason or len(self.reason) > 2000:
            raise ValueError("Governed approval requires a bounded reason")
        if len(self.expected_base_commit) not in {40, 64} or any(
            character not in "0123456789abcdef" for character in self.expected_base_commit
        ):
            raise ValueError("Expected base commit must be a lowercase Git hash")
        if self.required_checks != PROTECTED_STATUS_CHECKS:
            raise ValueError("Governed approval requires the canonical protected checks")
        if self.forge_target != CANONICAL_FORGE_TARGET:
            raise ValueError("Governed approval must target the canonical repository")


async def approve_contribution(
    session: AsyncSession,
    queue: JobQueue,
    command: ApproveContribution,
    *,
    now: datetime,
    decision_id_generator: Callable[[], UUID] = uuid4,
    publication_intent_id_generator: Callable[[], UUID] = uuid4,
) -> tuple[GovernanceDecision, PublicationIntent]:
    """Atomically record the approval, publication intent, and queue wake-up."""

    _require_aware(now)
    draft = await session.scalar(
        select(ContributionDraft)
        .where(ContributionDraft.id == command.source_draft_id)
        .with_for_update()
    )
    if draft is None:
        raise GovernanceDecisionError("contribution_not_found")
    if draft.review_state != "in_review":
        raise GovernanceDecisionError("contribution_not_in_review")
    if draft.user_id == command.deciding_actor_id:
        raise GovernanceDecisionError("self_review_prohibited")
    if draft.fields_json.get("pack_id") != command.approved_changes.pack_id:
        raise GovernanceDecisionError("pack_scope_mismatch")

    role = await session.scalar(
        select(GovernanceRoleAssignment).where(
            GovernanceRoleAssignment.pack_id == command.approved_changes.pack_id,
            GovernanceRoleAssignment.actor_id == command.deciding_actor_id,
            GovernanceRoleAssignment.role == GovernanceRole.STEWARD.value,
            GovernanceRoleAssignment.granted_at <= now,
            (
                GovernanceRoleAssignment.revoked_at.is_(None)
                | (GovernanceRoleAssignment.revoked_at > now)
            ),
        )
    )
    if role is None:
        raise GovernanceDecisionError("steward_role_not_active")
    recusal = await session.scalar(
        select(GovernanceRecusal.id).where(
            GovernanceRecusal.source_draft_id == draft.id,
            GovernanceRecusal.actor_id == command.deciding_actor_id,
            GovernanceRecusal.recused_at <= now,
        )
    )
    if recusal is not None:
        raise GovernanceDecisionError("steward_recused")
    pause = await session.scalar(
        select(GovernancePublicationPause.id).where(
            GovernancePublicationPause.pack_id == command.approved_changes.pack_id,
            GovernancePublicationPause.paused_at <= now,
            (
                GovernancePublicationPause.resumed_at.is_(None)
                | (GovernancePublicationPause.resumed_at > now)
            ),
        )
    )
    if pause is not None:
        raise GovernanceDecisionError("publication_paused")
    try:
        evidence = await require_verified_evidence(
            session,
            source_draft_id=draft.id,
            source_draft_version=draft.draft_version,
        )
    except EvidenceDurabilityError as error:
        raise GovernanceDecisionError(error.code) from error

    decision = GovernanceDecision(
        id=decision_id_generator(),
        source_draft_id=draft.id,
        source_draft_version=draft.draft_version,
        pack_id=command.approved_changes.pack_id,
        record_id=command.record_id,
        contributor_actor_id=draft.user_id,
        deciding_actor_id=command.deciding_actor_id,
        outcome="approved",
        reason=command.reason,
        approved_payload_digest=command.approved_changes.digest,
        approved_changes_json=command.approved_changes.as_json(),
        expected_base_commit=command.expected_base_commit,
        required_checks_json=list(command.required_checks),
        forge_target=command.forge_target,
        decided_at=now,
    )
    session.add(decision)
    await session.flush()
    intent = await create_publication_intent(
        session,
        queue,
        CreatePublicationIntent(
            source_draft_id=draft.id,
            source_draft_version=draft.draft_version,
            reviewed_decision_id=decision.id,
            approving_actor_id=command.deciding_actor_id,
            pack_id=command.approved_changes.pack_id,
            record_id=command.record_id,
            approved_payload_digest=command.approved_changes.digest,
            expected_base_commit=command.expected_base_commit,
            required_checks=command.required_checks,
            forge_target=command.forge_target,
            idempotency_key=f"governance-decision:{decision.id}",
            evidence_manifest_digests=(manifest_digest(evidence.manifest),),
            evidence_acknowledgements=evidence.acknowledgements,
        ),
        now=now,
        id_generator=publication_intent_id_generator,
    )
    draft.review_state = "publication_pending"
    draft.updated_at = now
    await session.flush()
    return decision, intent


def grant_steward(
    *,
    pack_id: str,
    actor_id: UUID,
    granted_by_actor_id: UUID,
    reason: str,
    now: datetime,
) -> GovernanceRoleAssignment:
    _require_aware(now)
    _require_reason(reason)
    if not pack_id or len(pack_id) > 160:
        raise ValueError("Steward grant requires a bounded pack ID")
    return GovernanceRoleAssignment(
        pack_id=pack_id,
        actor_id=actor_id,
        role=GovernanceRole.STEWARD.value,
        granted_by_actor_id=granted_by_actor_id,
        grant_reason=reason,
        granted_at=now,
    )


async def revoke_steward(
    session: AsyncSession,
    assignment_id: UUID,
    *,
    revoked_by_actor_id: UUID,
    reason: str,
    now: datetime,
) -> GovernanceRoleAssignment:
    _require_aware(now)
    _require_reason(reason)
    assignment = await session.scalar(
        select(GovernanceRoleAssignment)
        .where(GovernanceRoleAssignment.id == assignment_id)
        .with_for_update()
    )
    if assignment is None:
        raise GovernanceDecisionError("steward_role_not_found")
    if assignment.revoked_at is not None:
        raise GovernanceDecisionError("steward_role_already_revoked")
    if now < assignment.granted_at:
        raise GovernanceDecisionError("revocation_before_grant")
    assignment.revoked_by_actor_id = revoked_by_actor_id
    assignment.revocation_reason = reason
    assignment.revoked_at = now
    await session.flush()
    return assignment


def recuse_steward(
    *,
    pack_id: str,
    source_draft_id: UUID,
    actor_id: UUID,
    reason: str,
    now: datetime,
) -> GovernanceRecusal:
    _require_aware(now)
    _require_reason(reason)
    return GovernanceRecusal(
        pack_id=pack_id,
        source_draft_id=source_draft_id,
        actor_id=actor_id,
        reason=reason,
        recused_at=now,
    )


async def pause_publication(
    session: AsyncSession,
    *,
    pack_id: str,
    paused_by_actor_id: UUID,
    reason: str,
    now: datetime,
) -> GovernancePublicationPause:
    _require_aware(now)
    _require_reason(reason)
    role = await session.scalar(
        select(GovernanceRoleAssignment.id).where(
            GovernanceRoleAssignment.pack_id == pack_id,
            GovernanceRoleAssignment.actor_id == paused_by_actor_id,
            GovernanceRoleAssignment.role == GovernanceRole.STEWARD.value,
            GovernanceRoleAssignment.granted_at <= now,
            (
                GovernanceRoleAssignment.revoked_at.is_(None)
                | (GovernanceRoleAssignment.revoked_at > now)
            ),
        )
    )
    if role is None:
        raise GovernanceDecisionError("steward_role_not_active")
    pause = GovernancePublicationPause(
        pack_id=pack_id,
        paused_by_actor_id=paused_by_actor_id,
        pause_reason=reason,
        paused_at=now,
        updated_at=now,
    )
    try:
        async with session.begin_nested():
            session.add(pause)
            await session.flush()
    except IntegrityError as error:
        raise GovernanceDecisionError("publication_already_paused") from error
    return pause


async def resume_publication(
    session: AsyncSession,
    pause_id: UUID,
    *,
    resumed_by_actor_id: UUID,
    reason: str,
    now: datetime,
) -> GovernancePublicationPause:
    _require_aware(now)
    _require_reason(reason)
    pause = await session.scalar(
        select(GovernancePublicationPause)
        .where(GovernancePublicationPause.id == pause_id)
        .with_for_update()
    )
    if pause is None:
        raise GovernanceDecisionError("publication_pause_not_found")
    if pause.resumed_at is not None:
        raise GovernanceDecisionError("publication_pause_already_resumed")
    if now < pause.paused_at:
        raise GovernanceDecisionError("resume_before_pause")
    if pause.paused_by_actor_id == resumed_by_actor_id:
        raise GovernanceDecisionError("publication_resume_requires_second_steward")
    role = await session.scalar(
        select(GovernanceRoleAssignment.id).where(
            GovernanceRoleAssignment.pack_id == pause.pack_id,
            GovernanceRoleAssignment.actor_id == resumed_by_actor_id,
            GovernanceRoleAssignment.role == GovernanceRole.STEWARD.value,
            GovernanceRoleAssignment.granted_at <= now,
            (
                GovernanceRoleAssignment.revoked_at.is_(None)
                | (GovernanceRoleAssignment.revoked_at > now)
            ),
        )
    )
    if role is None:
        raise GovernanceDecisionError("steward_role_not_active")
    pause.resumed_by_actor_id = resumed_by_actor_id
    pause.resume_reason = reason
    pause.resumed_at = now
    pause.updated_at = now
    await session.flush()
    return pause


async def intervene_publication(
    session: AsyncSession,
    publication_intent_id: UUID,
    *,
    actor_id: UUID,
    action: GovernanceDecisionOutcome,
    reason: str,
    now: datetime,
) -> GovernancePublicationIntervention:
    """Atomically stop a governed merge and preserve the intervention audit record."""

    _require_aware(now)
    _require_reason(reason)
    if action not in {
        GovernanceDecisionOutcome.CHANGES_REQUESTED,
        GovernanceDecisionOutcome.REJECTED,
    }:
        raise ValueError("Publication intervention must reject or request changes")
    intent = await session.scalar(
        select(PublicationIntent)
        .where(PublicationIntent.id == publication_intent_id)
        .with_for_update()
    )
    if intent is None:
        raise GovernanceDecisionError("publication_not_found")
    if intent.state == "published":
        raise GovernanceDecisionError("published_contribution_is_immutable")
    authorization = await session.scalar(
        select(GovernanceMergeAuthorization.id).where(
            GovernanceMergeAuthorization.publication_intent_id == intent.id
        )
    )
    if authorization is not None:
        raise GovernanceDecisionError("merge_authorization_committed")
    existing = await session.scalar(
        select(GovernancePublicationIntervention.id).where(
            GovernancePublicationIntervention.publication_intent_id == intent.id
        )
    )
    if existing is not None:
        raise GovernanceDecisionError("publication_already_intervened")
    decision = await session.scalar(
        select(GovernanceDecision).where(GovernanceDecision.id == intent.reviewed_decision_id)
    )
    if decision is None:
        raise GovernanceDecisionError("governance_decision_not_found")
    if decision.contributor_actor_id == actor_id:
        raise GovernanceDecisionError("self_review_prohibited")
    role = await session.scalar(
        select(GovernanceRoleAssignment.id).where(
            GovernanceRoleAssignment.pack_id == decision.pack_id,
            GovernanceRoleAssignment.actor_id == actor_id,
            GovernanceRoleAssignment.role == GovernanceRole.STEWARD.value,
            GovernanceRoleAssignment.granted_at <= now,
            (
                GovernanceRoleAssignment.revoked_at.is_(None)
                | (GovernanceRoleAssignment.revoked_at > now)
            ),
        )
    )
    if role is None:
        raise GovernanceDecisionError("steward_role_not_active")
    recusal = await session.scalar(
        select(GovernanceRecusal.id).where(
            GovernanceRecusal.source_draft_id == decision.source_draft_id,
            GovernanceRecusal.actor_id == actor_id,
            GovernanceRecusal.recused_at <= now,
        )
    )
    if recusal is not None:
        raise GovernanceDecisionError("steward_recused")
    draft = await session.scalar(
        select(ContributionDraft)
        .where(ContributionDraft.id == decision.source_draft_id)
        .with_for_update()
    )
    if draft is None:
        raise GovernanceDecisionError("contribution_not_found")
    intervention = GovernancePublicationIntervention(
        id=uuid4(),
        publication_intent_id=intent.id,
        source_draft_id=draft.id,
        pack_id=decision.pack_id,
        actor_id=actor_id,
        action=action.value,
        reason=reason,
        intervened_at=now,
    )
    session.add(intervention)
    intent.state = "publish_blocked"
    intent.workflow_revision += 1
    intent.last_failure_code = f"governance_{action.value}"
    intent.last_failure_context_json = {
        "action": action.value,
        "actor_id": str(actor_id),
    }
    intent.updated_at = now
    draft.review_state = "changes_requested"
    draft.updated_at = now
    try:
        await session.flush()
    except IntegrityError as error:
        if "merge_authorization_committed" in str(error.orig):
            raise GovernanceDecisionError("merge_authorization_committed") from error
        raise
    return intervention


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Governance time must include a timezone")


def _require_reason(value: str) -> None:
    if not value.strip() or len(value) > 1000:
        raise ValueError("Governance intervention requires a bounded reason")
