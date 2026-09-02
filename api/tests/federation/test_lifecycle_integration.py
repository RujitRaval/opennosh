from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import os
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg  # type: ignore[import-untyped]
import pytest
from alembic import command as alembic_command
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from opennosh_api.federation.contracts import (
    FederationReleaseStatement,
    FederationScope,
    InvitationSecret,
    SignedFederationRelease,
    encode_public_key,
    release_signature_material,
    release_statement_digest,
)
from opennosh_api.federation.service import FederationError, FederationService
from opennosh_api.foodpacks.loader import prepare_food_pack
from opennosh_api.jobs import JobLane, JobMessage
from opennosh_api.jobs.worker import asyncpg_dsn
from opennosh_api.main import create_app
from opennosh_api.public.artifacts import (
    PublicPackArtifact,
    PublicReadReleaseManifest,
    artifact_descriptor,
)
from opennosh_api.public.bootstrap import _pack_archive
from opennosh_api.public.signing import public_key_text, sign_envelope
from opennosh_api.public_commons.manifests import ManifestKeyRing
from opennosh_api.publication.executor import PublicationEffectExecutor
from opennosh_api.publication.orchestrator import PublicationOrchestrator
from opennosh_api.publication.receipts import SignedPublicationReceipt
from opennosh_api.publication.repository import PostgresPublicationRepository
from opennosh_api.settings import Settings
from sqlalchemy.exc import DBAPIError
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
ROOT = Path(__file__).resolve().parents[3]


class VerifiedInstallation:
    def __init__(self) -> None:
        self.calls = 0

    async def verify(self, scope: FederationScope, *, installation_id: int) -> None:
        assert scope.repository.count("/") == 1
        assert installation_id > 0
        self.calls += 1


async def _published_release(
    database_url: str,
    *,
    now: datetime = NOW,
    namespace: UUID = NAMESPACE,
    suffix: str = "federation-release",
    manifest_digest: str = "f" * 64,
    release_version: str = "2026.08.26-testkit",
) -> tuple[object, dict[str, object], str]:
    clock = DeterministicClock(now)
    ids = DeterministicIdGenerator(namespace)
    seeded = await seed_publication(
        database_url,
        now=clock(),
        ids=ids,
        suffix=suffix,
        manifest_digest=manifest_digest,
        approved_payload_digest=manifest_digest,
    )
    pool = await asyncpg.create_pool(asyncpg_dsn(database_url), min_size=1, max_size=3)
    assert pool is not None
    orchestrator = PublicationOrchestrator(
        repository=PostgresPublicationRepository(pool),
        executor=PublicationEffectExecutor(
            publication_adapter_registry(
                PersistentExternalState(),
                clock,
                release_version=release_version,
            )
        ),
        owner=f"{suffix}-test",
        clock=clock,
    )
    message = JobMessage(
        lane=JobLane.PUBLICATION,
        job_type="publication.wake",
        subject_id=seeded.publication_id,
        idempotency_key=f"{suffix}-publication",
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
        allowed_scopes=(scope,),
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
            # Exercise the existing five-minute signer clock-skew allowance all the
            # way through the database constraint.
            issued_at=NOW + timedelta(minutes=4, seconds=30),
            key_id="maintainer-2026-01",
        )
        signature = (
            base64.urlsafe_b64encode(first_key.sign(release_signature_material(statement)))
            .decode("ascii")
            .rstrip("=")
        )
        release = SignedFederationRelease(statement=statement, signature=signature)
        first_digest = await service.publish_release(
            release,
            actor_id=actor_id,
            reason="Enroll the governed pack release",
            now=NOW + timedelta(minutes=4),
        )
        assert len(first_digest) == 64
        assert (
            await service.publish_release(
                release,
                actor_id=actor_id,
                reason="Replay the exact governed pack release",
                now=NOW + timedelta(minutes=4),
            )
            == first_digest
        )

        conflicting_statement = statement.model_copy(
            update={"issued_at": NOW + timedelta(minutes=4, seconds=31)}
        )
        conflicting_release = SignedFederationRelease(
            statement=conflicting_statement,
            signature=(
                base64.urlsafe_b64encode(
                    first_key.sign(release_signature_material(conflicting_statement))
                )
                .decode("ascii")
                .rstrip("=")
            ),
        )
        with pytest.raises(FederationError, match="release_version_conflict"):
            await service.publish_release(
                conflicting_release,
                actor_id=actor_id,
                reason="Prove a reused release version is rejected",
                now=NOW + timedelta(minutes=4, seconds=1),
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

        second_seeded, second_envelope_json, second_receipt_digest = await _published_release(
            database_url,
            now=NOW + timedelta(minutes=6),
            namespace=UUID("06ea51a5-17f4-4ee8-9026-04ac2f48af2a"),
            suffix="federation-release-second",
            manifest_digest="d" * 64,
            release_version="2026.08.26-testkit-2",
        )
        second_receipt = SignedPublicationReceipt.model_validate(second_envelope_json).receipt
        assert second_receipt.release_version == "2026.08.26-testkit-2"
        second_statement = FederationReleaseStatement(
            maintainer_id=active.maintainer_id,
            repository_id=scope.repository_id,
            repository=scope.repository,
            pack_id=scope.pack_id,
            publication_id=second_seeded.publication_id,
            release_version=second_receipt.release_version,
            manifest_digest=second_receipt.signed_release_metadata_digest,
            receipt_digest=second_receipt_digest,
            public_url=(
                "https://opennosh.example/api/v1/public/releases/"
                f"{second_receipt.release_version}/manifest"
            ),
            issued_at=NOW + timedelta(minutes=7),
            key_id="maintainer-2026-02",
        )
        with pytest.raises(FederationError, match="retired_or_untrusted"):
            await service.publish_release(
                release,
                actor_id=actor_id,
                reason="Prove an exact replay under the retired key is rejected",
                now=NOW + timedelta(minutes=7),
            )
        await service.record_rejected_attempt(
            actor_id=actor_id,
            operation="publish-release",
            code="release_key_retired_or_untrusted",
            maintainer_id=active.maintainer_id,
            now=NOW + timedelta(minutes=7),
        )

        third_seeded, third_envelope_json, third_receipt_digest = await _published_release(
            database_url,
            now=NOW + timedelta(minutes=8),
            namespace=UUID("26ec9abc-6c85-416c-8cbe-b0f1828a32cf"),
            suffix="federation-release-third",
            manifest_digest="e" * 64,
            release_version="2026.08.26-testkit-3",
        )
        third_receipt = SignedPublicationReceipt.model_validate(third_envelope_json).receipt
        assert third_receipt.release_version == "2026.08.26-testkit-3"
        third_statement = FederationReleaseStatement(
            maintainer_id=active.maintainer_id,
            repository_id=scope.repository_id,
            repository=scope.repository,
            pack_id=scope.pack_id,
            publication_id=third_seeded.publication_id,
            release_version=third_receipt.release_version,
            manifest_digest=third_receipt.signed_release_metadata_digest,
            receipt_digest=third_receipt_digest,
            public_url=(
                "https://opennosh.example/api/v1/public/releases/"
                f"{third_receipt.release_version}/manifest"
            ),
            issued_at=NOW + timedelta(minutes=9),
            key_id="maintainer-2026-02",
        )
        third_release = SignedFederationRelease(
            statement=third_statement,
            signature=(
                base64.urlsafe_b64encode(
                    second_key.sign(release_signature_material(third_statement))
                )
                .decode("ascii")
                .rstrip("=")
            ),
        )
        concurrent_digests = await asyncio.gather(
            service.publish_release(
                third_release,
                actor_id=actor_id,
                reason="Enroll the next governed pack release",
                now=NOW + timedelta(minutes=9),
            ),
            service.publish_release(
                third_release,
                actor_id=actor_id,
                reason="Replay the concurrent governed pack release",
                now=NOW + timedelta(minutes=9),
            ),
        )
        assert concurrent_digests[0] == concurrent_digests[1]

        rollback_release = SignedFederationRelease(
            statement=second_statement,
            signature=(
                base64.urlsafe_b64encode(
                    second_key.sign(release_signature_material(second_statement))
                )
                .decode("ascii")
                .rstrip("=")
            ),
        )
        with pytest.raises(FederationError, match="release_rollback_detected"):
            await service.publish_release(
                rollback_release,
                actor_id=actor_id,
                reason="Prove a release rollback is rejected",
                now=NOW + timedelta(minutes=9),
            )

        pending = await seed_publication(
            database_url,
            now=NOW + timedelta(minutes=10),
            ids=DeterministicIdGenerator(UUID("adf3a23f-2bbc-4612-9c1d-35f8fec40b66")),
            suffix="federation-quarantine",
            manifest_digest="c" * 64,
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
            now=NOW + timedelta(minutes=11),
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
                == 9
            )
            assert (
                await connection.fetchval(
                    "SELECT count(*) FROM federation_releases WHERE maintainer_id = $1",
                    active.maintainer_id,
                )
                == 2
            )
            first_row = await connection.fetchrow(
                """
                SELECT statement_json, statement_digest, signature, receipt_published_at
                FROM federation_releases
                WHERE maintainer_id = $1
                ORDER BY receipt_published_at
                LIMIT 1
                """,
                active.maintainer_id,
            )
            assert first_row is not None
            assert str(first_row["statement_digest"]) == first_digest
            assert str(first_row["signature"]) == release.signature
            assert first_row["receipt_published_at"] == receipt.published_at
            stored_statement = first_row["statement_json"]
            if isinstance(stored_statement, str):
                stored_statement = json.loads(stored_statement)
            assert stored_statement == statement.model_dump(mode="json")
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
            with pytest.raises(asyncpg.CheckViolationError, match="append_only"):
                await connection.execute(
                    "UPDATE federation_releases SET public_url = $1 WHERE maintainer_id = $2",
                    "https://example.test/tampered",
                    active.maintainer_id,
                )
            with pytest.raises(asyncpg.CheckViolationError, match="append_only"):
                await connection.execute(
                    "DELETE FROM federation_releases WHERE maintainer_id = $1",
                    active.maintainer_id,
                )
        finally:
            await connection.close()
    finally:
        await engine.dispose()


async def run_multi_scope_lifecycle(database_url: str) -> None:
    await reset_trust_tables(database_url)
    actor_id = uuid4()
    scopes = (
        FederationScope(
            github_account_id=280184755,
            github_login="aarolabs",
            repository_id=1339461317,
            repository="RujitRaval/opennosh",
            pack_id="global-core",
        ),
        FederationScope(
            github_account_id=280184756,
            github_login="second-maintainer",
            repository_id=1339461318,
            repository="OpenNutrition/regional-produce",
            pack_id="regional-produce",
        ),
        FederationScope(
            github_account_id=280184756,
            github_login="second-maintainer",
            repository_id=1339461319,
            repository="OpenNutrition/heritage-grains",
            pack_id="heritage-grains",
        ),
    )
    connection = await asyncpg.connect(asyncpg_dsn(database_url))
    try:
        await connection.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, 'hash')",
            actor_id,
            f"federation-multi-scope-{actor_id}@example.test",
        )
        await connection.executemany(
            """
            INSERT INTO governance_role_assignments (
                pack_id, actor_id, role, granted_by_actor_id,
                grant_reason, granted_at
            ) VALUES ($1, $2, 'steward', $2, $3, $4)
            """,
            [
                (
                    scope.pack_id,
                    actor_id,
                    "Bootstrap the multi-scope federation steward",
                    NOW - timedelta(minutes=1),
                )
                for scope in scopes
            ],
        )
    finally:
        await connection.close()

    verifier = VerifiedInstallation()
    engine = create_async_engine(database_url)
    service = FederationService(
        async_sessionmaker(engine, expire_on_commit=False),
        allowed_scopes=scopes,
        allowed_public_origin="https://opennosh.example",
        installation_verifier=verifier,
    )
    try:
        unconfigured = scopes[0].model_copy(
            update={
                "repository_id": 999999999,
                "repository": "Unreviewed/arbitrary",
                "pack_id": "unreviewed-pack",
            }
        )
        with pytest.raises(FederationError, match="federation_scope_not_invited"):
            await service.invite(
                unconfigured,
                inviter_actor_id=actor_id,
                expires_at=NOW + timedelta(hours=1),
                now=NOW,
            )

        raced = await asyncio.gather(
            service.invite(
                scopes[0],
                inviter_actor_id=actor_id,
                expires_at=NOW + timedelta(hours=1),
                now=NOW,
            ),
            service.invite(
                scopes[0],
                inviter_actor_id=actor_id,
                expires_at=NOW + timedelta(hours=1),
                now=NOW,
            ),
            return_exceptions=True,
        )
        invitations = [result for result in raced if isinstance(result, InvitationSecret)]
        errors = [result for result in raced if isinstance(result, FederationError)]
        assert len(invitations) == 1
        assert [error.code for error in errors] == ["federation_invitation_limit_reached"]

        other_invitations = await asyncio.gather(
            *(
                service.invite(
                    scope,
                    inviter_actor_id=actor_id,
                    expires_at=NOW + timedelta(hours=1),
                    now=NOW,
                )
                for scope in scopes[1:]
            )
        )
        invitations.extend(other_invitations)

        with pytest.raises(FederationError, match="invitation_identity_mismatch"):
            await service.verify(
                token=invitations[0].token,
                scope=scopes[1],
                installation_id=157058060,
                key_id="multi-scope-cross-use",
                public_key=encode_public_key(
                    Ed25519PrivateKey.from_private_bytes(b"h" * 32).public_key()
                ),
                public_key_fingerprint=hashlib.sha256(
                    Ed25519PrivateKey.from_private_bytes(b"h" * 32)
                    .public_key()
                    .public_bytes_raw()
                ).hexdigest(),
                now=NOW + timedelta(minutes=1),
            )

        statuses = []
        for index, (scope, invitation) in enumerate(zip(scopes, invitations, strict=True)):
            key = Ed25519PrivateKey.from_private_bytes(bytes([104 + index]) * 32)
            encoded_key = encode_public_key(key.public_key())
            fingerprint = hashlib.sha256(key.public_key().public_bytes_raw()).hexdigest()
            status = await service.verify(
                token=invitation.token,
                scope=scope,
                installation_id=157058059 + index,
                key_id=f"multi-scope-2026-{index + 1:02d}",
                public_key=encoded_key,
                public_key_fingerprint=fingerprint,
                now=NOW + timedelta(minutes=1),
            )
            statuses.append(
                await service.activate(
                    status.maintainer_id,
                    actor_id=actor_id,
                    reason="Activate one reviewed multi-scope maintainer",
                    now=NOW + timedelta(minutes=2),
                )
            )
        assert [status.state for status in statuses] == ["active", "active", "active"]
        assert verifier.calls == 3

        connection = await asyncpg.connect(asyncpg_dsn(database_url))
        try:
            assert await connection.fetchval("SELECT count(*) FROM federation_invitations") == 3
            assert await connection.fetchval("SELECT count(*) FROM federation_maintainers") == 3
            assert (
                await connection.fetchval(
                    "SELECT count(*) FROM federation_maintainers WHERE state = 'active'"
                )
                == 3
            )
            assert (
                await connection.fetchval(
                    "SELECT count(DISTINCT pack_id) FROM federation_invitations"
                )
                == 3
            )
        finally:
            await connection.close()
    finally:
        await engine.dispose()


def _global_core_pack_bytes() -> tuple[bytes, str]:
    packs_root = ROOT / "packs"
    source_directory = packs_root / "indian-staples-north"
    prepared = prepare_food_pack(source_directory)
    assert prepared.pack_version is not None
    source = io.BytesIO(_pack_archive(packs_root, source_directory))
    output = io.BytesIO()
    with zipfile.ZipFile(source) as incoming, zipfile.ZipFile(
        output,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as outgoing:
        for info in incoming.infolist():
            payload = incoming.read(info)
            if info.filename == "pack.yaml":
                payload = payload.replace(
                    b"id: indian-staples-north",
                    b"id: global-core",
                    1,
                )
            outgoing.writestr(info.filename, payload)
    return output.getvalue(), prepared.pack_version


async def run_verified_projection(database_url: str) -> None:
    await reset_trust_tables(database_url)
    pack_bytes, pack_version = _global_core_pack_bytes()
    release_version = "1.2.3.4"
    manifest_signing_key = Ed25519PrivateKey.from_private_bytes(b"m" * 32)
    descriptor = artifact_descriptor(
        f"packs/v1/{hashlib.sha256(pack_bytes).hexdigest()}.zip",
        pack_bytes,
        "application/zip",
    )
    manifest = PublicReadReleaseManifest(
        release_version=release_version,
        published_at=NOW,
        publication_receipt_key=(
            "receipts/v1/00000000-0000-0000-0000-000000000001.json"
        ),
        packs=(
            PublicPackArtifact(
                pack_id="global-core",
                pack_version=pack_version,
                download=descriptor,
            ),
        ),
    )
    manifest_bytes = sign_envelope(
        manifest.model_dump(mode="json"),
        key_id="projection-manifest-v1",
        private_key=manifest_signing_key,
    )
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    seeded, envelope_json, receipt_digest = await _published_release(
        database_url,
        manifest_digest=manifest_digest,
        release_version=release_version,
        suffix="verified-projection",
    )
    actor_id = uuid4()
    scope = FederationScope(
        github_account_id=280184755,
        github_login="aarolabs",
        repository_id=1339461317,
        repository=FORGE_TARGET.removeprefix("github:"),
        pack_id="global-core",
    )
    connection = await asyncpg.connect(asyncpg_dsn(database_url))
    try:
        await connection.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, 'hash')",
            actor_id,
            f"federation-projection-{actor_id}@example.test",
        )
        await connection.execute(
            """
            INSERT INTO governance_role_assignments (
                pack_id, actor_id, role, granted_by_actor_id,
                grant_reason, granted_at
            ) VALUES ('global-core', $1, 'steward', $1, $2, $3)
            """,
            actor_id,
            "Bootstrap the verified projection test steward",
            NOW - timedelta(minutes=1),
        )
    finally:
        await connection.close()

    role_key = Ed25519PrivateKey.from_private_bytes(b"r" * 32)
    engine = create_async_engine(database_url)
    service = FederationService(
        async_sessionmaker(engine, expire_on_commit=False),
        allowed_scopes=(scope,),
        allowed_public_origin="https://opennosh.example",
        installation_verifier=VerifiedInstallation(),
        manifest_keys=ManifestKeyRing.from_config(
            f"projection-manifest-v1:{public_key_text(manifest_signing_key)}"
        ),
        ingestion_enabled=True,
        projection_enabled=True,
    )
    try:
        invitation = await service.invite(
            scope,
            inviter_actor_id=actor_id,
            expires_at=NOW + timedelta(hours=1),
            now=NOW,
        )
        verified_maintainer = await service.verify(
            token=invitation.token,
            scope=scope,
            installation_id=157058059,
            key_id="projection-role-v1",
            public_key=encode_public_key(role_key.public_key()),
            public_key_fingerprint=hashlib.sha256(
                role_key.public_key().public_bytes_raw()
            ).hexdigest(),
            now=NOW + timedelta(minutes=1),
        )
        await service.activate(
            verified_maintainer.maintainer_id,
            actor_id=actor_id,
            reason="Activate the verified projection test scope",
            now=NOW + timedelta(minutes=2),
        )
        receipt = SignedPublicationReceipt.model_validate(envelope_json).receipt
        statement = FederationReleaseStatement(
            maintainer_id=verified_maintainer.maintainer_id,
            repository_id=scope.repository_id,
            repository=scope.repository,
            pack_id=scope.pack_id,
            publication_id=seeded.publication_id,
            release_version=release_version,
            manifest_digest=manifest_digest,
            receipt_digest=receipt_digest,
            public_url=(
                "https://opennosh.example/api/v1/public/releases/"
                f"{release_version}/manifest"
            ),
            issued_at=receipt.published_at + timedelta(minutes=1),
            key_id="projection-role-v1",
        )
        signed = SignedFederationRelease(
            statement=statement,
            signature=(
                base64.urlsafe_b64encode(
                    role_key.sign(release_signature_material(statement))
                )
                .decode("ascii")
                .rstrip("=")
            ),
        )
        statement_digest = release_statement_digest(statement)
        connection = await asyncpg.connect(asyncpg_dsn(database_url))
        try:
            role_key_id = await connection.fetchval(
                "SELECT id FROM federation_role_keys WHERE maintainer_id = $1",
                verified_maintainer.maintainer_id,
            )
            accepted_event_id = await connection.fetchval(
                "SELECT id FROM accepted_events WHERE publication_intent_id = $1",
                seeded.publication_id,
            )
            await connection.execute(
                """
                INSERT INTO federation_releases (
                    maintainer_id, role_key_id, accepted_event_id, repository_id,
                    repository, pack_id, publication_id, release_version,
                    statement_json, statement_digest, manifest_digest, receipt_digest,
                    public_url, key_id, signature, issued_at, receipt_published_at,
                    verified_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8,
                    $9::jsonb, $10, $11, $12, $13, $14, $15, $16, $17, $18
                )
                """,
                verified_maintainer.maintainer_id,
                role_key_id,
                accepted_event_id,
                scope.repository_id,
                scope.repository,
                scope.pack_id,
                seeded.publication_id,
                release_version,
                json.dumps(statement.model_dump(mode="json")),
                statement_digest,
                manifest_digest,
                receipt_digest,
                statement.public_url,
                statement.key_id,
                signed.signature,
                statement.issued_at,
                receipt.published_at,
                statement.issued_at,
            )
        finally:
            await connection.close()
        artifact_status = await service.verify_release_artifacts(
            statement_digest,
            manifest_bytes=manifest_bytes,
            pack_bytes=pack_bytes,
            actor_id=actor_id,
            reason="Verify signed release content for projection",
            now=statement.issued_at + timedelta(minutes=1),
        )
        assert artifact_status.state == "verified"
        replay_status = await service.verify_release_artifacts(
            statement_digest,
            manifest_bytes=manifest_bytes,
            pack_bytes=pack_bytes,
            actor_id=actor_id,
            reason="Replay exact verified release content",
            now=statement.issued_at + timedelta(minutes=2),
        )
        assert replay_status == artifact_status
        projection = await service.build_projection(
            actor_id=actor_id,
            reason="Activate the complete verified release set",
            now=statement.issued_at + timedelta(minutes=3),
        )
        projection_replay = await service.build_projection(
            actor_id=actor_id,
            reason="Replay the same complete release set",
            now=statement.issued_at + timedelta(minutes=4),
        )
        assert projection_replay.checkpoint_id == projection.checkpoint_id
        assert projection_replay.activated_at == projection.activated_at

        with TestClient(
            create_app(
                Settings(
                    database_url=database_url,
                    app_environment="test",
                    federation_search_enabled=True,
                    _env_file=None,
                )
            )
        ) as client:
            search = client.get(
                "/api/v1/foods/search",
                params={
                    "q": "samosa",
                    "source": "federation",
                    "pack": "global-core",
                },
            )
            assert search.status_code == 200
            search_payload = search.json()
            assert search_payload["release_set"] == {
                "enabled": True,
                "checkpoint_id": str(projection.checkpoint_id),
                "digest": projection.release_set_digest,
                "selected_pack_ids": ["global-core"],
                "stale": False,
            }
            assert len(search_payload["items"]) == 1
            item = search_payload["items"][0]
            assert item["source"] == "federation"
            assert item["source_id"].endswith(":north-indian-samosa")
            assert item["attribution"]["pack_id"] == "global-core"
            assert item["attribution"]["pack_version"] == pack_version
            assert item["attribution"]["release_version"] == release_version
            assert item["attribution"]["release_digest"] == statement_digest
            assert item["attribution"]["source_license"] == "CC0-1.0"
            assert item["variant_id"].startswith("federation:")
            assert item["variant_count"] == 1
            assert item["conflict"] is False

            detail = client.get(f"/api/v1/foods/federation/{item['source_id']}")
            assert detail.status_code == 200
            assert detail.json()["nutrients"]["basis"] == "per_100g"
            assert detail.json()["attribution"]["release_digest"] == statement_digest

            ordinary_search = client.get("/api/v1/foods/search", params={"q": "samosa"})
            assert ordinary_search.status_code == 200
            assert ordinary_search.json()["release_set"]["enabled"] is False
            assert all(
                row["source"] != "federation"
                for row in ordinary_search.json()["items"]
            )

            cursor_page = client.get(
                "/api/v1/foods/search",
                params={
                    "q": "rice",
                    "source": "federation",
                    "pack": "global-core",
                    "limit": 1,
                },
            )
            assert cursor_page.status_code == 200
            retained_cursor = cursor_page.json()["next_cursor"]
            assert retained_cursor is not None

        await service.quarantine_release(
            statement_digest,
            actor_id=actor_id,
            reason="Exercise stale search fallback after quarantine",
            now=datetime.now(UTC),
        )

        with TestClient(
            create_app(
                Settings(
                    database_url=database_url,
                    app_environment="test",
                    federation_search_enabled=True,
                    _env_file=None,
                )
            )
        ) as client:
            retained_page = client.get(
                "/api/v1/foods/search",
                params={
                    "q": "rice",
                    "source": "federation",
                    "pack": "global-core",
                    "limit": 1,
                    "cursor": retained_cursor,
                },
            )
            assert retained_page.status_code == 200
            assert retained_page.json()["items"]
            assert retained_page.json()["release_set"]["stale"] is True

            safe_first_page = client.get(
                "/api/v1/foods/search",
                params={
                    "q": "samosa",
                    "source": "federation",
                    "pack": "global-core",
                },
            )
            assert safe_first_page.status_code == 200
            assert safe_first_page.json()["items"] == []
            assert safe_first_page.json()["release_set"] == {
                "enabled": True,
                "checkpoint_id": str(projection.checkpoint_id),
                "digest": projection.release_set_digest,
                "selected_pack_ids": ["global-core"],
                "stale": True,
            }

        connection = await asyncpg.connect(asyncpg_dsn(database_url))
        try:
            assert await connection.fetchval(
                "SELECT count(*) FROM federation_verified_releases"
            ) == 1
            assert await connection.fetchval(
                "SELECT count(*) FROM federation_projection_checkpoints"
            ) == 1
            assert await connection.fetchval(
                "SELECT count(*) FROM federation_projection_activations"
            ) == 1
            assert await connection.fetchval(
                "SELECT count(*) FROM federation_projection_foods"
            ) == artifact_status.record_count
            with pytest.raises(asyncpg.CheckViolationError, match="append_only"):
                await connection.execute(
                    "DELETE FROM federation_projection_checkpoints WHERE id = $1",
                    projection.checkpoint_id,
                )
        finally:
            await connection.close()

        with pytest.raises(FederationError, match="release_pack_artifact_mismatch"):
            await service.verify_release_artifacts(
                statement_digest,
                manifest_bytes=manifest_bytes,
                pack_bytes=pack_bytes + b"tampered",
                actor_id=actor_id,
                reason="Reject and quarantine a tampered verified candidate",
                now=statement.issued_at + timedelta(minutes=5),
            )
        quarantined = await service.quarantine_release(
            statement_digest,
            actor_id=actor_id,
            reason="Replay quarantine without duplicating its terminal fact",
            now=statement.issued_at + timedelta(minutes=6),
        )
        assert quarantined.state == "quarantined"
        with pytest.raises(FederationError, match="release_set_empty"):
            await service.build_projection(
                actor_id=actor_id,
                reason="Prove quarantine cannot replace the last active checkpoint",
                now=statement.issued_at + timedelta(minutes=7),
            )
        connection = await asyncpg.connect(asyncpg_dsn(database_url))
        try:
            assert await connection.fetchval(
                "SELECT checkpoint_id FROM federation_projection_activations "
                "ORDER BY activated_at DESC LIMIT 1"
            ) == projection.checkpoint_id
            assert await connection.fetchval(
                "SELECT count(*) FROM federation_release_status_events"
            ) == 2
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
    config = migration_config(INTEGRATION_DATABASE_URL)
    alembic_command.upgrade(config, "head")
    asyncio.run(run_lifecycle(INTEGRATION_DATABASE_URL))
    with pytest.raises(DBAPIError, match="refuses to discard immutable federation release rows"):
        alembic_command.downgrade(config, "20260901_0024")


@pytest.mark.skipif(
    INTEGRATION_DATABASE_URL is None,
    reason="INTEGRATION_DATABASE_URL is required for PostgreSQL integration tests",
)
def test_reviewed_scopes_serialize_independently_and_downgrade_refuses_collapse() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    config = migration_config(INTEGRATION_DATABASE_URL)
    alembic_command.upgrade(config, "head")
    asyncio.run(run_multi_scope_lifecycle(INTEGRATION_DATABASE_URL))
    with pytest.raises(DBAPIError, match="refuses to collapse multiple federation invitation"):
        alembic_command.downgrade(config, "20260902_0025")


@pytest.mark.skipif(
    INTEGRATION_DATABASE_URL is None,
    reason="INTEGRATION_DATABASE_URL is required for PostgreSQL integration tests",
)
def test_verified_artifact_projection_is_atomic_append_only_and_quarantine_safe() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    config = migration_config(INTEGRATION_DATABASE_URL)
    alembic_command.upgrade(config, "head")
    asyncio.run(run_verified_projection(INTEGRATION_DATABASE_URL))
    try:
        with pytest.raises(
            DBAPIError,
            match="refusing downgrade while federation search identities exist",
        ):
            alembic_command.downgrade(config, "20260902_0026")
    finally:
        asyncio.run(reset_trust_tables(INTEGRATION_DATABASE_URL))
