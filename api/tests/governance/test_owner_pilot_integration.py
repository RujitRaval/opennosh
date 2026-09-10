"""Exercise owner approval and citation preservation against the real SQL guards."""

from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]
import pytest
from alembic import command
from opennosh_api.contributions.models import ContributionDraft
from opennosh_api.evidence.contracts import (
    DocumentRightsState,
    EvidencePublicState,
    PublicDocumentManifest,
    canonical_manifest_bytes,
)
from opennosh_api.evidence.models import EvidenceCitationCopy
from opennosh_api.evidence.policy import verify_durability
from opennosh_api.evidence.repository import load_bundle
from opennosh_api.evidence.service import create_manifest_and_enqueue
from opennosh_api.governance.contracts import ApprovedChangeSet, ApprovedFileChange
from opennosh_api.governance.gate import PostgresGovernanceGate
from opennosh_api.governance.models import GovernanceOwnerAuthorization, GovernanceRoleAssignment
from opennosh_api.governance.policy import GovernanceAuthorizationError
from opennosh_api.governance.review_service import (
    ReviewCaseError,
    approve_review_case,
    claim_review_case,
    open_review_case,
)
from opennosh_api.governance.service import ResubmitPublication, resubmit_publication
from opennosh_api.jobs.pgqueuer import PgQueuerJobQueue
from opennosh_api.jobs.worker import asyncpg_dsn
from opennosh_api.models.auth import User
from opennosh_api.publication.models import PublicationIntent
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.tests.test_migrations import migration_config
from deploy.render_runtime import grant_web_runtime_privileges

DATABASE = os.getenv("INTEGRATION_DATABASE_URL")
NOW = datetime.now(UTC) - timedelta(minutes=1)


async def _exercise(database: str) -> None:
    engine = create_async_engine(database)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    actor, draft_id, authorization_id = uuid4(), uuid4(), uuid4()
    pack_id = f"owner-test-{uuid4()}"
    queue = PgQueuerJobQueue(clock=lambda: NOW)
    manifest = PublicDocumentManifest(
        evidence_id=uuid4(),
        canonical_uri="https://example.test/label",
        publisher="Test source",
        license="reference-only",
        title="Test label",
        observed_at=NOW,
        observed_digest=None,
        rights_state=DocumentRightsState.REFERENCE_ONLY,
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("""DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'opennosh_web') THEN
                    CREATE ROLE opennosh_web NOLOGIN;
                END IF;
            END $$""")
            )
        await grant_web_runtime_privileges(database)
        async with sessions() as session, session.begin():
            session.add(User(id=actor, email=f"{actor}@example.test", password_hash="test"))
            await session.flush()
            session.add(
                ContributionDraft(
                    id=draft_id,
                    user_id=actor,
                    client_draft_id=str(uuid4()),
                    review_state="in_review",
                    fields_json={
                        "pack_id": pack_id,
                        "source_uri": manifest.canonical_uri,
                        "source_license": "reference-only",
                        "evidence_type": "public_document",
                        "rights_acknowledged": True,
                    },
                )
            )
            session.add(
                GovernanceRoleAssignment(
                    pack_id=pack_id,
                    actor_id=actor,
                    role="steward",
                    granted_by_actor_id=actor,
                    grant_reason="test steward",
                    granted_at=NOW,
                )
            )
            await session.flush()
            case = await open_review_case(
                session,
                source_draft_id=draft_id,
                source_draft_version=1,
                pack_id=pack_id,
                contributor_actor_id=actor,
                now=NOW,
            )
            case_id = case.id
        async with sessions() as session, session.begin():
            with pytest.raises(ReviewCaseError, match="self_review_prohibited"):
                await claim_review_case(
                    session,
                    review_case_id=case_id,
                    actor_id=actor,
                    expected_revision=1,
                    idempotency_key=uuid4(),
                    now=NOW,
                )
        other_actor = uuid4()
        async with sessions() as session, session.begin():
            session.add(
                User(id=other_actor, email=f"{other_actor}@example.test", password_hash="test")
            )
            await session.flush()
            session.add_all(
                [
                    GovernanceOwnerAuthorization(
                        pack_id=pack_id + "-other",
                        actor_id=actor,
                        role="owner",
                        granted_by_actor_id=actor,
                        grant_reason="wrong pack",
                        granted_at=NOW,
                    ),
                    GovernanceOwnerAuthorization(
                        pack_id=pack_id,
                        actor_id=other_actor,
                        role="owner",
                        granted_by_actor_id=actor,
                        grant_reason="wrong actor",
                        granted_at=NOW,
                    ),
                ]
            )
        async with sessions() as session, session.begin():
            with pytest.raises(ReviewCaseError, match="self_review_prohibited"):
                await claim_review_case(
                    session,
                    review_case_id=case_id,
                    actor_id=actor,
                    expected_revision=1,
                    idempotency_key=uuid4(),
                    now=NOW,
                )
        async with sessions() as session, session.begin():
            session.add(
                GovernanceOwnerAuthorization(
                    id=authorization_id,
                    pack_id=pack_id,
                    actor_id=actor,
                    role="owner",
                    granted_by_actor_id=actor,
                    grant_reason="explicit test authorization",
                    granted_at=NOW,
                )
            )

        # The production web role cannot create or revoke its own authorization.
        async with sessions() as session:
            with pytest.raises(DBAPIError, match="permission denied"):
                async with session.begin():
                    await session.execute(text("SET LOCAL ROLE opennosh_web"))
                    await session.execute(
                        text(
                            "UPDATE governance_owner_authorizations SET revoked_at = now() "
                            "WHERE id = :id"
                        ),
                        {"id": authorization_id},
                    )

        # Two identical concurrent attachments converge on one stored copy/ack, no evidence job.
        async def attach() -> None:
            async with sessions() as session, session.begin():
                await session.execute(text("SET LOCAL ROLE opennosh_web"))
                await create_manifest_and_enqueue(
                    session,
                    queue,
                    source_draft_id=draft_id,
                    source_draft_version=1,
                    manifest=manifest,
                    now=NOW,
                )

        await asyncio.gather(attach(), attach())
        async with sessions() as session, session.begin():
            bundle = await load_bundle(session, manifest.evidence_id)
            assert bundle.public_state is EvidencePublicState.REFERENCE_ONLY
            assert (
                verify_durability(bundle.manifest, bundle.acknowledgements)
                is EvidencePublicState.REFERENCE_ONLY
            )
            assert len(bundle.acknowledgements) == 1
            assert bundle.acknowledgements[0].adapter_identity == "opennosh.postgres-citation"
            copy = await session.get(EvidenceCitationCopy, manifest.evidence_id)
            assert copy is not None and copy.canonical_bytes == canonical_manifest_bytes(manifest)
            queued = await session.scalar(
                text(
                    "SELECT count(*) FROM opennosh_pgqueuer "
                    "WHERE convert_from(payload, 'UTF8')::jsonb->>'subject_id' = :id"
                ),
                {"id": str(manifest.evidence_id)},
            )
            assert queued == 0
            claimed = await claim_review_case(
                session,
                review_case_id=case_id,
                actor_id=actor,
                expected_revision=1,
                idempotency_key=uuid4(),
                now=NOW,
            )
            assert claimed.assigned_steward_actor_id == actor
        async with sessions() as session, session.begin():
            _, decision, intent = await approve_review_case(
                session,
                queue,
                review_case_id=case_id,
                actor_id=actor,
                approved_changes=ApprovedChangeSet.build(
                    pack_id=pack_id,
                    files=(
                        ApprovedFileChange(
                            path=f"packs/{pack_id}/foods/test.json", content='{"name":"Test"}\n'
                        ),
                    ),
                ),
                record_id="test",
                expected_base_commit="a" * 40,
                expected_revision=2,
                idempotency_key=uuid4(),
                reason="Owner reviewed their own test contribution.",
                now=NOW,
            )
            assert decision.contributor_actor_id == decision.deciding_actor_id == actor
            assert decision.approval_mode == "owner"
            assert decision.owner_authorization_id == authorization_id
            intent_id = intent.id
        connection = await asyncpg.connect(asyncpg_dsn(database))
        try:
            binding = await PostgresGovernanceGate._binding_for(connection, intent_id)
            binding.authorize_at(NOW)
            with pytest.raises(GovernanceAuthorizationError, match="owner_authorization_revoked"):
                replace(binding, owner_revoked_at=NOW).authorize_at(NOW)
            with pytest.raises(GovernanceAuthorizationError, match="publication_paused"):
                replace(binding, pause_intervals=((NOW, None),)).authorize_at(NOW)
            with pytest.raises(GovernanceAuthorizationError, match="steward_recused"):
                replace(binding, recused_at=NOW).authorize_at(NOW)
            with pytest.raises(GovernanceAuthorizationError, match="self_review_prohibited"):
                replace(binding, approval_mode="independent").authorize_at(NOW)
        finally:
            await connection.close()
        async with sessions() as session, session.begin():
            prior_intent = await session.get(PublicationIntent, intent_id)
            assert prior_intent is not None
            prior_intent.state = "publish_blocked"
            prior_intent.workflow_revision += 1
        async with sessions() as session, session.begin():
            successor_decision, successor = await resubmit_publication(
                session,
                queue,
                ResubmitPublication(
                    prior_publication_intent_id=intent_id,
                    deciding_actor_id=actor,
                    expected_base_commit="b" * 40,
                    reason="Owner retries the same approved content.",
                ),
                now=NOW + timedelta(seconds=1),
            )
            assert successor_decision.approval_mode == "owner"
            assert successor_decision.owner_authorization_id == authorization_id
            assert successor.prior_publication_intent_id == intent_id
        # New or conflicting byte acknowledgements cannot rewrite a committed citation.
        async with sessions() as session:
            with pytest.raises(DBAPIError):
                async with session.begin():
                    await session.execute(
                        text("SELECT preserve_reference_citation(:id, :payload)"),
                        {"id": manifest.evidence_id, "payload": b"{}"},
                    )
        async with sessions() as session:
            with pytest.raises(DBAPIError):
                async with session.begin():
                    await session.execute(
                        text(
                            "UPDATE evidence_citation_copies SET canonical_bytes = :payload "
                            "WHERE evidence_id = :id"
                        ),
                        {"id": manifest.evidence_id, "payload": b"{}"},
                    )
    finally:
        # The migration downgrade deliberately refuses to erase genuine history;
        # fixtures are removable only by test-database TRUNCATE, never production cleanup.
        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE users CASCADE"))
        await engine.dispose()


@pytest.mark.skipif(DATABASE is None, reason="local PostgreSQL integration database required")
def test_owner_claim_citation_approval_and_publication_authority() -> None:
    assert DATABASE is not None
    command.upgrade(migration_config(DATABASE), "head")
    asyncio.run(_exercise(DATABASE))
