from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID

import asyncpg  # type: ignore[import-untyped]
import pytest
from alembic import command as alembic_command
from opennosh_api.contributions.models import ContributionDraft
from opennosh_api.contributions.schemas import ContributionFieldName, ContributionFieldPatch
from opennosh_api.governance.contracts import (
    ApprovedChangeSet,
    ApprovedFileChange,
    GovernanceDecisionOutcome,
)
from opennosh_api.governance.models import (
    GovernanceAppeal,
    GovernanceDecision,
    GovernanceDispute,
    GovernanceRecusal,
    GovernanceReviewCase,
    GovernanceReviewEvent,
)
from opennosh_api.governance.review_service import (
    ReviewCaseError,
    approve_review_case,
    claim_review_case,
    open_appeal,
    open_dispute,
    open_review_case,
    pause_review_case,
    record_nonapproval_decision,
    recuse_review_case,
    resolve_appeal,
    resolve_dispute,
    respond_to_changes_request,
    resume_review_case,
)
from opennosh_api.governance.reviews import DisputeCategory
from opennosh_api.jobs.pgqueuer import PGQUEUER_SETTINGS, PgQueuerJobQueue
from opennosh_api.jobs.worker import asyncpg_dsn
from opennosh_api.publication.models import PublicationIntent
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.tests.evidence.factories import seed_verified_reference_evidence
from api.tests.test_migrations import migration_config

INTEGRATION_DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL")
NOW = datetime(2026, 9, 1, 21, tzinfo=UTC)
CONTRIBUTOR = UUID("11111111-1111-4111-8111-111111111111")
STEWARD = UUID("22222222-2222-4222-8222-222222222222")
OTHER_STEWARD = UUID("33333333-3333-4333-8333-333333333333")
THIRD_STEWARD = UUID("34343434-3434-4434-8434-343434343434")
DRAFT = UUID("44444444-4444-4444-8444-444444444444")
CASE = UUID("55555555-5555-4555-8555-555555555555")
CLAIM_KEY = UUID("66666666-6666-4666-8666-666666666666")
DECISION_KEY = UUID("77777777-7777-4777-8777-777777777777")
DISPUTE = UUID("88888888-8888-4888-8888-888888888888")
APPEAL = UUID("99999999-9999-4999-8999-999999999999")
APPROVAL_KEY = UUID("12121212-1212-4212-8212-121212121212")


async def _seed(database_url: str) -> None:
    connection = await asyncpg.connect(asyncpg_dsn(database_url))
    try:
        await connection.execute(
            "TRUNCATE governance_appeals, governance_disputes, "
            "governance_review_private_notes, governance_review_events, "
            "governance_review_cases, governance_decisions, governance_recusals, "
            "governance_role_assignments, publication_steps, publication_intents, "
            f"{PGQUEUER_SETTINGS.queue_table}, contribution_drafts, users CASCADE"
        )
        await connection.execute(
            "INSERT INTO users (id, email, password_hash) VALUES "
            "($1, 'contributor-review@example.test', 'hash'), "
            "($2, 'steward-review@example.test', 'hash'), "
            "($3, 'other-steward-review@example.test', 'hash'), "
            "($4, 'third-steward-review@example.test', 'hash')",
            CONTRIBUTOR,
            STEWARD,
            OTHER_STEWARD,
            THIRD_STEWARD,
        )
        await connection.execute(
            "INSERT INTO contribution_drafts "
            "(id, user_id, client_draft_id, review_state, fields_json) "
            "VALUES ($1, $2, 'accountable-review', 'in_review', "
            '\'{"pack_id":"global-core"}\')',
            DRAFT,
            CONTRIBUTOR,
        )
        await connection.execute(
            "INSERT INTO governance_role_assignments "
            "(pack_id, actor_id, role, granted_by_actor_id, grant_reason, granted_at) "
            "VALUES ('global-core', $1, 'steward', $1, 'test grant', $4), "
            "('global-core', $2, 'steward', $1, 'test grant', $4), "
            "('global-core', $3, 'steward', $1, 'test grant', $4)",
            STEWARD,
            OTHER_STEWARD,
            THIRD_STEWARD,
            NOW,
        )
    finally:
        await connection.close()


async def _exercise_review_lifecycle(database_url: str) -> None:
    await _seed(database_url)
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session, session.begin():
            await seed_verified_reference_evidence(
                session,
                draft_id=DRAFT,
                draft_version=1,
                now=NOW,
            )
            review_case = await open_review_case(
                session,
                source_draft_id=DRAFT,
                source_draft_version=1,
                pack_id="global-core",
                contributor_actor_id=CONTRIBUTOR,
                now=NOW,
                review_case_id_generator=lambda: CASE,
            )
            assert review_case.state == "pending"
            assert review_case.revision == 1

        async with sessions() as session, session.begin():
            claimed = await claim_review_case(
                session,
                review_case_id=CASE,
                actor_id=STEWARD,
                expected_revision=1,
                idempotency_key=CLAIM_KEY,
                now=NOW,
            )
            assert claimed.state == "in_review"
            assert claimed.revision == 2
            assert claimed.assigned_steward_actor_id == STEWARD

        async with sessions() as session, session.begin():
            replayed = await claim_review_case(
                session,
                review_case_id=CASE,
                actor_id=STEWARD,
                expected_revision=1,
                idempotency_key=CLAIM_KEY,
                now=NOW,
            )
            assert replayed.revision == 2

        async with sessions() as session, session.begin():
            review_case, decision = await record_nonapproval_decision(
                session,
                review_case_id=CASE,
                actor_id=STEWARD,
                outcome=GovernanceDecisionOutcome.CHANGES_REQUESTED,
                expected_revision=2,
                idempotency_key=DECISION_KEY,
                reason="  Clarify the serving size shown on the package.  ",
                now=NOW,
            )
            assert review_case.state == "changes_requested"
            assert review_case.revision == 3
            assert decision.outcome == "changes_requested"
            assert decision.approved_payload_digest is None
            assert decision.reason == "Clarify the serving size shown on the package."

        async with sessions() as session, session.begin():
            replayed_case, replayed_decision = await record_nonapproval_decision(
                session,
                review_case_id=CASE,
                actor_id=STEWARD,
                outcome=GovernanceDecisionOutcome.CHANGES_REQUESTED,
                expected_revision=2,
                idempotency_key=DECISION_KEY,
                reason="Clarify the serving size shown on the package.",
                now=NOW,
            )
            assert replayed_case.revision == 3
            assert replayed_decision.id == decision.id

        async with sessions() as session, session.begin():
            disputed_case, dispute = await open_dispute(
                session,
                review_case_id=CASE,
                actor_id=CONTRIBUTOR,
                category=DisputeCategory.ACCURACY,
                public_reason="The requested serving size is already visible.",
                requested_remedy="Compare the preserved front panel again.",
                expected_revision=3,
                idempotency_key=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
                now=NOW,
                dispute_id_generator=lambda: DISPUTE,
            )
            assert disputed_case.state == "disputed"
            assert dispute.state == "open"
            assert dispute.decision_id == decision.id

        async with sessions() as session, session.begin():
            resolved_case, resolved_dispute = await resolve_dispute(
                session,
                dispute_id=DISPUTE,
                actor_id=OTHER_STEWARD,
                expected_case_revision=4,
                expected_dispute_revision=1,
                idempotency_key=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
                resolution="A second comparison confirms the change request.",
                now=NOW,
            )
            assert resolved_case.state == "reopened"
            assert resolved_dispute.state == "resolved"
            assert resolved_dispute.revision == 2

        async with sessions() as session, session.begin():
            appealed_case, appeal = await open_appeal(
                session,
                dispute_id=DISPUTE,
                actor_id=CONTRIBUTOR,
                expected_case_revision=5,
                expected_dispute_revision=2,
                idempotency_key=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
                public_reason="The comparison used the wrong portion panel.",
                requested_remedy="Review the independently preserved back panel.",
                now=NOW,
                appeal_id_generator=lambda: APPEAL,
            )
            assert appealed_case.state == "appealed"
            assert appeal.original_deciding_actor_id == STEWARD

        async with sessions() as session, session.begin():
            with pytest.raises(ReviewCaseError, match="appeal_requires_independent_steward"):
                await resolve_appeal(
                    session,
                    appeal_id=APPEAL,
                    actor_id=STEWARD,
                    expected_case_revision=6,
                    expected_appeal_revision=1,
                    idempotency_key=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
                    resolution="The original decision stands.",
                    now=NOW,
                )

        async with sessions() as session, session.begin():
            with pytest.raises(ReviewCaseError, match="appeal_requires_independent_steward"):
                await resolve_appeal(
                    session,
                    appeal_id=APPEAL,
                    actor_id=OTHER_STEWARD,
                    expected_case_revision=6,
                    expected_appeal_revision=1,
                    idempotency_key=UUID("dededede-dede-4ded-8ded-dededededede"),
                    resolution="The dispute resolution stands.",
                    now=NOW,
                )

        async with sessions() as session, session.begin():
            final_case, resolved_appeal = await resolve_appeal(
                session,
                appeal_id=APPEAL,
                actor_id=THIRD_STEWARD,
                expected_case_revision=6,
                expected_appeal_revision=1,
                idempotency_key=UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"),
                resolution="The appeal is upheld; the case returns for review.",
                now=NOW,
            )
            assert final_case.state == "reopened"
            assert resolved_appeal.state == "resolved"
            assert resolved_appeal.decided_by_actor_id == THIRD_STEWARD

        async with sessions() as session, session.begin():
            reclaimed_case = await claim_review_case(
                session,
                review_case_id=CASE,
                actor_id=THIRD_STEWARD,
                expected_revision=7,
                idempotency_key=UUID("ffffffff-ffff-4fff-8fff-ffffffffffff"),
                now=NOW,
            )
            assert reclaimed_case.state == "in_review"
            assert reclaimed_case.revision == 8

        async with sessions() as session, session.begin():
            decided_case, successor_decision, publication_intent = await approve_review_case(
                session,
                PgQueuerJobQueue(clock=lambda: NOW),
                review_case_id=CASE,
                actor_id=THIRD_STEWARD,
                approved_changes=ApprovedChangeSet.build(
                    pack_id="global-core",
                    files=(
                        ApprovedFileChange(
                            path="packs/global-core/foods/lentils.json",
                            content='{"name":"Lentils"}\n',
                        ),
                    ),
                ),
                record_id="lentils",
                expected_base_commit="a" * 40,
                expected_revision=8,
                idempotency_key=UUID("12121212-1212-4212-8212-121212121212"),
                reason="The independent appeal review confirms the preserved source.",
                now=NOW,
            )
            assert decided_case.state == "approved"
            assert successor_decision.prior_decision_id == decision.id
            assert publication_intent.reviewed_decision_id == successor_decision.id

        async with sessions() as session:
            case_row = await session.get(GovernanceReviewCase, CASE)
            assert case_row is not None
            assert case_row.state == "approved"
            event_count = await session.scalar(
                select(func.count()).select_from(GovernanceReviewEvent)
            )
            decision_count = await session.scalar(
                select(func.count()).select_from(GovernanceDecision)
            )
            dispute_count = await session.scalar(
                select(func.count()).select_from(GovernanceDispute)
            )
            appeal_count = await session.scalar(select(func.count()).select_from(GovernanceAppeal))
            publication_count = await session.scalar(
                select(func.count()).select_from(PublicationIntent)
            )
            queue_count = await session.scalar(
                select(func.count()).select_from(text(PGQUEUER_SETTINGS.queue_table))
            )
            draft_state = await session.scalar(
                select(ContributionDraft.review_state).where(ContributionDraft.id == DRAFT)
            )
            assert event_count == 9
            assert decision_count == 2
            assert dispute_count == 1
            assert appeal_count == 1
            assert publication_count == 1
            assert queue_count == 1
            assert draft_state == "publication_pending"

        connection = await asyncpg.connect(asyncpg_dsn(database_url))
        try:
            with pytest.raises(
                asyncpg.CheckViolationError,
                match="governance_review_case_transition_invalid",
            ):
                await connection.execute(
                    "UPDATE governance_review_cases "
                    "SET state = 'pending', revision = revision + 1 WHERE id = $1",
                    CASE,
                )
            with pytest.raises(
                asyncpg.CheckViolationError,
                match="governance_review_history_is_immutable",
            ):
                await connection.execute(
                    "UPDATE governance_review_events SET public_reason = 'rewritten' "
                    "WHERE review_case_id = $1",
                    CASE,
                )
            with pytest.raises(
                asyncpg.CheckViolationError,
                match="governance_review_history_is_immutable",
            ):
                await connection.execute(
                    "DELETE FROM governance_review_cases WHERE id = $1",
                    CASE,
                )
        finally:
            await connection.close()
    finally:
        await engine.dispose()


async def _exercise_concurrent_claim(database_url: str) -> None:
    await _seed(database_url)
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session, session.begin():
            await open_review_case(
                session,
                source_draft_id=DRAFT,
                source_draft_version=1,
                pack_id="global-core",
                contributor_actor_id=CONTRIBUTOR,
                now=NOW,
                review_case_id_generator=lambda: CASE,
            )

        async def claim(actor_id: UUID, key: UUID) -> object:
            try:
                async with sessions() as session, session.begin():
                    return await claim_review_case(
                        session,
                        review_case_id=CASE,
                        actor_id=actor_id,
                        expected_revision=1,
                        idempotency_key=key,
                        now=NOW,
                    )
            except ReviewCaseError as error:
                return error

        results = await asyncio.gather(
            claim(STEWARD, CLAIM_KEY),
            claim(OTHER_STEWARD, UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")),
        )
        successes = [item for item in results if isinstance(item, GovernanceReviewCase)]
        errors = [item for item in results if isinstance(item, ReviewCaseError)]
        assert len(successes) == 1
        assert [error.code for error in errors] == ["review_case_revision_conflict"]
    finally:
        await engine.dispose()


async def _exercise_approval_path(database_url: str) -> None:
    await _seed(database_url)
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    approved_changes = ApprovedChangeSet.build(
        pack_id="global-core",
        files=(
            ApprovedFileChange(
                path="packs/global-core/foods/lentils.json",
                content='{"name":"Lentils"}\n',
            ),
        ),
    )
    try:
        async with sessions() as session, session.begin():
            await seed_verified_reference_evidence(
                session,
                draft_id=DRAFT,
                draft_version=1,
                now=NOW,
            )
            await open_review_case(
                session,
                source_draft_id=DRAFT,
                source_draft_version=1,
                pack_id="global-core",
                contributor_actor_id=CONTRIBUTOR,
                now=NOW,
                review_case_id_generator=lambda: CASE,
            )

        async with sessions() as session, session.begin():
            await claim_review_case(
                session,
                review_case_id=CASE,
                actor_id=STEWARD,
                expected_revision=1,
                idempotency_key=CLAIM_KEY,
                now=NOW,
            )

        queue = PgQueuerJobQueue(clock=lambda: NOW)
        async with sessions() as session, session.begin():
            review_case, decision, publication_intent = await approve_review_case(
                session,
                queue,
                review_case_id=CASE,
                actor_id=STEWARD,
                approved_changes=approved_changes,
                record_id="lentils",
                expected_base_commit="a" * 40,
                expected_revision=2,
                idempotency_key=APPROVAL_KEY,
                reason="Source and normalized record reviewed.",
                now=NOW,
            )
            assert review_case.state == "approved"
            assert review_case.revision == 3
            assert decision.outcome == "approved"
            assert publication_intent.reviewed_decision_id == decision.id

        async with sessions() as session, session.begin():
            replayed_case, replayed_decision, replayed_intent = await approve_review_case(
                session,
                queue,
                review_case_id=CASE,
                actor_id=STEWARD,
                approved_changes=approved_changes,
                record_id="lentils",
                expected_base_commit="a" * 40,
                expected_revision=2,
                idempotency_key=APPROVAL_KEY,
                reason="Source and normalized record reviewed.",
                now=NOW,
            )
            assert replayed_case.revision == 3
            assert replayed_decision.id == decision.id
            assert replayed_intent.id == publication_intent.id

        async with sessions() as session:
            draft_state = await session.scalar(
                select(ContributionDraft.review_state).where(ContributionDraft.id == DRAFT)
            )
            publication_count = await session.scalar(
                select(func.count()).select_from(PublicationIntent)
            )
            queue_count = await session.scalar(
                select(func.count()).select_from(text(PGQUEUER_SETTINGS.queue_table))
            )
            assert draft_state == "publication_pending"
            assert publication_count == 1
            assert queue_count == 1
    finally:
        await engine.dispose()


async def _exercise_contributor_response(database_url: str) -> None:
    await _seed(database_url)
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session, session.begin():
            first_case = await open_review_case(
                session,
                source_draft_id=DRAFT,
                source_draft_version=1,
                pack_id="global-core",
                contributor_actor_id=CONTRIBUTOR,
                now=NOW,
                review_case_id_generator=lambda: CASE,
            )
            assert first_case.submitted_fields_json["pack_id"] == "global-core"

        async with sessions() as session, session.begin():
            await claim_review_case(
                session,
                review_case_id=CASE,
                actor_id=STEWARD,
                expected_revision=1,
                idempotency_key=CLAIM_KEY,
                now=NOW,
            )
            await record_nonapproval_decision(
                session,
                review_case_id=CASE,
                actor_id=STEWARD,
                outcome=GovernanceDecisionOutcome.CHANGES_REQUESTED,
                expected_revision=2,
                idempotency_key=DECISION_KEY,
                reason="Use the corrected product name.",
                now=NOW,
            )

        response_key = UUID("13131313-1313-4313-8313-131313131313")
        patches = (
            ContributionFieldPatch(
                field=ContributionFieldName.NAME,
                value="Lentils, revised",
            ),
        )
        async with sessions() as session, session.begin():
            prior_case, next_case, draft = await respond_to_changes_request(
                session,
                review_case_id=CASE,
                actor_id=CONTRIBUTOR,
                patches=patches,
                expected_revision=3,
                expected_draft_version=1,
                idempotency_key=response_key,
                public_reason="Updated the product name as requested.",
                now=NOW,
            )
            next_case_id = next_case.id
            assert prior_case.state == "closed"
            assert prior_case.revision == 5
            assert next_case.source_draft_version == 2
            assert next_case.submitted_fields_json["name"] == "Lentils, revised"
            assert draft.draft_version == 2
            assert draft.review_state == "in_review"

        async with sessions() as session, session.begin():
            replayed_prior, replayed_next, replayed_draft = await respond_to_changes_request(
                session,
                review_case_id=CASE,
                actor_id=CONTRIBUTOR,
                patches=patches,
                expected_revision=3,
                expected_draft_version=1,
                idempotency_key=response_key,
                public_reason="Updated the product name as requested.",
                now=NOW,
            )
            assert replayed_prior.revision == 5
            assert replayed_next.id == next_case_id
            assert replayed_draft.draft_version == 2

        async with sessions() as session, session.begin():
            await claim_review_case(
                session,
                review_case_id=next_case_id,
                actor_id=STEWARD,
                expected_revision=1,
                idempotency_key=UUID("14141414-1414-4414-8414-141414141414"),
                now=NOW,
            )
            with pytest.raises(ReviewCaseError, match="evidence_manifest_missing"):
                await approve_review_case(
                    session,
                    PgQueuerJobQueue(clock=lambda: NOW),
                    review_case_id=next_case_id,
                    actor_id=STEWARD,
                    approved_changes=ApprovedChangeSet.build(
                        pack_id="global-core",
                        files=(
                            ApprovedFileChange(
                                path="packs/global-core/foods/lentils.json",
                                content='{"name":"Lentils, revised"}\n',
                            ),
                        ),
                    ),
                    record_id="lentils",
                    expected_base_commit="a" * 40,
                    expected_revision=2,
                    idempotency_key=UUID("15151515-1515-4515-8515-151515151515"),
                    reason="Reviewed the revised name.",
                    now=NOW,
                )

        async with sessions() as session:
            publication_count = await session.scalar(
                select(func.count()).select_from(PublicationIntent)
            )
            queue_count = await session.scalar(
                select(func.count()).select_from(text(PGQUEUER_SETTINGS.queue_table))
            )
            assert publication_count == 0
            assert queue_count == 0
    finally:
        await engine.dispose()


async def _exercise_steward_controls(database_url: str) -> None:
    await _seed(database_url)
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session, session.begin():
            await open_review_case(
                session,
                source_draft_id=DRAFT,
                source_draft_version=1,
                pack_id="global-core",
                contributor_actor_id=CONTRIBUTOR,
                now=NOW,
                review_case_id_generator=lambda: CASE,
            )
            await claim_review_case(
                session,
                review_case_id=CASE,
                actor_id=STEWARD,
                expected_revision=1,
                idempotency_key=CLAIM_KEY,
                now=NOW,
            )
            paused = await pause_review_case(
                session,
                review_case_id=CASE,
                actor_id=STEWARD,
                expected_revision=2,
                idempotency_key=UUID("16161616-1616-4616-8616-161616161616"),
                reason="Waiting for a second source comparison.",
                next_review_at=NOW + timedelta(days=1),
                now=NOW,
            )
            assert paused.state == "in_review"
            assert paused.next_review_at == NOW + timedelta(days=1)

            resumed = await resume_review_case(
                session,
                review_case_id=CASE,
                actor_id=STEWARD,
                expected_revision=3,
                idempotency_key=UUID("17171717-1717-4717-8717-171717171717"),
                reason="The second source is now available.",
                now=NOW,
            )
            assert resumed.pause_reason is None
            assert resumed.next_review_at is None

            recused = await recuse_review_case(
                session,
                review_case_id=CASE,
                actor_id=STEWARD,
                expected_revision=4,
                idempotency_key=UUID("18181818-1818-4818-8818-181818181818"),
                reason="I contributed to the source data set.",
                now=NOW,
            )
            assert recused.state == "pending"
            assert recused.assigned_steward_actor_id is None

        async with sessions() as session, session.begin():
            with pytest.raises(ReviewCaseError, match="steward_recused"):
                await claim_review_case(
                    session,
                    review_case_id=CASE,
                    actor_id=STEWARD,
                    expected_revision=5,
                    idempotency_key=UUID("19191919-1919-4919-8919-191919191919"),
                    now=NOW,
                )
            claimed = await claim_review_case(
                session,
                review_case_id=CASE,
                actor_id=OTHER_STEWARD,
                expected_revision=5,
                idempotency_key=UUID("20202020-2020-4020-8020-202020202020"),
                now=NOW,
            )
            assert claimed.assigned_steward_actor_id == OTHER_STEWARD

        async with sessions() as session:
            event_count = await session.scalar(
                select(func.count()).select_from(GovernanceReviewEvent)
            )
            recusal_count = await session.scalar(
                select(func.count()).select_from(GovernanceRecusal)
            )
            assert event_count == 6
            assert recusal_count == 1
    finally:
        await engine.dispose()


@pytest.mark.skipif(
    INTEGRATION_DATABASE_URL is None,
    reason="INTEGRATION_DATABASE_URL is required for PostgreSQL integration tests",
)
def test_accountable_review_lifecycle_is_idempotent_and_audited() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    alembic_command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_exercise_review_lifecycle(INTEGRATION_DATABASE_URL))


@pytest.mark.skipif(
    INTEGRATION_DATABASE_URL is None,
    reason="INTEGRATION_DATABASE_URL is required for PostgreSQL integration tests",
)
def test_concurrent_review_claim_accepts_exactly_one_steward() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    alembic_command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_exercise_concurrent_claim(INTEGRATION_DATABASE_URL))


@pytest.mark.skipif(
    INTEGRATION_DATABASE_URL is None,
    reason="INTEGRATION_DATABASE_URL is required for PostgreSQL integration tests",
)
def test_review_approval_delegates_to_the_existing_publication_path() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    alembic_command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_exercise_approval_path(INTEGRATION_DATABASE_URL))


@pytest.mark.skipif(
    INTEGRATION_DATABASE_URL is None,
    reason="INTEGRATION_DATABASE_URL is required for PostgreSQL integration tests",
)
def test_contributor_response_preserves_versions_and_requires_fresh_evidence() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    alembic_command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_exercise_contributor_response(INTEGRATION_DATABASE_URL))


@pytest.mark.skipif(
    INTEGRATION_DATABASE_URL is None,
    reason="INTEGRATION_DATABASE_URL is required for PostgreSQL integration tests",
)
def test_steward_pause_resume_and_recusal_are_audited() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    alembic_command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_exercise_steward_controls(INTEGRATION_DATABASE_URL))
