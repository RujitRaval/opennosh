from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg  # type: ignore[import-untyped]
import pytest
from alembic import command as alembic_command
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from opennosh_api.federation.contracts import (
    FederationReleaseStatement,
    FederationScope,
    SignedFederationRelease,
    encode_public_key,
    release_signature_material,
)
from opennosh_api.federation.service import FederationError, FederationService
from opennosh_api.jobs import JobLane, JobMessage
from opennosh_api.jobs.worker import asyncpg_dsn
from opennosh_api.publication.executor import PublicationEffectExecutor
from opennosh_api.publication.orchestrator import PublicationOrchestrator
from opennosh_api.publication.receipts import SignedPublicationReceipt
from opennosh_api.publication.repository import PostgresPublicationRepository
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.tests.test_migrations import migration_config
from api.tests.workflow_testkit import (
    FORGE_TARGET,
    DeterministicClock,
    DeterministicIdGenerator,
    PersistentExternalState,
    publication_adapter_registry,
    reset_trust_tables,
    seed_publication,
)

INTEGRATION_DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL")
NOW = datetime(2026, 8, 29, 14, tzinfo=UTC)
NAMESPACE = UUID("11d53b03-4da4-4058-9565-719b727268f3")


class VerifiedInstallation:
    def __init__(self) -> None:
        self.calls = 0

    async def verify(self, scope: FederationScope, *, installation_id: int) -> None:
        assert scope.repository == FORGE_TARGET.removeprefix("github:")
        assert installation_id == 157058059
        self.calls += 1


async def _published_release(database_url: str) -> tuple[object, dict[str, object], str]:
    clock = DeterministicClock(NOW)
    ids = DeterministicIdGenerator(NAMESPACE)
    seeded = await seed_publication(
        database_url,
        now=clock(),
        ids=ids,
        suffix="federation-release",
    )
    pool = await asyncpg.create_pool(asyncpg_dsn(database_url), min_size=1, max_size=3)
    assert pool is not None
    orchestrator = PublicationOrchestrator(
        repository=PostgresPublicationRepository(pool),
        executor=PublicationEffectExecutor(
            publication_adapter_registry(PersistentExternalState(), clock)
        ),
        owner="federation-release-test",
        clock=clock,
    )
    message = JobMessage(
        lane=JobLane.PUBLICATION,
        job_type="publication.wake",
        subject_id=seeded.publication_id,
        idempotency_key="federation-release-publication",
    )
    for _ in range(12):
        await orchestrator.process(message, queue_job_id=seeded.queue_job_id)
        clock.advance(timedelta(seconds=1))
    row = await pool.fetchrow(
        "SELECT envelope_json, receipt_digest FROM publication_receipts WHERE publication_id = $1",
        seeded.publication_id,
    )
    assert row is not None
    await pool.close()
    envelope_value = row["envelope_json"]
    if isinstance(envelope_value, str):
        envelope_value = json.loads(envelope_value)
    assert isinstance(envelope_value, dict)
    return seeded, envelope_value, str(row["receipt_digest"])


async def run_lifecycle(database_url: str) -> None:
    await reset_trust_tables(database_url)
    seeded, envelope_json, receipt_digest = await _published_release(database_url)
    actor_id = uuid4()
    connection = await asyncpg.connect(asyncpg_dsn(database_url))
    try:
        await connection.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, 'hash')",
            actor_id,
            f"federation-steward-{actor_id}@example.test",
        )
        await connection.execute(
            """
            INSERT INTO governance_role_assignments (
                pack_id, actor_id, role, granted_by_actor_id,
                grant_reason, granted_at
            ) VALUES ('global-core', $1, 'steward', $1, $2, $3)
            """,
            actor_id,
            "Bootstrap the federation lifecycle test steward",
            NOW - timedelta(minutes=1),
        )
    finally:
        await connection.close()

    scope = FederationScope(
        github_account_id=280184755,
        github_login="aarolabs",
        repository_id=1339461317,
        repository=FORGE_TARGET.removeprefix("github:"),
        pack_id="global-core",
    )
    verifier = VerifiedInstallation()
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = FederationService(
        factory,
        allowed_scope=scope,
        allowed_public_origin="https://opennosh.example",
        installation_verifier=verifier,
    )
    first_key = Ed25519PrivateKey.from_private_bytes(b"f" * 32)
    second_key = Ed25519PrivateKey.from_private_bytes(b"g" * 32)
    first_public = encode_public_key(first_key.public_key())
    first_fingerprint = hashlib.sha256(first_key.public_key().public_bytes_raw()).hexdigest()
    second_public = encode_public_key(second_key.public_key())
    second_fingerprint = hashlib.sha256(second_key.public_key().public_bytes_raw()).hexdigest()
    try:
        with pytest.raises(FederationError, match="federation_steward_not_active"):
            await service.invite(
                scope,
                inviter_actor_id=uuid4(),
                expires_at=NOW + timedelta(hours=1),
                now=NOW,
            )
        with pytest.raises(FederationError, match="invitation_expiry_invalid"):
            await service.invite(
                scope,
                inviter_actor_id=actor_id,
                expires_at=NOW + timedelta(hours=25),
                now=NOW,
            )
        invitation = await service.invite(
            scope,
            inviter_actor_id=actor_id,
            expires_at=NOW + timedelta(hours=1),
            now=NOW,
        )
        with pytest.raises(FederationError, match="invitation_limit_reached"):
            await service.invite(
                scope,
                inviter_actor_id=actor_id,
                expires_at=NOW + timedelta(hours=2),
                now=NOW,
            )
        with pytest.raises(FederationError, match="role_key_fingerprint_mismatch"):
            await service.verify(
                token=invitation.token,
                scope=scope,
                installation_id=157058059,
                key_id="maintainer-2026-01",
                public_key=first_public,
                public_key_fingerprint="0" * 64,
                now=NOW + timedelta(minutes=1),
            )
        verified = await service.verify(
            token=invitation.token,
            scope=scope,
            installation_id=157058059,
            key_id="maintainer-2026-01",
            public_key=first_public,
            public_key_fingerprint=first_fingerprint,
            now=NOW + timedelta(minutes=1),
        )
        assert verified.state == "verified"
        with pytest.raises(FederationError, match="already_consumed"):
            await service.verify(
                token=invitation.token,
                scope=scope,
                installation_id=157058059,
                key_id="maintainer-2026-01",
                public_key=first_public,
                public_key_fingerprint=first_fingerprint,
                now=NOW + timedelta(minutes=2),
            )
        assert verifier.calls == 1

        active = await service.activate(
            verified.maintainer_id,
            actor_id=actor_id,
            reason="Activate the first bounded federation maintainer",
            now=NOW + timedelta(minutes=3),
        )
        assert active.state == "active"

        receipt = SignedPublicationReceipt.model_validate(envelope_json).receipt
        statement = FederationReleaseStatement(
            maintainer_id=active.maintainer_id,
            repository_id=scope.repository_id,
            repository=scope.repository,
            pack_id=scope.pack_id,
            publication_id=seeded.publication_id,
            release_version=receipt.release_version,
            manifest_digest=receipt.signed_release_metadata_digest,
            receipt_digest=receipt_digest,
            public_url=(
                "https://opennosh.example/api/v1/public/releases/"
                f"{receipt.release_version}/manifest"
            ),
            issued_at=NOW + timedelta(minutes=4),
            key_id="maintainer-2026-01",
        )
        signature = (
            base64.urlsafe_b64encode(first_key.sign(release_signature_material(statement)))
            .decode("ascii")
            .rstrip("=")
        )
        release = SignedFederationRelease(statement=statement, signature=signature)
        assert (
            len(
                await service.publish_release(
                    release,
                    actor_id=actor_id,
                    reason="Enroll the governed pack release",
                    now=NOW + timedelta(minutes=4),
                )
            )
            == 64
        )

        rotated = await service.rotate_key(
            active.maintainer_id,
            key_id="maintainer-2026-02",
            public_key=second_public,
            public_key_fingerprint=second_fingerprint,
            actor_id=actor_id,
            reason="Exercise first online role-key rotation",
            now=NOW + timedelta(minutes=5),
        )
        assert rotated.current_role_key_id == "maintainer-2026-02"
        with pytest.raises(FederationError, match="retired_or_untrusted"):
            await service.publish_release(
                release,
                actor_id=actor_id,
                reason="Prove the retired key is rejected",
                now=NOW + timedelta(minutes=6),
            )
        await service.record_rejected_attempt(
            actor_id=actor_id,
            operation="publish-release",
            code="release_key_retired_or_untrusted",
            maintainer_id=active.maintainer_id,
            now=NOW + timedelta(minutes=6),
        )

        pending = await seed_publication(
            database_url,
            now=NOW + timedelta(minutes=7),
            ids=DeterministicIdGenerator(UUID("adf3a23f-2bbc-4612-9c1d-35f8fec40b66")),
            suffix="federation-quarantine",
            manifest_digest="e" * 64,
        )
        connection = await asyncpg.connect(asyncpg_dsn(database_url))
        try:
            pending_revision = await connection.fetchval(
                "SELECT workflow_revision FROM publication_intents WHERE id = $1",
                pending.publication_id,
            )
        finally:
            await connection.close()
        quarantined = await service.quarantine(
            active.maintainer_id,
            actor_id=actor_id,
            reason="Complete the bounded quarantine proof",
            now=NOW + timedelta(minutes=8),
        )
        assert quarantined.state == "quarantined"

        connection = await asyncpg.connect(asyncpg_dsn(database_url))
        try:
            assert (
                await connection.fetchval(
                    "SELECT state FROM publication_intents WHERE id = $1", pending.publication_id
                )
                == "publish_blocked"
            )
            assert (
                await connection.fetchval(
                    "SELECT workflow_revision FROM publication_intents WHERE id = $1",
                    pending.publication_id,
                )
                == pending_revision + 1
            )
            assert (
                await connection.fetchval(
                    "SELECT count(*) FROM publication_receipts WHERE publication_id = $1",
                    seeded.publication_id,
                )
                == 1
            )
            assert (
                await connection.fetchval(
                    "SELECT count(*) FROM federation_audit_events WHERE maintainer_id = $1",
                    active.maintainer_id,
                )
                == 8
            )
            assert (
                await connection.fetchval(
                    "SELECT count(*) FROM federation_role_keys WHERE maintainer_id = $1",
                    active.maintainer_id,
                )
                == 2
            )
            with pytest.raises(asyncpg.CheckViolationError, match="append_only"):
                await connection.execute(
                    "DELETE FROM federation_audit_events WHERE maintainer_id = $1",
                    active.maintainer_id,
                )
        finally:
            await connection.close()
    finally:
        await engine.dispose()


@pytest.mark.skipif(
    INTEGRATION_DATABASE_URL is None,
    reason="INTEGRATION_DATABASE_URL is required for PostgreSQL integration tests",
)
def test_single_invitation_release_rotation_and_quarantine_lifecycle() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    alembic_command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(run_lifecycle(INTEGRATION_DATABASE_URL))
