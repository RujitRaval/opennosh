from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]
import pytest
from alembic import command as alembic_command
from opennosh_api.jobs.worker import asyncpg_dsn
from opennosh_api.missions.owner_workflow import (
    collect_owner_mission_readiness,
    run_owner_mission,
)
from opennosh_api.settings import Settings

from api.tests.test_migrations import migration_config

INTEGRATION_DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL")
NOW = datetime(2026, 9, 12, 12, tzinfo=UTC)


async def _exercise_owner_workflow(database_url: str) -> None:
    owner_id = uuid4()
    authorization_id = uuid4()
    draft_id = uuid4()
    decision_id = uuid4()
    intent_id = uuid4()
    publication_id = uuid4()
    accepted_id = uuid4()
    record_id = f"owner-mission-food-{draft_id.hex[:12]}"
    pack_id = "indian-sweets"
    commit_sha = hashlib.sha256(draft_id.bytes).hexdigest()[:40]
    payload_digest = hashlib.sha256(decision_id.bytes).hexdigest()
    receipt_digest = hashlib.sha256(publication_id.bytes).hexdigest()
    repository = "github:RujitRaval/opennosh"
    connection = await asyncpg.connect(asyncpg_dsn(database_url))
    try:
        await connection.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, 'hash')",
            owner_id,
            f"owner-workflow-{draft_id.hex}@example.test",
        )
        await connection.execute(
            "INSERT INTO governance_owner_authorizations "
            "(id, pack_id, actor_id, role, granted_by_actor_id, grant_reason, granted_at) "
            "VALUES ($1, $2, $3, 'owner', $3, 'owner workflow test', $4)",
            authorization_id,
            pack_id,
            owner_id,
            NOW,
        )
        await connection.execute(
            "INSERT INTO contribution_drafts "
            "(id, user_id, draft_version, review_state, fields_json) "
            "VALUES ($1, $2, 1, 'publication_pending', $3::jsonb)",
            draft_id,
            owner_id,
            json.dumps({"pack_id": pack_id}),
        )
        await connection.execute(
            """
            INSERT INTO governance_decisions (
                id, source_draft_id, source_draft_version, pack_id, record_id,
                contributor_actor_id, deciding_actor_id, approval_mode,
                owner_authorization_id, outcome, reason, approved_payload_digest,
                approved_changes_json, expected_base_commit, required_checks_json,
                forge_target, decided_at
            ) VALUES (
                $1, $2, 1, $3, $4, $5, $5, 'owner', $6, 'approved',
                'Owner-approved integration record.', $7, '{}'::jsonb, $8,
                '[]'::jsonb, $9, $10
            )
            """,
            decision_id,
            draft_id,
            pack_id,
            record_id,
            owner_id,
            authorization_id,
            payload_digest,
            commit_sha,
            repository,
            NOW,
        )
        await connection.execute(
            """
            INSERT INTO publication_intents (
                id, source_draft_id, source_draft_version, reviewed_decision_id,
                approving_actor_id, state, pack_id, record_id, approved_payload_digest,
                expected_base_commit, required_checks_json, forge_target,
                idempotency_key_hash, event_type, evidence_manifest_digests_json,
                evidence_acknowledgements_json, published_at
            ) VALUES (
                $1, $2, 1, $3, $4, 'published', $5, $6, $7, $8,
                '[]'::jsonb, $9, $10, 'publication', $11::jsonb, $12::jsonb, $13
            )
            """,
            intent_id,
            draft_id,
            decision_id,
            owner_id,
            pack_id,
            record_id,
            payload_digest,
            commit_sha,
            repository,
            hashlib.sha256(intent_id.bytes).hexdigest(),
            json.dumps([hashlib.sha256(draft_id.bytes).hexdigest()]),
            json.dumps([{"kind": "immutable", "content_digest": payload_digest}]),
            NOW,
        )
        await connection.execute(
            """
            INSERT INTO publication_receipts (
                id, publication_intent_id, publication_id, schema_version,
                receipt_digest, event_type, pack_id, record_id, envelope_json,
                signature_key_id, registry_reference, artifact_reference,
                published_at, reconciled_at
            ) VALUES (
                $1, $2, $3, '1.0', $4, 'publication', $5, $6, $7::jsonb,
                'owner-mission-test-key', 'registry:test', 'artifact:test', $8, $8
            )
            """,
            uuid4(),
            intent_id,
            publication_id,
            receipt_digest,
            pack_id,
            record_id,
            json.dumps(
                {
                    "receipt": {
                        "schema_version": "1.0",
                        "publication_id": str(publication_id),
                        "event_type": "publication",
                        "prior_receipt_digest": None,
                        "pack_id": pack_id,
                        "record_id": record_id,
                        "merged_commit": commit_sha,
                        "published_at": NOW.isoformat(),
                        "verified_steps": [
                            {
                                "step": "commit_record",
                                "destination": repository,
                                "external_reference": commit_sha,
                            }
                        ],
                    },
                    "signature_key_id": "owner-mission-test-key",
                }
            ),
            NOW,
        )
        await connection.execute(
            """
            INSERT INTO accepted_events (
                id, publication_intent_id, repository, commit_sha, pack_id,
                record_id, event_type, receipt_digest, published_at
            ) VALUES ($1, $2, $3, $4, $5, $6, 'record.published', $7, $8)
            """,
            accepted_id,
            intent_id,
            repository,
            commit_sha,
            pack_id,
            record_id,
            receipt_digest,
            NOW,
        )
    finally:
        await connection.close()

    settings = Settings(
        app_environment="test",
        database_url=database_url,
        database_capacity_manifest_path="config/database-capacity.local.v1.json",
    )
    readiness = await collect_owner_mission_readiness(
        settings,
        actor_id=owner_id,
        mission_key=f"integration-{draft_id.hex}",
        pack_id=pack_id,
        record_ids=(record_id,),
    )
    report = await run_owner_mission(
        settings,
        actor_id=owner_id,
        mission_key=f"integration-{draft_id.hex}",
        pack_id=pack_id,
        record_ids=(record_id,),
        approved_readiness_digest=readiness.readiness_digest,
    )
    replay = await run_owner_mission(
        settings,
        actor_id=owner_id,
        mission_key=f"integration-{draft_id.hex}",
        pack_id=pack_id,
        record_ids=(record_id,),
        approved_readiness_digest=readiness.readiness_digest,
    )

    assert report == replay
    assert report.approval_mode == "owner"
    assert report.owner_authorization_id == str(authorization_id)
    assert report.accepted_count == report.acceptance_target == 1
    assert report.record_ids == (record_id,)
    assert report.receipt_digests == (receipt_digest,)


@pytest.mark.skipif(
    INTEGRATION_DATABASE_URL is None,
    reason="INTEGRATION_DATABASE_URL is required for PostgreSQL integration tests",
)
def test_owner_workflow_is_digest_bound_attributed_and_idempotent() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    alembic_command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_exercise_owner_workflow(INTEGRATION_DATABASE_URL))
