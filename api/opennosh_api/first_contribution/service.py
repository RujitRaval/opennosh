from __future__ import annotations

import asyncio
import hashlib
import secrets
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from opennosh_api.auth.passwords import hash_password
from opennosh_api.contributions.models import ContributionDraft
from opennosh_api.evidence.contracts import (
    EvidenceAcknowledgement,
    EvidenceAcknowledgementKind,
    EvidenceClass,
    EvidencePublicState,
    PublicDocumentManifest,
    manifest_digest,
    parse_manifest,
)
from opennosh_api.evidence.repository import (
    EvidenceBundle,
    EvidenceConflictError,
    EvidenceNotFoundError,
    create_manifest,
    load_bundle,
    record_acknowledgements,
)
from opennosh_api.first_contribution.contracts import (
    FIRST_PACK_ID,
    FirstContributionPackage,
    FirstContributionReceipt,
)
from opennosh_api.first_contribution.prepare import validate_first_contribution_package
from opennosh_api.governance.contracts import (
    CANONICAL_FORGE_TARGET,
    PROTECTED_STATUS_CHECKS,
    ApprovedChangeSet,
)
from opennosh_api.governance.models import GovernanceDecision, GovernanceRoleAssignment
from opennosh_api.governance.service import (
    ApproveContribution,
    GovernanceDecisionError,
    approve_contribution,
    grant_steward,
)
from opennosh_api.jobs import JobQueue
from opennosh_api.models import User
from opennosh_api.publication.models import PublicationIntent
from opennosh_api.publication.receipts import ReceiptEventType
from opennosh_api.publication.service import PublicationIntentConflictError

SOURCE_ACTOR_EMAIL = "usda-fdc-1105314@actors.opennosh.invalid"


class FirstContributionConflictError(RuntimeError):
    pass


class FirstContributionAuthorityError(RuntimeError):
    pass


class FirstContributionEvidenceStore(Protocol):
    async def preserve(
        self,
        manifest: PublicDocumentManifest,
        *,
        now: datetime,
    ) -> EvidenceAcknowledgement: ...


async def commit_usda_first_contribution(
    factory: async_sessionmaker[AsyncSession],
    queue: JobQueue,
    evidence_store: FirstContributionEvidenceStore,
    package: FirstContributionPackage,
    *,
    steward_actor_id: UUID,
    expected_base_commit: str,
    reason: str,
    bootstrap_steward: bool,
    now: datetime | None = None,
) -> FirstContributionReceipt:
    validate_first_contribution_package(package)
    committed_at = now or datetime.now(UTC)
    if committed_at.tzinfo is None or committed_at.utcoffset() is None:
        raise ValueError("First-contribution commit time must include a timezone")
    if len(expected_base_commit) not in {40, 64} or any(
        character not in "0123456789abcdef" for character in expected_base_commit
    ):
        raise ValueError("First-contribution base commit must be a lowercase Git hash")
    if not reason.strip() or len(reason) > 1000:
        raise ValueError("First-contribution reason must contain 1 to 1000 characters")
    if not bootstrap_steward:
        raise FirstContributionAuthorityError(
            "First common-fruits contribution requires explicit steward bootstrap"
        )

    existing = await _prepare_database(
        factory,
        package,
        steward_actor_id=steward_actor_id,
        expected_base_commit=expected_base_commit,
        reason=reason,
        now=committed_at,
    )
    if existing is not None:
        return existing

    parsed = parse_manifest(package.evidence_manifest)
    if not isinstance(parsed, PublicDocumentManifest):
        raise FirstContributionConflictError("First contribution evidence class changed")
    acknowledgement = await evidence_store.preserve(parsed, now=committed_at)

    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
                {"scope": f"first-contribution-steward:{FIRST_PACK_ID}"},
            )
            replay = await _load_receipt(
                session,
                package,
                steward_actor_id=steward_actor_id,
                expected_base_commit=expected_base_commit,
                reason=reason,
            )
            if replay is not None:
                return replay
            steward = await session.get(User, steward_actor_id, with_for_update=True)
            if (
                steward is None
                or steward.actor_kind != "person"
                or steward.login_disabled_at is not None
            ):
                raise FirstContributionAuthorityError(
                    "First-contribution steward must be an active person"
                )
            if steward.id == package.source_actor_id:
                raise FirstContributionAuthorityError("First-contribution self-review is forbidden")
            prior_role = await session.scalar(
                select(GovernanceRoleAssignment)
                .where(GovernanceRoleAssignment.pack_id == FIRST_PACK_ID)
                .with_for_update()
            )
            if prior_role is not None:
                raise FirstContributionAuthorityError(
                    "The common-fruits steward scope has already been bootstrapped"
                )
            role = grant_steward(
                pack_id=FIRST_PACK_ID,
                actor_id=steward_actor_id,
                granted_by_actor_id=steward_actor_id,
                reason=reason,
                now=committed_at,
            )
            role.id = package.role_assignment_id
            await session.execute(
                insert(GovernanceRoleAssignment)
                .values(
                    id=role.id,
                    pack_id=role.pack_id,
                    actor_id=role.actor_id,
                    role=role.role,
                    granted_by_actor_id=role.granted_by_actor_id,
                    grant_reason=role.grant_reason,
                    granted_at=role.granted_at,
                )
                .on_conflict_do_nothing()
            )
            stored_role = await session.get(
                GovernanceRoleAssignment,
                package.role_assignment_id,
                with_for_update=True,
            )
            if stored_role is None or (
                stored_role.pack_id != role.pack_id
                or stored_role.actor_id != role.actor_id
                or stored_role.role != role.role
                or stored_role.granted_by_actor_id != role.granted_by_actor_id
                or stored_role.grant_reason != role.grant_reason
                or stored_role.revoked_at is not None
            ):
                raise FirstContributionConflictError(
                    "First-contribution steward assignment differs from the reviewed authority"
                )
            role = stored_role
            try:
                bundle = await record_acknowledgements(
                    session,
                    package.evidence_id,
                    (acknowledgement,),
                )
            except (EvidenceConflictError, EvidenceNotFoundError) as error:
                raise FirstContributionConflictError(str(error)) from error
            if bundle.public_state is not EvidencePublicState.REFERENCE_ONLY:
                raise FirstContributionConflictError(
                    "First contribution evidence did not become reference_only"
                )
            replay = await _load_receipt(
                session,
                package,
                steward_actor_id=steward_actor_id,
                expected_base_commit=expected_base_commit,
                reason=reason,
            )
            if replay is not None:
                return replay
            changes = ApprovedChangeSet.from_json(package.approved_changes)
            try:
                decision, intent = await approve_contribution(
                    session,
                    queue,
                    ApproveContribution(
                        source_draft_id=package.draft_id,
                        deciding_actor_id=steward_actor_id,
                        approved_changes=changes,
                        record_id=package.record_id,
                        expected_base_commit=expected_base_commit,
                        required_checks=PROTECTED_STATUS_CHECKS,
                        forge_target=CANONICAL_FORGE_TARGET,
                        reason=reason,
                    ),
                    now=committed_at,
                    decision_id_generator=lambda: package.decision_id,
                    publication_intent_id_generator=lambda: package.publication_intent_id,
                )
            except GovernanceDecisionError as error:
                if error.code in {
                    "self_review_prohibited",
                    "steward_recused",
                    "steward_role_not_active",
                    "publication_paused",
                }:
                    raise FirstContributionAuthorityError(error.code) from error
                raise FirstContributionConflictError(error.code) from error
            except PublicationIntentConflictError as error:
                raise FirstContributionConflictError(str(error)) from error
            replay = await _load_receipt(
                session,
                package,
                steward_actor_id=steward_actor_id,
                expected_base_commit=expected_base_commit,
                reason=reason,
            )
            if replay is None:
                raise FirstContributionConflictError(
                    "First-contribution receipt rows were not visible after approval"
                )
            return replay


async def _prepare_database(
    factory: async_sessionmaker[AsyncSession],
    package: FirstContributionPackage,
    *,
    steward_actor_id: UUID,
    expected_base_commit: str,
    reason: str,
    now: datetime,
) -> FirstContributionReceipt | None:
    async with factory() as session:
        async with session.begin():
            replay = await _load_receipt(
                session,
                package,
                steward_actor_id=steward_actor_id,
                expected_base_commit=expected_base_commit,
                reason=reason,
            )
            if replay is not None:
                return replay
            await _create_or_compare_source_actor(session, package, now=now)
            await _create_or_compare_draft(session, package, now=now)
            manifest = parse_manifest(package.evidence_manifest)
            try:
                await create_manifest(
                    session,
                    source_draft_id=package.draft_id,
                    source_draft_version=1,
                    manifest=manifest,
                )
            except EvidenceConflictError as error:
                raise FirstContributionConflictError(str(error)) from error
    return None


async def _create_or_compare_source_actor(
    session: AsyncSession,
    package: FirstContributionPackage,
    *,
    now: datetime,
) -> None:
    actor = await session.get(User, package.source_actor_id, with_for_update=True)
    if actor is None:
        password = secrets.token_urlsafe(48)
        password_hash = await asyncio.to_thread(hash_password, password)
        await session.execute(
            insert(User)
            .values(
                id=package.source_actor_id,
                email=SOURCE_ACTOR_EMAIL,
                password_hash=password_hash,
                recovery_token_hash=None,
                actor_kind="service",
                login_disabled_at=now,
                settings_json={"source": "usda-fooddata-central"},
            )
            .on_conflict_do_nothing()
        )
        actor = await session.get(User, package.source_actor_id, with_for_update=True)
    if actor is None:
        raise FirstContributionConflictError(
            "First-contribution source actor email is already bound differently"
        )
    if (
        actor.email != SOURCE_ACTOR_EMAIL
        or actor.actor_kind != "service"
        or actor.login_disabled_at is None
        or actor.recovery_token_hash is not None
        or actor.settings_json != {"source": "usda-fooddata-central"}
    ):
        raise FirstContributionConflictError(
            "First-contribution source actor already has different authority"
        )


async def _create_or_compare_draft(
    session: AsyncSession,
    package: FirstContributionPackage,
    *,
    now: datetime,
) -> None:
    draft = await session.get(ContributionDraft, package.draft_id, with_for_update=True)
    expected_key = hashlib.sha256(
        f"first-usda:{package.package_digest}".encode("ascii")
    ).hexdigest()
    if draft is None:
        await session.execute(
            insert(ContributionDraft)
            .values(
                id=package.draft_id,
                user_id=package.source_actor_id,
                client_draft_id=f"first-usda-{package.source_record_digest[:32]}",
                workflow_version="1",
                draft_version=1,
                review_state="in_review",
                fields_json=package.draft_fields,
                duplicate_candidates_json=[],
                submission_id=package.submission_id,
                submission_key_hash=expected_key,
                submission_request_hash=package.package_digest,
                submitted_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing()
        )
        draft = await session.get(ContributionDraft, package.draft_id, with_for_update=True)
    if draft is None or (
        draft.user_id != package.source_actor_id
        or draft.client_draft_id != f"first-usda-{package.source_record_digest[:32]}"
        or draft.workflow_version != "1"
        or draft.draft_version != 1
        or draft.review_state != "in_review"
        or draft.fields_json != package.draft_fields
        or draft.duplicate_candidates_json != []
        or draft.submission_id != package.submission_id
        or draft.submission_key_hash != expected_key
        or draft.submission_request_hash != package.package_digest
    ):
        raise FirstContributionConflictError(
            "First-contribution draft already exists with different material"
        )


async def _load_receipt(
    session: AsyncSession,
    package: FirstContributionPackage,
    *,
    steward_actor_id: UUID,
    expected_base_commit: str,
    reason: str,
) -> FirstContributionReceipt | None:
    decision = await session.get(GovernanceDecision, package.decision_id)
    intent = await session.get(PublicationIntent, package.publication_intent_id)
    if decision is None and intent is None:
        return None
    if decision is None or intent is None:
        raise FirstContributionConflictError(
            "First-contribution decision and publication intent are incomplete"
        )
    actor = await session.get(User, package.source_actor_id)
    draft = await session.get(ContributionDraft, package.draft_id)
    role = await session.get(GovernanceRoleAssignment, package.role_assignment_id)
    if actor is None or draft is None or role is None:
        raise FirstContributionConflictError(
            "First-contribution replay is missing source, draft, or steward state"
        )
    try:
        bundle = await load_bundle(session, package.evidence_id)
    except EvidenceNotFoundError as error:
        raise FirstContributionConflictError(str(error)) from error
    return _receipt(
        package,
        steward_actor_id,
        decision,
        intent,
        actor,
        draft,
        role,
        bundle,
        expected_base_commit=expected_base_commit,
        reason=reason,
    )


def _receipt(
    package: FirstContributionPackage,
    steward_actor_id: UUID,
    decision: GovernanceDecision,
    intent: PublicationIntent,
    actor: User,
    draft: ContributionDraft,
    role: GovernanceRoleAssignment,
    bundle: EvidenceBundle,
    *,
    expected_base_commit: str,
    reason: str,
) -> FirstContributionReceipt:
    changes = ApprovedChangeSet.from_json(package.approved_changes)
    expected_manifest = parse_manifest(package.evidence_manifest)
    expected_manifest_digest = manifest_digest(expected_manifest)
    expected_submission_key = hashlib.sha256(
        f"first-usda:{package.package_digest}".encode("ascii")
    ).hexdigest()
    acknowledgements = bundle.acknowledgements
    if len(acknowledgements) != 1:
        raise FirstContributionConflictError(
            "First-contribution replay has a different evidence acknowledgement set"
        )
    acknowledgement = acknowledgements[0]
    expected_object = f"evidence/citations/v1/{expected_manifest_digest}.json"
    expected_acknowledgements = [
        acknowledgement.model_dump(mode="json"),
    ]
    expected_intent_key = hashlib.sha256(
        f"governance-decision:{decision.id}".encode()
    ).hexdigest()
    if (
        actor.email != SOURCE_ACTOR_EMAIL
        or actor.actor_kind != "service"
        or actor.login_disabled_at is None
        or actor.recovery_token_hash is not None
        or actor.settings_json != {"source": "usda-fooddata-central"}
        or draft.user_id != package.source_actor_id
        or draft.client_draft_id != f"first-usda-{package.source_record_digest[:32]}"
        or draft.workflow_version != "1"
        or draft.draft_version != 1
        or draft.review_state != "publication_pending"
        or draft.fields_json != package.draft_fields
        or draft.duplicate_candidates_json != []
        or draft.submission_id != package.submission_id
        or draft.submission_key_hash != expected_submission_key
        or draft.submission_request_hash != package.package_digest
        or role.pack_id != FIRST_PACK_ID
        or role.actor_id != steward_actor_id
        or role.role != "steward"
        or role.granted_by_actor_id != steward_actor_id
        or role.grant_reason != reason
        or role.revoked_at is not None
        or role.revoked_by_actor_id is not None
        or role.revocation_reason is not None
        or bundle.manifest != expected_manifest
        or bundle.public_state is not EvidencePublicState.REFERENCE_ONLY
        or bundle.tombstone is not None
        or acknowledgement.evidence_id != package.evidence_id
        or acknowledgement.evidence_class is not EvidenceClass.PUBLIC_DOCUMENT
        or acknowledgement.manifest_digest != expected_manifest_digest
        or acknowledgement.kind is not EvidenceAcknowledgementKind.CITATION_MANIFEST
        or acknowledgement.content_digest != expected_manifest_digest
        or not acknowledgement.destination.startswith("r2://")
        or acknowledgement.external_reference
        != f"{acknowledgement.destination}/{expected_object}"
        or decision.source_draft_id != package.draft_id
        or decision.source_draft_version != 1
        or decision.pack_id != FIRST_PACK_ID
        or decision.record_id != package.record_id
        or decision.contributor_actor_id != package.source_actor_id
        or decision.deciding_actor_id != steward_actor_id
        or decision.outcome != "approved"
        or decision.reason != reason
        or decision.approved_payload_digest != changes.digest
        or decision.approved_changes_json != changes.as_json()
        or decision.expected_base_commit != expected_base_commit
        or decision.required_checks_json != list(PROTECTED_STATUS_CHECKS)
        or decision.forge_target != CANONICAL_FORGE_TARGET
        or intent.source_draft_id != package.draft_id
        or intent.source_draft_version != 1
        or intent.reviewed_decision_id != decision.id
        or intent.approving_actor_id != steward_actor_id
        or intent.id != package.publication_intent_id
        or intent.pack_id != FIRST_PACK_ID
        or intent.record_id != package.record_id
        or intent.approved_payload_digest != changes.digest
        or intent.expected_base_commit != expected_base_commit
        or intent.required_checks_json != list(PROTECTED_STATUS_CHECKS)
        or intent.forge_target != CANONICAL_FORGE_TARGET
        or intent.idempotency_key_hash != expected_intent_key
        or intent.event_type != ReceiptEventType.PUBLICATION.value
        or intent.prior_receipt_digest is not None
        or intent.evidence_manifest_digests_json != [expected_manifest_digest]
        or intent.evidence_acknowledgements_json != expected_acknowledgements
    ):
        raise FirstContributionConflictError(
            "First-contribution receipt rows differ from the reviewed package"
        )
    return FirstContributionReceipt(
        package_digest=package.package_digest,
        source_actor_id=package.source_actor_id,
        steward_actor_id=steward_actor_id,
        draft_id=package.draft_id,
        evidence_id=package.evidence_id,
        evidence_manifest_digest=expected_manifest_digest,
        decision_id=decision.id,
        publication_intent_id=intent.id,
        approved_payload_digest=changes.digest,
        decided_at=decision.decided_at,
    )
