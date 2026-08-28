from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from uuid import UUID

import pytest
from alembic import command as alembic_command
from opennosh_api.evidence.contracts import (
    EvidenceAcknowledgement,
    EvidenceAcknowledgementKind,
    PublicDocumentManifest,
    manifest_digest,
)
from opennosh_api.first_contribution.prepare import _build_package
from opennosh_api.first_contribution.service import (
    FirstContributionAuthorityError,
    FirstContributionConflictError,
    commit_usda_first_contribution,
)
from opennosh_api.jobs.pgqueuer import PGQUEUER_SETTINGS, PgQueuerJobQueue
from opennosh_api.public.r2 import R2PublicationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from api.tests.test_migrations import migration_config

INTEGRATION_DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL")
NOW = datetime(2026, 8, 28, 17, 30, tzinfo=UTC)
STEWARD = UUID("22222222-2222-4222-8222-222222222222")
SECOND_STEWARD = UUID("33333333-3333-4333-8333-333333333333")


class MemoryEvidenceStore:
    def __init__(self) -> None:
        self.calls = 0

    async def preserve(
        self,
        manifest: PublicDocumentManifest,
        *,
        now: datetime,
    ) -> EvidenceAcknowledgement:
        self.calls += 1
        digest = manifest_digest(manifest)
        return EvidenceAcknowledgement(
            evidence_id=manifest.evidence_id,
            evidence_class=manifest.evidence_class,
            manifest_digest=digest,
            kind=EvidenceAcknowledgementKind.CITATION_MANIFEST,
            destination="r2://opennosh-public-commons",
            content_digest=digest,
            external_reference=(
                f"r2://opennosh-public-commons/evidence/citations/v1/{digest}.json"
            ),
            verified_at=now,
            adapter_identity="opennosh.test-first-contribution-store",
            adapter_version="1.0",
        )


class BarrierEvidenceStore(MemoryEvidenceStore):
    def __init__(self) -> None:
        super().__init__()
        self._both_arrived = asyncio.Event()

    async def preserve(
        self,
        manifest: PublicDocumentManifest,
        *,
        now: datetime,
    ) -> EvidenceAcknowledgement:
        self.calls += 1
        if self.calls == 2:
            self._both_arrived.set()
        else:
            await self._both_arrived.wait()
        return await MemoryEvidenceStore().preserve(manifest, now=now)


class FailOnceEvidenceStore(MemoryEvidenceStore):
    async def preserve(
        self,
        manifest: PublicDocumentManifest,
        *,
        now: datetime,
    ) -> EvidenceAcknowledgement:
        if self.calls == 0:
            self.calls += 1
            raise R2PublicationError("simulated first evidence write failure")
        return await super().preserve(manifest, now=now)


async def _truncate_first_contribution_state(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                TRUNCATE evidence_durable_acknowledgements, evidence_manifests,
                  governance_decisions, governance_role_assignments,
                  publication_steps, publication_intents,
                  contribution_drafts, auth_sessions, users,
                  opennosh_pgqueuer, opennosh_pgqueuer_log,
                  opennosh_pgqueuer_statistics, opennosh_pgqueuer_schedules
                CASCADE
                """
            )
        )


async def run_first_contribution_replays(database_url: str) -> None:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = MemoryEvidenceStore()
    package = _build_package("a" * 64)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    TRUNCATE evidence_durable_acknowledgements, evidence_manifests,
                      governance_decisions, governance_role_assignments,
                      publication_steps, publication_intents,
                      contribution_drafts, auth_sessions, users,
                      opennosh_pgqueuer, opennosh_pgqueuer_log,
                      opennosh_pgqueuer_statistics, opennosh_pgqueuer_schedules
                    CASCADE
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO users (id, email, password_hash)
                    VALUES (:id, 'steward@example.test', 'hash')
                    """
                ),
                {"id": STEWARD},
            )
        queue = PgQueuerJobQueue(clock=lambda: NOW)
        receipts = []
        for _ in range(10):
            receipts.append(
                await commit_usda_first_contribution(
                    factory,
                    queue,
                    store,
                    package,
                    steward_actor_id=STEWARD,
                    expected_base_commit="b" * 40,
                    reason="Reviewed the pinned USDA record and first pack scope.",
                    bootstrap_steward=True,
                    now=NOW,
                )
            )

        assert all(receipt == receipts[0] for receipt in receipts)
        assert receipts[0].evidence_state == "reference_only"
        assert receipts[0].publication_intent_id == package.publication_intent_id
        assert store.calls == 1
        with pytest.raises(FirstContributionConflictError):
            await commit_usda_first_contribution(
                factory,
                queue,
                store,
                package,
                steward_actor_id=STEWARD,
                expected_base_commit="c" * 40,
                reason="Reviewed the pinned USDA record and first pack scope.",
                bootstrap_steward=True,
                now=NOW,
            )
        with pytest.raises(FirstContributionConflictError):
            await commit_usda_first_contribution(
                factory,
                queue,
                store,
                package,
                steward_actor_id=STEWARD,
                expected_base_commit="b" * 40,
                reason="A different approval reason.",
                bootstrap_steward=True,
                now=NOW,
            )
        assert store.calls == 1

        async with engine.connect() as connection:
            counts = {}
            for table in (
                "users",
                "contribution_drafts",
                "evidence_manifests",
                "evidence_durable_acknowledgements",
                "governance_role_assignments",
                "governance_decisions",
                "publication_intents",
                PGQUEUER_SETTINGS.queue_table,
            ):
                counts[table] = int(
                    (await connection.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()
                )
            draft_state = await connection.scalar(
                text("SELECT review_state FROM contribution_drafts WHERE id = :id"),
                {"id": package.draft_id},
            )
            evidence_state = await connection.scalar(
                text("SELECT public_state FROM evidence_manifests WHERE id = :id"),
                {"id": package.evidence_id},
            )
            source_actor = (
                await connection.execute(
                    text(
                        """
                        SELECT actor_kind, recovery_token_hash,
                               login_disabled_at IS NOT NULL AS disabled
                        FROM users WHERE id = :id
                        """
                    ),
                    {"id": package.source_actor_id},
                )
            ).one()
        assert counts == {
            "users": 2,
            "contribution_drafts": 1,
            "evidence_manifests": 1,
            "evidence_durable_acknowledgements": 1,
            "governance_role_assignments": 1,
            "governance_decisions": 1,
            "publication_intents": 1,
            PGQUEUER_SETTINGS.queue_table: 1,
        }
        assert draft_state == "publication_pending"
        assert evidence_state == "reference_only"
        assert tuple(source_actor) == ("service", None, True)
    finally:
        await engine.dispose()


@pytest.mark.skipif(
    INTEGRATION_DATABASE_URL is None,
    reason="INTEGRATION_DATABASE_URL is required for PostgreSQL integration tests",
)
def test_first_contribution_ten_replays_create_one_pending_intent() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    alembic_command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(run_first_contribution_replays(INTEGRATION_DATABASE_URL))


async def run_competing_steward_bootstraps(database_url: str) -> None:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    package = _build_package("c" * 64)
    store = BarrierEvidenceStore()
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    TRUNCATE evidence_durable_acknowledgements, evidence_manifests,
                      governance_decisions, governance_role_assignments,
                      publication_steps, publication_intents,
                      contribution_drafts, auth_sessions, users,
                      opennosh_pgqueuer, opennosh_pgqueuer_log,
                      opennosh_pgqueuer_statistics, opennosh_pgqueuer_schedules
                    CASCADE
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO users (id, email, password_hash)
                    VALUES
                      (:first_id, 'first-steward@example.test', 'hash'),
                      (:second_id, 'second-steward@example.test', 'hash')
                    """
                ),
                {"first_id": STEWARD, "second_id": SECOND_STEWARD},
            )
        queue = PgQueuerJobQueue(clock=lambda: NOW)

        async def attempt(steward_actor_id: UUID) -> object:
            try:
                return await commit_usda_first_contribution(
                    factory,
                    queue,
                    store,
                    package,
                    steward_actor_id=steward_actor_id,
                    expected_base_commit="d" * 40,
                    reason="Reviewed the pinned USDA record and first pack scope.",
                    bootstrap_steward=True,
                    now=NOW,
                )
            except FirstContributionConflictError as error:
                return error

        results = await asyncio.gather(attempt(STEWARD), attempt(SECOND_STEWARD))

        assert sum(not isinstance(result, Exception) for result in results) == 1
        assert sum(isinstance(result, FirstContributionConflictError) for result in results) == 1
        assert store.calls == 2
        async with engine.connect() as connection:
            counts = []
            for table in (
                "governance_role_assignments",
                "governance_decisions",
                "publication_intents",
                PGQUEUER_SETTINGS.queue_table,
            ):
                result = await connection.execute(text(f"SELECT count(*) FROM {table}"))
                counts.append(int(result.scalar_one()))
        assert counts == [1, 1, 1, 1]
    finally:
        await engine.dispose()


@pytest.mark.skipif(
    INTEGRATION_DATABASE_URL is None,
    reason="INTEGRATION_DATABASE_URL is required for PostgreSQL integration tests",
)
def test_competing_stewards_are_serialized_to_one_bootstrap() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    alembic_command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(run_competing_steward_bootstraps(INTEGRATION_DATABASE_URL))


async def run_first_contribution_resume(database_url: str) -> None:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    package = _build_package("e" * 64)
    store = FailOnceEvidenceStore()
    queue = PgQueuerJobQueue(clock=lambda: NOW)
    try:
        await _truncate_first_contribution_state(engine)
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO users (id, email, password_hash)
                    VALUES (:id, 'resume-steward@example.test', 'hash')
                    """
                ),
                {"id": STEWARD},
            )

        with pytest.raises(R2PublicationError, match="evidence write failure"):
            await commit_usda_first_contribution(
                factory,
                queue,
                store,
                package,
                steward_actor_id=STEWARD,
                expected_base_commit="f" * 40,
                reason="Reviewed the pinned USDA record and first pack scope.",
                bootstrap_steward=True,
                now=NOW,
            )

        async with engine.connect() as connection:
            first_phase = []
            for table in (
                "contribution_drafts",
                "evidence_manifests",
                "evidence_durable_acknowledgements",
                "governance_role_assignments",
                "governance_decisions",
                "publication_intents",
                PGQUEUER_SETTINGS.queue_table,
            ):
                first_phase.append(
                    int(
                        (
                            await connection.execute(text(f"SELECT count(*) FROM {table}"))
                        ).scalar_one()
                    )
                )
        assert first_phase == [1, 1, 0, 0, 0, 0, 0]

        receipt = await commit_usda_first_contribution(
            factory,
            queue,
            store,
            package,
            steward_actor_id=STEWARD,
            expected_base_commit="f" * 40,
            reason="Reviewed the pinned USDA record and first pack scope.",
            bootstrap_steward=True,
            now=NOW,
        )
        assert receipt.publication_intent_id == package.publication_intent_id
        assert store.calls == 2

        async with engine.connect() as connection:
            completed = []
            for table in (
                "evidence_durable_acknowledgements",
                "governance_role_assignments",
                "governance_decisions",
                "publication_intents",
                PGQUEUER_SETTINGS.queue_table,
            ):
                completed.append(
                    int(
                        (
                            await connection.execute(text(f"SELECT count(*) FROM {table}"))
                        ).scalar_one()
                    )
                )
        assert completed == [1, 1, 1, 1, 1]
    finally:
        await engine.dispose()


@pytest.mark.skipif(
    INTEGRATION_DATABASE_URL is None,
    reason="INTEGRATION_DATABASE_URL is required for PostgreSQL integration tests",
)
def test_first_contribution_resumes_after_evidence_provider_failure() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    alembic_command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(run_first_contribution_resume(INTEGRATION_DATABASE_URL))


async def run_rejected_steward_scenario(database_url: str, scenario: str) -> None:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    package = _build_package("1" * 64)
    queue = PgQueuerJobQueue(clock=lambda: NOW)
    store = MemoryEvidenceStore()
    steward_actor_id = package.source_actor_id if scenario == "source-service" else STEWARD
    try:
        await _truncate_first_contribution_state(engine)
        async with engine.begin() as connection:
            if scenario not in {"missing", "source-service"}:
                actor_kind = "service" if scenario == "service" else "person"
                disabled_at = NOW if scenario == "service" else None
                await connection.execute(
                    text(
                        """
                        INSERT INTO users (
                          id, email, password_hash, actor_kind, login_disabled_at
                        ) VALUES (
                          :id, 'candidate-steward@example.test', 'hash',
                          :actor_kind, :disabled_at
                        )
                        """
                    ),
                    {
                        "id": STEWARD,
                        "actor_kind": actor_kind,
                        "disabled_at": disabled_at,
                    },
                )
            if scenario == "prior-role":
                await connection.execute(
                    text(
                        """
                        INSERT INTO users (id, email, password_hash)
                        VALUES (:id, 'prior-steward@example.test', 'hash')
                        """
                    ),
                    {"id": SECOND_STEWARD},
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO governance_role_assignments (
                          id, pack_id, actor_id, role, granted_by_actor_id,
                          grant_reason, granted_at
                        ) VALUES (
                          '44444444-4444-4444-8444-444444444444',
                          'common-fruits', :id, 'steward', :id,
                          'Existing reviewed steward scope.', :granted_at
                        )
                        """
                    ),
                    {"id": SECOND_STEWARD, "granted_at": NOW},
                )

        with pytest.raises(FirstContributionAuthorityError):
            await commit_usda_first_contribution(
                factory,
                queue,
                store,
                package,
                steward_actor_id=steward_actor_id,
                expected_base_commit="2" * 40,
                reason="Reviewed the pinned USDA record and first pack scope.",
                bootstrap_steward=scenario != "no-bootstrap",
                now=NOW,
            )
        assert store.calls == 0
        async with engine.connect() as connection:
            untouched = []
            for table in (
                "contribution_drafts",
                "evidence_manifests",
                "evidence_durable_acknowledgements",
                "governance_decisions",
                "publication_intents",
                PGQUEUER_SETTINGS.queue_table,
            ):
                untouched.append(
                    int(
                        (
                            await connection.execute(text(f"SELECT count(*) FROM {table}"))
                        ).scalar_one()
                    )
                )
            source_actor_count = int(
                (
                    await connection.execute(
                        text("SELECT count(*) FROM users WHERE id = :id"),
                        {"id": package.source_actor_id},
                    )
                ).scalar_one()
            )
        assert untouched == [0, 0, 0, 0, 0, 0]
        assert source_actor_count == 0
    finally:
        await engine.dispose()


@pytest.mark.skipif(
    INTEGRATION_DATABASE_URL is None,
    reason="INTEGRATION_DATABASE_URL is required for PostgreSQL integration tests",
)
@pytest.mark.parametrize(
    "scenario",
    ["no-bootstrap", "missing", "service", "source-service", "prior-role"],
)
def test_first_contribution_rejects_invalid_steward_authority(scenario: str) -> None:
    assert INTEGRATION_DATABASE_URL is not None
    alembic_command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(run_rejected_steward_scenario(INTEGRATION_DATABASE_URL, scenario))
