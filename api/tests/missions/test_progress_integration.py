from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg  # type: ignore[import-untyped]
import pytest
from alembic import command as alembic_command
from opennosh_api.jobs.worker import asyncpg_dsn
from opennosh_api.missions.contracts import (
    MissionDefinitionSpec,
    MissionGapKind,
    MissionLifecycleAction,
)
from opennosh_api.missions.progress_service import (
    BindMissionContribution,
    RebuildMissionProgress,
    bind_mission_contribution,
    rebuild_mission_progress,
)
from opennosh_api.missions.repository import MissionRepository
from opennosh_api.missions.service import (
    ProposeMission,
    TransitionMission,
    propose_mission,
    transition_mission,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.tests.test_migrations import migration_config

INTEGRATION_DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL")
NOW = datetime(2026, 9, 2, 21, tzinfo=UTC)
PACK_ID = "opennosh-starter"


async def _insert_accepted_event(
    connection: asyncpg.Connection,
    *,
    draft_id: UUID,
    draft_version: int,
    intent_id: UUID,
    prior_intent_id: UUID | None,
    receipt_digest: str,
    prior_receipt_digest: str | None,
    receipt_event_type: str,
    accepted_event_type: str,
    commit_sha: str,
    published_at: datetime,
) -> None:
    publication_id = uuid4()
    await connection.execute(
        """
        INSERT INTO publication_intents (
            id, source_draft_id, prior_publication_intent_id, source_draft_version,
            reviewed_decision_id, approving_actor_id, state, pack_id, record_id,
            approved_payload_digest, expected_base_commit, required_checks_json,
            forge_target, idempotency_key_hash, event_type, prior_receipt_digest,
            evidence_manifest_digests_json, evidence_acknowledgements_json, published_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6, 'published', $7, 'food-1', $8, $9,
            '[]'::jsonb, 'github:RujitRaval/opennosh', $10, $11, $12,
            $13::jsonb, $14::jsonb, $15
        )
        """,
        intent_id,
        draft_id,
        prior_intent_id,
        draft_version,
        uuid4(),
        uuid4(),
        PACK_ID,
        "d" * 64,
        "e" * 40,
        hashlib.sha256(intent_id.bytes).hexdigest(),
        receipt_event_type,
        prior_receipt_digest,
        json.dumps(["f" * 64]),
        json.dumps([{"kind": "immutable"}]),
        published_at,
    )
    await connection.execute(
        """
        INSERT INTO publication_receipts (
            id, publication_intent_id, publication_id, schema_version, receipt_digest,
            event_type, prior_receipt_digest, pack_id, record_id, envelope_json,
            signature_key_id, registry_reference, artifact_reference, published_at,
            reconciled_at
        ) VALUES (
            $1, $2, $3, '1.0', $4, $5, $6, $7, 'food-1', $8::jsonb,
            'mission-test-key', 'registry:mission-test', 'artifact:mission-test',
            $9, $9
        )
        """,
        uuid4(),
        intent_id,
        publication_id,
        receipt_digest,
        receipt_event_type,
        prior_receipt_digest,
        PACK_ID,
        json.dumps(
            {
                "receipt": {
                    "schema_version": "1.0",
                    "publication_id": str(publication_id),
                    "event_type": receipt_event_type,
                    "prior_receipt_digest": prior_receipt_digest,
                    "pack_id": PACK_ID,
                    "record_id": "food-1",
                    "merged_commit": commit_sha,
                    "published_at": published_at.isoformat(),
                    "verified_steps": [
                        {
                            "step": "commit_record",
                            "destination": "github:RujitRaval/opennosh",
                            "external_reference": commit_sha,
                        }
                    ],
                },
                "signature_key_id": "mission-test-key",
            }
        ),
        published_at,
    )
    await connection.execute(
        """
        INSERT INTO accepted_events (
            id, publication_intent_id, repository, commit_sha, pack_id, record_id,
            event_type, receipt_digest, published_at
        ) VALUES (
            $1, $2, 'github:RujitRaval/opennosh', $3, $4, 'food-1', $5, $6, $7
        )
        """,
        uuid4(),
        intent_id,
        commit_sha,
        PACK_ID,
        accepted_event_type,
        receipt_digest,
        published_at,
    )


async def _exercise_progress_rebuild(database_url: str) -> None:
    mission_id = uuid4()
    definition_id = uuid4()
    proposal_id = uuid4()
    approval_id = uuid4()
    proposer_id = uuid4()
    steward_id = uuid4()
    contributor_id = uuid4()
    draft_id = uuid4()
    suffix = mission_id.hex
    receipt_digests = [
        hashlib.sha256(f"{suffix}:receipt:{index}".encode()).hexdigest()
        for index in range(3)
    ]
    commit_shas = [
        hashlib.sha256(f"{suffix}:commit:{index}".encode()).hexdigest()[:40]
        for index in range(3)
    ]
    connection = await asyncpg.connect(asyncpg_dsn(database_url))
    try:
        await connection.executemany(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, 'hash')",
            (
                (proposer_id, f"progress-proposer-{suffix}@example.test"),
                (steward_id, f"progress-steward-{suffix}@example.test"),
                (contributor_id, f"progress-contributor-{suffix}@example.test"),
            ),
        )
        await connection.executemany(
            "INSERT INTO governance_role_assignments "
            "(pack_id, actor_id, role, granted_by_actor_id, grant_reason, granted_at) "
            "VALUES ($1, $2, 'steward', $2, 'mission progress test', $3)",
            ((PACK_ID, proposer_id, NOW), (PACK_ID, steward_id, NOW)),
        )
        await connection.execute(
            "INSERT INTO contribution_drafts "
            "(id, user_id, draft_version, review_state, fields_json) "
            "VALUES ($1, $2, 1, 'draft', $3::jsonb)",
            draft_id,
            contributor_id,
            json.dumps({"pack_id": PACK_ID}),
        )
    finally:
        await connection.close()

    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session, session.begin():
            definition, proposal = await propose_mission(
                MissionRepository(session),
                ProposeMission(
                    mission_id=mission_id,
                    definition_id=definition_id,
                    event_id=proposal_id,
                    actor_id=proposer_id,
                    responsible_steward_actor_id=steward_id,
                    definition=MissionDefinitionSpec(
                        gap_kind=MissionGapKind.DATASET,
                        title="Complete the verified mission projection",
                        summary="Exercise real accepted-event and receipt lineage.",
                        target_pack_id=PACK_ID,
                        target_dataset="mission-progress-integration",
                        acceptance_target=1,
                        acceptance_criteria="Count one current verified accepted record.",
                    ),
                    public_reason="Prove the rebuildable mission progress boundary.",
                ),
                now=NOW,
            )
        async with sessions() as session, session.begin():
            await transition_mission(
                MissionRepository(session),
                TransitionMission(
                    mission_id=mission_id,
                    definition_id=definition.id,
                    event_id=approval_id,
                    expected_prior_event_id=proposal.id,
                    actor_id=steward_id,
                    action=MissionLifecycleAction.APPROVE,
                    public_reason="Approve the bounded integration mission.",
                ),
                now=NOW + timedelta(seconds=1),
            )
        async with sessions() as session, session.begin():
            await bind_mission_contribution(
                MissionRepository(session),
                BindMissionContribution(
                    binding_id=uuid4(),
                    mission_id=mission_id,
                    definition_id=definition_id,
                    source_draft_id=draft_id,
                    source_draft_version=1,
                    actor_id=contributor_id,
                ),
                now=NOW + timedelta(seconds=2),
            )

        intent_ids = [uuid4(), uuid4(), uuid4()]
        connection = await asyncpg.connect(asyncpg_dsn(database_url))
        try:
            await _insert_accepted_event(
                connection,
                draft_id=draft_id,
                draft_version=1,
                intent_id=intent_ids[0],
                prior_intent_id=None,
                receipt_digest=receipt_digests[0],
                prior_receipt_digest=None,
                receipt_event_type="publication",
                accepted_event_type="record.published",
                commit_sha=commit_shas[0],
                published_at=NOW + timedelta(minutes=1),
            )
        finally:
            await connection.close()

        async with sessions() as session, session.begin():
            first = await rebuild_mission_progress(
                MissionRepository(session),
                RebuildMissionProgress(
                    checkpoint_id=uuid4(),
                    activation_id=uuid4(),
                    mission_id=mission_id,
                    definition_id=definition_id,
                    expected_active_checkpoint_id=None,
                ),
                now=NOW + timedelta(minutes=2),
            )
        assert first.progress.accepted_count == 1
        assert first.progress.matched_event_count == 1

        connection = await asyncpg.connect(asyncpg_dsn(database_url))
        try:
            await _insert_accepted_event(
                connection,
                draft_id=draft_id,
                draft_version=2,
                intent_id=intent_ids[1],
                prior_intent_id=None,
                receipt_digest=receipt_digests[1],
                prior_receipt_digest=receipt_digests[0],
                receipt_event_type="correction",
                accepted_event_type="record.corrected",
                commit_sha=commit_shas[1],
                published_at=NOW + timedelta(minutes=3),
            )
        finally:
            await connection.close()

        async with sessions() as session, session.begin():
            second = await rebuild_mission_progress(
                MissionRepository(session),
                RebuildMissionProgress(
                    checkpoint_id=uuid4(),
                    activation_id=uuid4(),
                    mission_id=mission_id,
                    definition_id=definition_id,
                    expected_active_checkpoint_id=first.checkpoint.id,
                ),
                now=NOW + timedelta(minutes=4),
            )
        assert second.progress.accepted_count == 1
        assert second.progress.matched_event_count == 2
        assert second.progress.records[0].receipt_digest == receipt_digests[1]

        connection = await asyncpg.connect(asyncpg_dsn(database_url))
        try:
            await _insert_accepted_event(
                connection,
                draft_id=draft_id,
                draft_version=3,
                intent_id=intent_ids[2],
                prior_intent_id=None,
                receipt_digest=receipt_digests[2],
                prior_receipt_digest=receipt_digests[1],
                receipt_event_type="revocation",
                accepted_event_type="record.revoked",
                commit_sha=commit_shas[2],
                published_at=NOW + timedelta(minutes=5),
            )
        finally:
            await connection.close()

        async with sessions() as session, session.begin():
            third = await rebuild_mission_progress(
                MissionRepository(session),
                RebuildMissionProgress(
                    checkpoint_id=uuid4(),
                    activation_id=uuid4(),
                    mission_id=mission_id,
                    definition_id=definition_id,
                    expected_active_checkpoint_id=second.checkpoint.id,
                ),
                now=NOW + timedelta(minutes=6),
            )
        assert third.progress.accepted_count == 0
        assert third.progress.matched_event_count == 3
        assert third.progress.records == ()

        async with engine.connect() as database:
            active = await database.exec_driver_sql(
                "SELECT checkpoint_id FROM mission_progress_activations "
                "WHERE definition_id = $1",
                (definition_id,),
            )
            assert active.scalar_one() == third.checkpoint.id
            checkpoints = await database.exec_driver_sql(
                "SELECT count(*) FROM mission_progress_checkpoints WHERE definition_id = $1",
                (definition_id,),
            )
            assert checkpoints.scalar_one() == 3
    finally:
        await engine.dispose()


@pytest.mark.skipif(
    INTEGRATION_DATABASE_URL is None,
    reason="INTEGRATION_DATABASE_URL is required for PostgreSQL integration tests",
)
def test_bind_build_correct_and_revoke_progress_in_postgresql() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    alembic_command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_exercise_progress_rebuild(INTEGRATION_DATABASE_URL))
