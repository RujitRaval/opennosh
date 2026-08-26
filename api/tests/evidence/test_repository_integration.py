from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]
import pytest
from alembic import command
from opennosh_api.contributions.models import ContributionDraft
from opennosh_api.evidence.contracts import (
    DocumentRightsState,
    EvidenceAcknowledgementKind,
    EvidencePublicState,
    PublicDocumentManifest,
)
from opennosh_api.evidence.repository import (
    EvidenceConflictError,
    create_manifest,
    load_bundle,
    record_acknowledgements,
    require_verified_evidence,
    tombstone_evidence,
)
from opennosh_api.evidence.storage import MemoryEvidenceStore
from opennosh_api.evidence.worker import EvidencePreservationWorker
from opennosh_api.governance.models import GovernanceRoleAssignment
from opennosh_api.models.auth import User
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.tests.test_migrations import migration_config

INTEGRATION_DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL")
NOW = datetime(2026, 8, 26, 12, tzinfo=UTC)


async def _run_repository_contract(database_url: str) -> None:
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    contributor_id = uuid4()
    remover_id = uuid4()
    draft_id = uuid4()
    manifest = PublicDocumentManifest(
        evidence_id=uuid4(),
        canonical_uri="https://example.test/public-source",
        publisher="Public source",
        license="CC-BY-4.0",
        title="Nutrition record",
        observed_at=NOW,
        observed_digest=hashlib.sha256(b"observed source").hexdigest(),
        rights_state=DocumentRightsState.REFERENCE_ONLY,
    )
    try:
        async with sessions() as session:
            async with session.begin():
                session.add_all(
                    [
                        User(
                            id=contributor_id,
                            email=f"{contributor_id}@example.test",
                            password_hash="h",
                        ),
                        User(
                            id=remover_id,
                            email=f"{remover_id}@example.test",
                            password_hash="h",
                        ),
                    ]
                )
                await session.flush()
                session.add_all(
                    [
                        ContributionDraft(
                        id=draft_id,
                        user_id=contributor_id,
                        client_draft_id=f"evidence-{draft_id}",
                        fields_json={"pack_id": "test-pack"},
                        ),
                        GovernanceRoleAssignment(
                            pack_id="test-pack",
                            actor_id=remover_id,
                            role="steward",
                            granted_by_actor_id=remover_id,
                            grant_reason="Evidence removal fixture",
                            granted_at=NOW,
                        ),
                    ]
                )
            async with session.begin():
                first = await create_manifest(
                    session,
                    source_draft_id=draft_id,
                    source_draft_version=1,
                    manifest=manifest,
                )
            async with session.begin():
                duplicate = await create_manifest(
                    session,
                    source_draft_id=draft_id,
                    source_draft_version=1,
                    manifest=manifest,
                )
            assert first.id == duplicate.id

            acknowledgements = await EvidencePreservationWorker(
                MemoryEvidenceStore(destination="urn:test:rpo-zero")
            ).preserve(manifest, payloads={}, now=NOW)
            async with session.begin():
                partial = await record_acknowledgements(session, manifest.evidence_id, ())
                assert partial.public_state is None
            async with session.begin():
                verified = await record_acknowledgements(
                    session,
                    manifest.evidence_id,
                    acknowledgements,
                )
                replay = await record_acknowledgements(
                    session,
                    manifest.evidence_id,
                    acknowledgements,
                )
                assert verified.public_state is EvidencePublicState.REFERENCE_ONLY
                assert replay == verified
                later_replay = tuple(
                    acknowledgement.model_copy(
                        update={"verified_at": NOW + timedelta(minutes=5)}
                    )
                    for acknowledgement in acknowledgements
                )
                assert (
                    await record_acknowledgements(
                        session, manifest.evidence_id, later_replay
                    )
                ) == verified
            async with session.begin():
                governed = await require_verified_evidence(
                    session,
                    source_draft_id=draft_id,
                    source_draft_version=1,
                )
                assert governed.public_state is EvidencePublicState.REFERENCE_ONLY
            async with session.begin():
                tombstone = await tombstone_evidence(
                    session,
                    evidence_id=manifest.evidence_id,
                    removed_by_actor_id=remover_id,
                    reason="Governed removal after rights withdrawal.",
                    now=NOW,
                )
                replayed_tombstone = await tombstone_evidence(
                    session,
                    evidence_id=manifest.evidence_id,
                    removed_by_actor_id=remover_id,
                    reason="Governed removal after rights withdrawal.",
                    now=NOW,
                )
                assert replayed_tombstone == tombstone
            async with session.begin():
                bundle = await load_bundle(session, manifest.evidence_id)
                assert bundle.public_state is EvidencePublicState.TOMBSTONED
                assert bundle.tombstone is not None
                assert bundle.tombstone.prior_state is EvidencePublicState.REFERENCE_ONLY
                with pytest.raises(EvidenceConflictError, match="Tombstoned"):
                    await record_acknowledgements(
                        session,
                        manifest.evidence_id,
                        acknowledgements,
                    )
        connection = await asyncpg.connect(database_url.replace("+asyncpg", ""))
        try:
            for statement in (
                "UPDATE evidence_manifests SET manifest_digest = repeat('0', 64) WHERE id = $1",
                "DELETE FROM evidence_durable_acknowledgements WHERE evidence_id = $1",
                "UPDATE evidence_removal_tombstones SET reason = 'rewritten' "
                "WHERE evidence_id = $1",
            ):
                with pytest.raises(asyncpg.RaiseError, match="immutable|append-only"):
                    await connection.execute(statement, manifest.evidence_id)
        finally:
            await connection.close()
    finally:
        await engine.dispose()


@pytest.mark.skipif(
    INTEGRATION_DATABASE_URL is None,
    reason="INTEGRATION_DATABASE_URL is required for PostgreSQL integration tests",
)
def test_evidence_repository_is_idempotent_verified_and_tombstone_preserving() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_run_repository_contract(INTEGRATION_DATABASE_URL))


def test_acknowledgement_kind_fixture_is_stable() -> None:
    assert EvidenceAcknowledgementKind.CITATION_MANIFEST.value == "citation_manifest"
