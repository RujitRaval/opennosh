from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID

import asyncpg  # type: ignore[import-untyped]
import pytest
from alembic import command as alembic_command
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from opennosh_api.jobs import JobLane, JobMessage
from opennosh_api.jobs.worker import asyncpg_dsn
from opennosh_api.publication.executor import PublicationEffectExecutor
from opennosh_api.publication.orchestrator import PublicationOrchestrator
from opennosh_api.publication.receipts import (
    Ed25519ReceiptSigner,
    MemoryPublicationReceiptStore,
    PublicationReceiptDraft,
    PublicationReceiptKeyRing,
    ReceiptEventType,
    canonical_signed_receipt_bytes,
    receipt_draft_from_snapshot,
    receipt_object_key,
    signed_receipt_digest,
)
from opennosh_api.publication.reconciliation import PublicationReceiptReconciler
from opennosh_api.publication.repository import PostgresPublicationRepository

from api.tests.test_migrations import migration_config
from api.tests.workflow_testkit import (
    DeterministicClock,
    DeterministicIdGenerator,
    PersistentExternalState,
    publication_adapter_registry,
    reset_trust_tables,
    seed_publication,
)

INTEGRATION_DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL")
NOW = datetime(2026, 8, 26, 14, tzinfo=UTC)
NAMESPACE = UUID("0b44078f-b566-40ef-9717-a83b5f1a9a3a")
PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(b"z" * 32)
SIGNER = Ed25519ReceiptSigner(
    key_id="reconciliation-test-2026",
    publisher_identity="opennosh:reconciliation-test",
    private_key=PRIVATE_KEY,
)
KEY_RING = PublicationReceiptKeyRing({"reconciliation-test-2026": PRIVATE_KEY.public_key()})


async def _candidate(database_url: str):
    clock = DeterministicClock(NOW)
    ids = DeterministicIdGenerator(NAMESPACE)
    seeded = await seed_publication(
        database_url,
        now=clock(),
        ids=ids,
        suffix="receipt-recovery",
    )
    pool = await asyncpg.create_pool(dsn=asyncpg_dsn(database_url), min_size=1, max_size=3)
    assert pool is not None
    state = PersistentExternalState()
    orchestrator = PublicationOrchestrator(
        repository=PostgresPublicationRepository(pool),
        executor=PublicationEffectExecutor(publication_adapter_registry(state, clock)),
        owner="receipt-preparation",
        clock=clock,
    )
    message = JobMessage(
        lane=JobLane.PUBLICATION,
        job_type="publication.wake",
        subject_id=seeded.publication_id,
        idempotency_key="receipt-recovery-message",
    )
    for _ in range(7):
        await orchestrator.process(message, queue_job_id=seeded.queue_job_id)
    snapshot = await PostgresPublicationRepository(pool).load_or_initialize(seeded.publication_id)
    envelope = SIGNER.sign(receipt_draft_from_snapshot(snapshot))
    return pool, seeded, envelope


async def _put(store, envelope) -> None:
    payload = canonical_signed_receipt_bytes(envelope)
    await store.put_immutable(
        receipt_object_key(envelope.receipt.publication_id),
        payload,
        expected_digest=signed_receipt_digest(envelope),
    )


async def _run_reconstruction_scenarios(database_url: str) -> None:
    await reset_trust_tables(database_url)
    pool, seeded, envelope = await _candidate(database_url)
    registry = MemoryPublicationReceiptStore(destination="urn:opennosh:registry:receipt")
    artifacts = MemoryPublicationReceiptStore(destination="urn:opennosh:durability:receipt")
    reconciler = PublicationReceiptReconciler(
        pool,
        registry=registry,
        artifacts=artifacts,
        key_ring=KEY_RING,
    )
    try:
        await _put(artifacts, envelope)
        missing = await reconciler.run(now=NOW)
        assert [issue.code for issue in missing.pending] == [
            "receipt_destination_acknowledgement_missing"
        ]
        assert missing.reconstructed == 0
        assert await pool.fetchval("SELECT count(*) FROM publication_receipts") == 0

        await _put(registry, envelope)
        object_key = receipt_object_key(envelope.receipt.publication_id)
        registry_observation = await registry.observe(object_key)
        assert registry_observation is not None
        receipt_digest = signed_receipt_digest(envelope)
        async with pool.acquire() as connection:
            async with connection.transaction():
                await connection.executemany(
                    """
                    INSERT INTO publication_durable_acknowledgements (
                        publication_intent_id, acknowledgement_kind, destination,
                        content_digest, external_reference, context_json, verified_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
                    """,
                    (
                        (
                            seeded.publication_id,
                            "sign_receipt",
                            "urn:opennosh:receipt:signer",
                            receipt_digest,
                            f"key:{envelope.signature_key_id}",
                            json.dumps(
                                {
                                    "adapter_identity": envelope.receipt.publisher_adapter_identity,
                                    "adapter_version": envelope.receipt.publisher_adapter_version,
                                    "signed_receipt": envelope.model_dump(mode="json"),
                                }
                            ),
                            NOW + timedelta(seconds=1),
                        ),
                        (
                            seeded.publication_id,
                            "publish_receipt_registry",
                            registry.destination,
                            receipt_digest,
                            registry_observation.external_reference,
                            json.dumps(
                                {
                                    "adapter_identity": registry.identity,
                                    "adapter_version": registry.version,
                                }
                            ),
                            NOW + timedelta(seconds=2),
                        ),
                    ),
                )
                await connection.execute(
                    """
                    UPDATE publication_steps
                    SET state = 'verified',
                        verified_at = CASE step_name
                            WHEN 'sign_receipt' THEN $2
                            ELSE $3
                        END
                    WHERE publication_intent_id = $1
                      AND step_name IN ('sign_receipt', 'publish_receipt_registry')
                    """,
                    seeded.publication_id,
                    NOW + timedelta(seconds=1),
                    NOW + timedelta(seconds=2),
                )
        concurrent = await asyncio.gather(
            reconciler.run(now=NOW + timedelta(seconds=3)),
            reconciler.run(now=NOW + timedelta(seconds=3)),
        )
        assert sum(result.reconstructed for result in concurrent) == 1
        assert sum(result.already_current for result in concurrent) == 1
        state, steps, acknowledgements, receipts, accepted = await pool.fetchrow(
            """
            SELECT intent.state,
                   (SELECT count(*) FROM publication_steps
                    WHERE publication_intent_id = intent.id),
                   (SELECT count(*) FROM publication_durable_acknowledgements
                    WHERE publication_intent_id = intent.id),
                   (SELECT count(*) FROM publication_receipts
                    WHERE publication_intent_id = intent.id),
                   (SELECT count(*) FROM accepted_events
                    WHERE publication_intent_id = intent.id)
            FROM publication_intents AS intent
            WHERE intent.id = $1
            """,
            seeded.publication_id,
        )
        assert (state, steps, acknowledgements, receipts, accepted) == (
            "published",
            10,
            10,
            1,
            1,
        )
        with pytest.raises(asyncpg.RaiseError, match="append-only"):
            await pool.execute(
                "UPDATE publication_receipts SET pack_id = 'changed' WHERE publication_id = $1",
                seeded.publication_id,
            )
        with pytest.raises(asyncpg.RaiseError, match="append-only"):
            await pool.execute(
                "DELETE FROM publication_receipts WHERE publication_id = $1",
                seeded.publication_id,
            )

        replay = await reconciler.run(now=NOW + timedelta(seconds=2))
        assert replay.already_current == 1
        assert replay.reconstructed == 0

        await pool.execute(
            "DELETE FROM accepted_events WHERE publication_intent_id = $1",
            seeded.publication_id,
        )
        repaired = await reconciler.run(now=NOW + timedelta(seconds=3))
        assert repaired.reconstructed == 1
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM accepted_events WHERE publication_intent_id = $1",
                seeded.publication_id,
            )
            == 1
        )

        prior_digest = signed_receipt_digest(envelope)
        correction_draft = envelope.receipt.model_dump(
            mode="python",
            exclude={
                "publisher_identity",
                "publisher_adapter_identity",
                "publisher_adapter_version",
            },
        )
        correction_steps = list(correction_draft["verified_steps"])
        correction_steps[0]["external_reference"] = "c" * 40
        for proof in correction_steps:
            proof["verified_at"] = NOW + timedelta(minutes=1)
        correction_draft.update(
            {
                "publication_id": UUID("11111111-1111-4111-8111-111111111112"),
                "event_type": ReceiptEventType.CORRECTION,
                "prior_receipt_digest": prior_digest,
                "idempotency_key_hash": "8" * 64,
                "published_at": NOW + timedelta(minutes=1),
                "merged_commit": "c" * 40,
                "verified_steps": correction_steps,
            }
        )
        correction = SIGNER.sign(PublicationReceiptDraft.model_validate(correction_draft))
        await _put(registry, correction)
        await _put(artifacts, correction)
        corrected = await reconciler.run(now=NOW + timedelta(minutes=1, seconds=1))
        assert corrected.reconstructed == 1

        revocation_draft = correction.receipt.model_dump(
            mode="python",
            exclude={
                "publisher_identity",
                "publisher_adapter_identity",
                "publisher_adapter_version",
            },
        )
        revocation_steps = list(revocation_draft["verified_steps"])
        revocation_steps[0]["external_reference"] = "d" * 40
        for proof in revocation_steps:
            proof["verified_at"] = NOW + timedelta(minutes=2)
        revocation_draft.update(
            {
                "publication_id": UUID("11111111-1111-4111-8111-111111111113"),
                "event_type": ReceiptEventType.REVOCATION,
                "prior_receipt_digest": signed_receipt_digest(correction),
                "idempotency_key_hash": "7" * 64,
                "published_at": NOW + timedelta(minutes=2),
                "merged_commit": "d" * 40,
                "verified_steps": revocation_steps,
            }
        )
        revocation = SIGNER.sign(PublicationReceiptDraft.model_validate(revocation_draft))
        await _put(registry, revocation)
        await _put(artifacts, revocation)
        revoked = await reconciler.run(now=NOW + timedelta(minutes=2, seconds=1))
        assert revoked.reconstructed == 1
        assert await pool.fetchval("SELECT count(*) FROM publication_receipts") == 3
        assert await pool.fetchval("SELECT count(*) FROM accepted_events") == 3
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM accepted_events WHERE event_type = 'record.revoked'"
            )
            == 1
        )
    finally:
        await reset_trust_tables(database_url)
        await pool.close()


async def _run_tamper_scenario(database_url: str) -> None:
    await reset_trust_tables(database_url)
    pool, _, envelope = await _candidate(database_url)
    registry = MemoryPublicationReceiptStore(destination="urn:opennosh:registry:receipt")
    artifacts = MemoryPublicationReceiptStore(destination="urn:opennosh:durability:receipt")
    try:
        await _put(registry, envelope)
        await _put(artifacts, envelope)
        key = receipt_object_key(envelope.receipt.publication_id)
        artifacts.objects[key] = artifacts.objects[key].replace(
            b'"record_id":"record-receipt-recovery"',
            b'"record_id":"record-receipt-tampered"',
        )
        result = await PublicationReceiptReconciler(
            pool,
            registry=registry,
            artifacts=artifacts,
            key_ring=KEY_RING,
        ).run(now=NOW)
        assert [issue.code for issue in result.quarantined] == [
            "receipt_destination_digest_conflict"
        ]
        assert await pool.fetchval("SELECT count(*) FROM publication_receipts") == 0
    finally:
        await reset_trust_tables(database_url)
        await pool.close()


@pytest.mark.skipif(
    INTEGRATION_DATABASE_URL is None,
    reason="INTEGRATION_DATABASE_URL is required for PostgreSQL integration tests",
)
def test_receipts_reconstruct_after_prepublication_restore_and_replay() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    alembic_command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_run_reconstruction_scenarios(INTEGRATION_DATABASE_URL))


@pytest.mark.skipif(
    INTEGRATION_DATABASE_URL is None,
    reason="INTEGRATION_DATABASE_URL is required for PostgreSQL integration tests",
)
def test_reconciliation_quarantines_cross_destination_tampering() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    alembic_command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_run_tamper_scenario(INTEGRATION_DATABASE_URL))
