from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import UTC, datetime
from io import BytesIO
from uuid import UUID, uuid4

import asyncpg  # type: ignore[import-untyped]
import pytest
from alembic import command
from opennosh_api.contributions.models import ContributionDraft
from opennosh_api.evidence.contracts import EvidenceAcknowledgementKind, EvidenceManifest
from opennosh_api.evidence.repository import EvidenceConflictError
from opennosh_api.evidence.runtime import EvidenceJobProcessor, EvidenceWorkerRepository
from opennosh_api.evidence.sanitization import DeterministicAllowEvidenceScanner
from opennosh_api.evidence.sanitizer_worker import (
    EvidenceSanitizationJobProcessor,
    EvidenceSanitizationRepository,
)
from opennosh_api.evidence.service import attach_sanitized_upload
from opennosh_api.evidence.storage import MemoryEvidenceStore, MemoryEvidenceUploadBroker
from opennosh_api.evidence.uploads import (
    EvidenceUploadNotFoundError,
    EvidenceUploadQuotaError,
    EvidenceUploadState,
    complete_upload_session,
    create_upload_session,
)
from opennosh_api.jobs.pgqueuer import PgQueuerJobQueue
from opennosh_api.jobs.worker import asyncpg_dsn
from opennosh_api.models.auth import User
from PIL import Image
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.tests.test_migrations import migration_config

INTEGRATION_DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL")
NOW = datetime.now(UTC)


def _png() -> bytes:
    image = Image.new("RGB", (5, 4), "green")
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


async def _reset(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    TRUNCATE auth_rate_limits, evidence_durable_acknowledgements,
                        evidence_manifests, evidence_upload_sessions,
                        contribution_drafts, users, opennosh_pgqueuer CASCADE
                    """
                )
            )
    finally:
        await engine.dispose()


async def _seed_drafts(database_url: str, count: int) -> tuple[UUID, list[UUID]]:
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user_id = uuid4()
    draft_ids = [uuid4() for _ in range(count)]
    try:
        async with sessions() as session:
            async with session.begin():
                session.add(User(id=user_id, email=f"{user_id}@example.test", password_hash="hash"))
                await session.flush()
                session.add_all(
                    ContributionDraft(
                        id=draft_id,
                        user_id=user_id,
                        client_draft_id=f"sanitization-{index}",
                    )
                    for index, draft_id in enumerate(draft_ids)
                )
        return user_id, draft_ids
    finally:
        await engine.dispose()


async def _assert_concurrent_quotas(database_url: str) -> None:
    await _reset(database_url)
    user_id, draft_ids = await _seed_drafts(database_url, 6)
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    broker = MemoryEvidenceUploadBroker()

    async def create(draft_id: UUID, index: int, *, account_limit: int, draft_limit: int):
        async with sessions() as session:
            return await create_upload_session(
                session,
                broker,
                draft_id=draft_id,
                user_id=user_id,
                source_draft_version=1,
                media_type="image/png",
                byte_length=8,
                idempotency_key=f"quota-{index}",
                now=NOW,
                outstanding_account_limit=account_limit,
                outstanding_draft_limit=draft_limit,
            )

    try:
        same_draft = await asyncio.gather(
            *(create(draft_ids[0], index, account_limit=5, draft_limit=2) for index in range(3)),
            return_exceptions=True,
        )
        assert sum(isinstance(result, EvidenceUploadQuotaError) for result in same_draft) == 1
        async with sessions() as session:
            count = await session.scalar(select(func.count()).select_from(ContributionDraft))
            assert count == 6
            uploads = await session.scalar(
                text("SELECT count(*) FROM evidence_upload_sessions")
            )
            assert uploads == 2

        await _reset(database_url)
        user_id, draft_ids = await _seed_drafts(database_url, 6)
        account = await asyncio.gather(
            *(
                create(draft_id, index + 10, account_limit=5, draft_limit=2)
                for index, draft_id in enumerate(draft_ids)
            ),
            return_exceptions=True,
        )
        assert sum(isinstance(result, EvidenceUploadQuotaError) for result in account) == 1
        async with sessions() as session:
            uploads = await session.scalar(
                text("SELECT count(*) FROM evidence_upload_sessions")
            )
            assert uploads == 5
    finally:
        await engine.dispose()


class _SanitizedSource:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    async def payloads_for(
        self,
        manifest: EvidenceManifest,
    ) -> dict[EvidenceAcknowledgementKind, bytes]:
        del manifest
        return {EvidenceAcknowledgementKind.IMMUTABLE_SANITIZED_COPY: self._payload}


async def _assert_full_sanitization_attachment_preservation(database_url: str) -> None:
    await _reset(database_url)
    user_id, (draft_id,) = await _seed_drafts(database_url, 1)
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    broker = MemoryEvidenceUploadBroker()
    source = _png()
    queue = PgQueuerJobQueue(clock=lambda: NOW)
    pool = await asyncpg.create_pool(asyncpg_dsn(database_url), min_size=1, max_size=3)
    assert pool is not None
    sanitized_store = MemoryEvidenceStore(destination="urn:test:sanitized")
    immutable_store = MemoryEvidenceStore(destination="urn:test:immutable")
    try:
        async with sessions() as session:
            created = await create_upload_session(
                session,
                broker,
                draft_id=draft_id,
                user_id=user_id,
                source_draft_version=1,
                media_type="image/png",
                byte_length=len(source),
                idempotency_key="full-sanitization-flow",
                now=NOW,
            )
        assert created.completion_capability is not None
        broker.put_for_test(
            f"quarantine/{created.session.upload_id}",
            source,
            media_type="image/png",
        )
        async with sessions() as session:
            completed = await complete_upload_session(
                session,
                broker,
                upload_id=created.session.upload_id,
                draft_id=draft_id,
                user_id=user_id,
                completion_capability=created.completion_capability,
                now=NOW,
                queue=queue,
            )
        assert completed.state is EvidenceUploadState.UPLOADED

        sanitizer = EvidenceSanitizationJobProcessor(
            EvidenceSanitizationRepository(pool),
            broker,
            sanitized_store,
            DeterministicAllowEvidenceScanner(),
            clock=lambda: datetime.now(UTC),
            max_bytes=10_485_760,
        )
        sanitized = await sanitizer.process(
            created.session.upload_id,
            workflow_revision=2,
        )
        assert sanitized.state is EvidenceUploadState.SANITIZED
        assert broker.objects == {}
        assert len(sanitized_store.objects) == 1
        sanitized_payload = next(iter(sanitized_store.objects.values()))

        attached_at = datetime.now(UTC)
        async with sessions() as session:
            attached = await attach_sanitized_upload(
                session,
                queue,
                upload_id=created.session.upload_id,
                draft_id=draft_id,
                user_id=user_id,
                source_draft_version=1,
                source_description="Packaging nutrition label",
                rights_acknowledged=True,
                redaction_state="reviewed",  # type: ignore[arg-type]
                now=attached_at,
            )
        assert attached.state is EvidenceUploadState.ATTACHED
        assert attached.evidence_id is not None

        async with sessions() as session:
            replayed = await attach_sanitized_upload(
                session,
                queue,
                upload_id=created.session.upload_id,
                draft_id=draft_id,
                user_id=user_id,
                source_draft_version=1,
                source_description="Packaging nutrition label",
                rights_acknowledged=True,
                redaction_state="reviewed",  # type: ignore[arg-type]
                now=attached_at,
            )
        assert replayed.evidence_id == attached.evidence_id

        async with sessions() as session:
            with pytest.raises(EvidenceConflictError):
                await attach_sanitized_upload(
                    session,
                    queue,
                    upload_id=created.session.upload_id,
                    draft_id=draft_id,
                    user_id=user_id,
                    source_draft_version=1,
                    source_description="A conflicting description",
                    rights_acknowledged=True,
                    redaction_state="reviewed",  # type: ignore[arg-type]
                    now=attached_at,
                )
        async with sessions() as session:
            with pytest.raises(EvidenceUploadNotFoundError):
                await attach_sanitized_upload(
                    session,
                    queue,
                    upload_id=created.session.upload_id,
                    draft_id=draft_id,
                    user_id=uuid4(),
                    source_draft_version=1,
                    source_description="Packaging nutrition label",
                    rights_acknowledged=True,
                    redaction_state="reviewed",  # type: ignore[arg-type]
                    now=attached_at,
                )

        processor = EvidenceJobProcessor(
            EvidenceWorkerRepository(pool),
            _SanitizedSource(sanitized_payload),  # type: ignore[arg-type]
            immutable_store,
            clock=lambda: datetime.now(UTC),
        )
        await processor.process(attached.evidence_id)
        async with sessions() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT state, preserved_at FROM evidence_upload_sessions WHERE id = :id"
                    ),
                    {"id": created.session.upload_id},
                )
            ).one()
            queued = await session.scalar(text("SELECT count(*) FROM opennosh_pgqueuer"))
        assert row.state == "preserved"
        assert row.preserved_at is not None
        assert row.preserved_at >= attached_at
        assert queued == 2
        immutable_payload = next(iter(immutable_store.objects.values()))
        assert hashlib.sha256(immutable_payload).hexdigest() == hashlib.sha256(
            sanitized_payload
        ).hexdigest()
    finally:
        await pool.close()
        await engine.dispose()


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_evidence_upload_quotas_are_race_safe() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_assert_concurrent_quotas(INTEGRATION_DATABASE_URL))


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_sanitized_upload_attaches_and_preserves_atomically() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_assert_full_sanitization_attachment_preservation(INTEGRATION_DATABASE_URL))
