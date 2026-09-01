from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from alembic import command
from opennosh_api.contributions.models import ContributionDraft
from opennosh_api.evidence.storage import (
    EvidenceUploadObjectTooLargeError,
    EvidenceUploadStorageError,
    MemoryEvidenceUploadBroker,
    QuarantinedEvidenceObservation,
)
from opennosh_api.evidence.uploads import (
    EvidenceUploadConflictError,
    EvidenceUploadExpiredError,
    EvidenceUploadNotFoundError,
    EvidenceUploadState,
    EvidenceUploadUnavailableError,
    complete_upload_session,
    create_upload_session,
    get_upload_session,
)
from opennosh_api.models.auth import User
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.tests.test_migrations import migration_config

INTEGRATION_DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL")


class CreateUnavailableBroker(MemoryEvidenceUploadBroker):
    async def create_upload(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        raise EvidenceUploadStorageError("create unavailable")


class ObserveUnavailableBroker(MemoryEvidenceUploadBroker):
    async def observe(self, object_key: str, *, max_bytes: int):  # type: ignore[no-untyped-def]
        del object_key, max_bytes
        raise EvidenceUploadStorageError("observe unavailable")


class TooLargeBroker(MemoryEvidenceUploadBroker):
    async def observe(self, object_key: str, *, max_bytes: int):  # type: ignore[no-untyped-def]
        del object_key, max_bytes
        raise EvidenceUploadObjectTooLargeError("too large")


class OversizeObservationBroker(MemoryEvidenceUploadBroker):
    async def observe(
        self, object_key: str, *, max_bytes: int
    ) -> QuarantinedEvidenceObservation | None:
        del max_bytes
        return QuarantinedEvidenceObservation(
            object_key=object_key,
            media_type="image/png",
            size_bytes=9,
            content_digest="a" * 64,
            revision='"oversize"',
        )


class ChangingBroker(MemoryEvidenceUploadBroker):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def observe(
        self, object_key: str, *, max_bytes: int
    ) -> QuarantinedEvidenceObservation | None:
        observed = await super().observe(object_key, max_bytes=max_bytes)
        self.calls += 1
        if observed is None:
            return None
        return QuarantinedEvidenceObservation(
            object_key=observed.object_key,
            media_type=observed.media_type,
            size_bytes=observed.size_bytes,
            content_digest=observed.content_digest,
            revision=f'"revision-{self.calls}"',
        )


async def _run_upload_repository_contract(database_url: str) -> None:
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user_id = uuid4()
    draft_ids = [uuid4() for _ in range(12)]
    now = datetime.now(UTC)

    async def create(
        draft_id: UUID,
        broker: MemoryEvidenceUploadBroker,
        key: str,
        *,
        byte_length: int = 8,
        source_draft_version: int = 1,
    ):
        async with sessions() as session:
            return await create_upload_session(
                session,
                broker,
                draft_id=draft_id,
                user_id=user_id,
                source_draft_version=source_draft_version,
                media_type="image/png",
                byte_length=byte_length,
                idempotency_key=key,
                now=now,
            )

    async def complete(
        draft_id: UUID,
        upload_id: UUID,
        broker: MemoryEvidenceUploadBroker,
        capability: str,
        *,
        completed_at: datetime = now,
        max_bytes: int = 10_485_760,
    ):
        async with sessions() as session:
            return await complete_upload_session(
                session,
                broker,
                upload_id=upload_id,
                draft_id=draft_id,
                user_id=user_id,
                completion_capability=capability,
                now=completed_at,
                max_bytes=max_bytes,
            )

    try:
        async with sessions() as session:
            async with session.begin():
                session.add(
                    User(
                        id=user_id,
                        email=f"{user_id}@example.test",
                        password_hash="hash",
                    )
                )
                await session.flush()
                session.add_all(
                    ContributionDraft(
                        id=draft_id,
                        user_id=user_id,
                        client_draft_id=f"upload-{index}",
                    )
                    for index, draft_id in enumerate(draft_ids)
                )

        with pytest.raises(EvidenceUploadNotFoundError):
            await create(uuid4(), MemoryEvidenceUploadBroker(), "missing")
        with pytest.raises(EvidenceUploadConflictError):
            await create(
                draft_ids[0],
                MemoryEvidenceUploadBroker(),
                "stale-version",
                source_draft_version=2,
            )
        with pytest.raises(EvidenceUploadUnavailableError):
            await create(draft_ids[0], CreateUnavailableBroker(), "create-unavailable")

        idempotent_broker = MemoryEvidenceUploadBroker()
        idempotent = await create(draft_ids[0], idempotent_broker, "idempotent")
        replay = await create(draft_ids[0], idempotent_broker, "idempotent")
        assert replay.replayed is True
        assert replay.session.upload_id == idempotent.session.upload_id
        with pytest.raises(EvidenceUploadConflictError):
            await create(
                draft_ids[0],
                idempotent_broker,
                "idempotent",
                byte_length=9,
            )
        assert idempotent.completion_capability is not None
        with pytest.raises(EvidenceUploadNotFoundError):
            await complete(draft_ids[0], idempotent.session.upload_id, idempotent_broker, "x" * 43)

        missing_broker = MemoryEvidenceUploadBroker()
        missing = await create(draft_ids[1], missing_broker, "missing-object")
        assert missing.completion_capability is not None
        with pytest.raises(EvidenceUploadConflictError):
            await complete(
                draft_ids[1],
                missing.session.upload_id,
                missing_broker,
                missing.completion_capability,
            )
        with pytest.raises(EvidenceUploadConflictError):
            await complete(
                draft_ids[1],
                missing.session.upload_id,
                missing_broker,
                missing.completion_capability,
            )

        for index, payload, media_type in (
            (2, b"wrong-size", "image/png"),
            (3, b"evidence", "image/jpeg"),
        ):
            broker = MemoryEvidenceUploadBroker()
            created = await create(draft_ids[index], broker, f"mismatch-{index}")
            assert created.completion_capability is not None
            broker.put_for_test(
                f"quarantine/{created.session.upload_id}",
                payload,
                media_type=media_type,
            )
            with pytest.raises(EvidenceUploadConflictError):
                await complete(
                    draft_ids[index],
                    created.session.upload_id,
                    broker,
                    created.completion_capability,
                )

        changing = ChangingBroker()
        changed = await create(draft_ids[4], changing, "changed")
        assert changed.completion_capability is not None
        changing.put_for_test(
            f"quarantine/{changed.session.upload_id}", b"evidence", media_type="image/png"
        )
        with pytest.raises(EvidenceUploadConflictError):
            await complete(
                draft_ids[4],
                changed.session.upload_id,
                changing,
                changed.completion_capability,
            )

        for index, broker, key in (
            (5, TooLargeBroker(), "too-large"),
            (6, OversizeObservationBroker(), "oversize-observation"),
        ):
            created = await create(draft_ids[index], broker, key)
            assert created.completion_capability is not None
            with pytest.raises(EvidenceUploadConflictError):
                await complete(
                    draft_ids[index],
                    created.session.upload_id,
                    broker,
                    created.completion_capability,
                    max_bytes=8,
                )

        unavailable_broker = ObserveUnavailableBroker()
        unavailable = await create(draft_ids[7], unavailable_broker, "observe-unavailable")
        assert unavailable.completion_capability is not None
        with pytest.raises(EvidenceUploadUnavailableError):
            await complete(
                draft_ids[7],
                unavailable.session.upload_id,
                unavailable_broker,
                unavailable.completion_capability,
            )

        stale_broker = MemoryEvidenceUploadBroker()
        stale = await create(draft_ids[8], stale_broker, "stale-draft")
        assert stale.completion_capability is not None
        stale_broker.put_for_test(
            f"quarantine/{stale.session.upload_id}", b"evidence", media_type="image/png"
        )
        async with sessions() as session:
            await session.execute(
                update(ContributionDraft)
                .where(ContributionDraft.id == draft_ids[8])
                .values(draft_version=2)
            )
            await session.commit()
        with pytest.raises(EvidenceUploadConflictError):
            await complete(
                draft_ids[8],
                stale.session.upload_id,
                stale_broker,
                stale.completion_capability,
            )

        expired_broker = MemoryEvidenceUploadBroker()
        expired = await create(draft_ids[9], expired_broker, "expired")
        assert expired.completion_capability is not None
        with pytest.raises(EvidenceUploadExpiredError):
            await complete(
                draft_ids[9],
                expired.session.upload_id,
                expired_broker,
                expired.completion_capability,
                completed_at=now + timedelta(minutes=11),
            )
        with pytest.raises(EvidenceUploadExpiredError):
            await complete(
                draft_ids[9],
                expired.session.upload_id,
                expired_broker,
                expired.completion_capability,
                completed_at=now + timedelta(minutes=11),
            )

        read_expired = await create(draft_ids[10], MemoryEvidenceUploadBroker(), "read-expired")
        async with sessions() as session:
            view = await get_upload_session(
                session,
                upload_id=read_expired.session.upload_id,
                draft_id=draft_ids[10],
                user_id=user_id,
                now=now + timedelta(minutes=11),
            )
        assert view.state is EvidenceUploadState.EXPIRED

        uploaded_broker = MemoryEvidenceUploadBroker()
        uploaded = await create(draft_ids[11], uploaded_broker, "uploaded")
        assert uploaded.completion_capability is not None
        uploaded_broker.put_for_test(
            f"quarantine/{uploaded.session.upload_id}", b"evidence", media_type="image/png"
        )
        completed = await complete(
            draft_ids[11],
            uploaded.session.upload_id,
            uploaded_broker,
            uploaded.completion_capability,
        )
        replayed = await complete(
            draft_ids[11],
            uploaded.session.upload_id,
            uploaded_broker,
            uploaded.completion_capability,
        )
        assert completed.state is replayed.state is EvidenceUploadState.UPLOADED
    finally:
        await engine.dispose()


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_upload_repository_state_machine_failure_and_replay_contract() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_run_upload_repository_contract(INTEGRATION_DATABASE_URL))
