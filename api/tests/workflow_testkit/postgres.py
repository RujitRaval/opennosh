from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import asyncpg  # type: ignore[import-untyped]
from opennosh_api.evidence.contracts import EvidenceAcknowledgement
from opennosh_api.jobs.pgqueuer import PGQUEUER_SETTINGS, PgQueuerJobQueue
from opennosh_api.jobs.worker import asyncpg_dsn
from opennosh_api.publication.service import CreatePublicationIntent, create_publication_intent
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.tests.workflow_testkit.deterministic import DeterministicIdGenerator

FORGE_TARGET = "github:RujitRaval/opennosh-data"
_PUBLICATION_TABLES = (
    "publication_intents",
    "publication_steps",
    "publication_durable_acknowledgements",
    "publication_receipts",
    "accepted_events",
)
_SNAPSHOT_TABLES = (*_PUBLICATION_TABLES, PGQUEUER_SETTINGS.queue_table)


@dataclass(frozen=True, slots=True)
class SeededPublication:
    publication_id: UUID
    queue_job_id: int


@dataclass(frozen=True, slots=True)
class PublicationDatabaseSnapshot:
    publication_id: UUID
    rows: tuple[tuple[str, tuple[str, ...]], ...]


async def reset_trust_tables(database_url: str) -> None:
    connection = await asyncpg.connect(asyncpg_dsn(database_url))
    try:
        await connection.execute(
            "TRUNCATE accepted_events, publication_receipts, "
            "publication_durable_acknowledgements, "
            "publication_steps, publication_intents, opennosh_pgqueuer, "
            "opennosh_pgqueuer_log, opennosh_pgqueuer_statistics, "
            "opennosh_pgqueuer_schedules, contribution_drafts, users CASCADE"
        )
    finally:
        await connection.close()


async def seed_publication(
    database_url: str,
    *,
    now: datetime,
    ids: DeterministicIdGenerator,
    suffix: str,
) -> SeededPublication:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Seed publication time must include a timezone")
    user_id = ids()
    draft_id = ids()
    connection = await asyncpg.connect(asyncpg_dsn(database_url))
    try:
        async with connection.transaction():
            await connection.execute(
                "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, 'hash')",
                user_id,
                f"testkit-{suffix}-{user_id}@example.test",
            )
            await connection.execute(
                "INSERT INTO contribution_drafts "
                "(id, user_id, client_draft_id, review_state) "
                "VALUES ($1, $2, $3, 'approved')",
                draft_id,
                user_id,
                f"testkit-{suffix}-{draft_id}",
            )
            await connection.execute(
                "INSERT INTO evidence_manifests "
                "(id, source_draft_id, source_draft_version, schema_version, "
                "evidence_class, manifest_digest, manifest_json, public_state) "
                "VALUES ($1, $2, 1, '1.0', 'sanitized_media', $3, "
                "'{}'::jsonb, 'evidence_preserved')",
                ids(),
                draft_id,
                "f" * 64,
            )
    finally:
        await connection.close()

    queue = PgQueuerJobQueue(clock=lambda: now)
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        command = CreatePublicationIntent(
            source_draft_id=draft_id,
            source_draft_version=1,
            reviewed_decision_id=ids(),
            approving_actor_id=ids(),
            pack_id="global-core",
            record_id=f"record-{suffix}",
            approved_payload_digest="a" * 64,
            expected_base_commit="b" * 40,
            required_checks=("schema", "provenance", "license"),
            forge_target=FORGE_TARGET,
            idempotency_key=f"publication-testkit-{suffix}",
            evidence_manifest_digests=("f" * 64,),
            evidence_acknowledgements=(
                EvidenceAcknowledgement(
                    evidence_id=draft_id,
                    evidence_class="sanitized_media",
                    manifest_digest="f" * 64,
                    kind="immutable_sanitized_copy",
                    destination="urn:opennosh:durability:evidence",
                    content_digest="7" * 64,
                    external_reference="memory:evidence",
                    verified_at=now,
                    adapter_identity="workflow-testkit-evidence",
                    adapter_version="1.0",
                ),
            ),
        )
        async with sessions() as session:
            async with session.begin():
                intent = await create_publication_intent(
                    session,
                    queue,
                    command,
                    now=now,
                    id_generator=ids,
                )
        async with engine.connect() as connection:
            result = await connection.exec_driver_sql(
                f"SELECT id FROM {PGQUEUER_SETTINGS.queue_table} ORDER BY id LIMIT 1"
            )
            queue_job_id = result.scalar_one()
        return SeededPublication(publication_id=intent.id, queue_job_id=int(queue_job_id))
    finally:
        await engine.dispose()


def _snapshot_select(table: str) -> str:
    if table == "publication_intents":
        predicate = "source.id = $1"
    elif table == PGQUEUER_SETTINGS.queue_table:
        predicate = "(convert_from(source.payload, 'UTF8')::jsonb ->> 'subject_id')::uuid = $1"
    else:
        predicate = "source.publication_intent_id = $1"
    return (
        f"SELECT to_jsonb(source)::text AS value FROM {table} AS source "
        f"WHERE {predicate} ORDER BY source.id"
    )


def _snapshot_delete(table: str) -> str:
    if table == "publication_intents":
        predicate = "id = $1"
    elif table == PGQUEUER_SETTINGS.queue_table:
        predicate = "(convert_from(payload, 'UTF8')::jsonb ->> 'subject_id')::uuid = $1"
    else:
        predicate = "publication_intent_id = $1"
    return f"DELETE FROM {table} WHERE {predicate}"


async def capture_publication_snapshot(
    pool: asyncpg.Pool, publication_id: UUID
) -> PublicationDatabaseSnapshot:
    captured: list[tuple[str, tuple[str, ...]]] = []
    async with pool.acquire() as connection:
        async with connection.transaction(isolation="repeatable_read", readonly=True):
            for table in _SNAPSHOT_TABLES:
                rows = await connection.fetch(_snapshot_select(table), publication_id)
                captured.append((table, tuple(str(row["value"]) for row in rows)))
    if not captured[0][1]:
        raise LookupError(f"Unknown publication intent: {publication_id}")
    return PublicationDatabaseSnapshot(publication_id=publication_id, rows=tuple(captured))


async def restore_publication_snapshot(
    pool: asyncpg.Pool, snapshot: PublicationDatabaseSnapshot
) -> None:
    rows_by_table = dict(snapshot.rows)
    async with pool.acquire() as connection:
        async with connection.transaction():
            locked = await connection.fetchval(
                "SELECT id FROM publication_intents WHERE id = $1 FOR UPDATE",
                snapshot.publication_id,
            )
            if locked is None:
                raise LookupError(f"Unknown publication intent: {snapshot.publication_id}")
            # This test-only helper models an operator restoring a physical database
            # checkpoint. Normal application transactions must never delete ledger rows.
            await connection.execute(
                "ALTER TABLE publication_intents "
                "DISABLE TRIGGER prohibit_publication_intents_delete"
            )
            for table in reversed(_SNAPSHOT_TABLES):
                await connection.execute(
                    _snapshot_delete(table),
                    snapshot.publication_id,
                )
            for table in _SNAPSHOT_TABLES:
                for row in rows_by_table[table]:
                    await connection.execute(
                        f"INSERT INTO {table} "
                        f"SELECT * FROM jsonb_populate_record(NULL::{table}, $1::jsonb)",
                        row,
                    )
            await connection.execute(
                "ALTER TABLE publication_intents "
                "ENABLE TRIGGER prohibit_publication_intents_delete"
            )


async def expire_publication_lease(
    pool: asyncpg.Pool, publication_id: UUID, *, before: datetime
) -> None:
    await pool.execute(
        "UPDATE publication_steps SET lease_expires_at = $1 "
        "WHERE publication_intent_id = $2 AND state = 'leased'",
        before,
        publication_id,
    )
