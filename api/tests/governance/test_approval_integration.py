from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from uuid import UUID

import asyncpg  # type: ignore[import-untyped]
import pytest
from alembic import command as alembic_command
from opennosh_api.governance.contracts import (
    PROTECTED_STATUS_CHECKS,
    ApprovedChangeSet,
    ApprovedFileChange,
)
from opennosh_api.governance.service import (
    ApproveContribution,
    GovernanceDecisionError,
    approve_contribution,
)
from opennosh_api.jobs.pgqueuer import PGQUEUER_SETTINGS, PgQueuerJobQueue
from opennosh_api.jobs.worker import asyncpg_dsn
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.tests.test_migrations import migration_config

INTEGRATION_DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL")
NOW = datetime(2026, 8, 26, 12, tzinfo=UTC)
CONTRIBUTOR = UUID("11111111-1111-4111-8111-111111111111")
STEWARD = UUID("22222222-2222-4222-8222-222222222222")
DRAFT = UUID("33333333-3333-4333-8333-333333333333")


def approval() -> ApproveContribution:
    return ApproveContribution(
        source_draft_id=DRAFT,
        deciding_actor_id=STEWARD,
        approved_changes=ApprovedChangeSet.build(
            pack_id="global-core",
            files=(
                ApprovedFileChange(
                    path="packs/global-core/foods/lentils.json",
                    content='{"name":"Lentils"}\n',
                ),
            ),
        ),
        record_id="lentils",
        expected_base_commit="a" * 40,
        required_checks=PROTECTED_STATUS_CHECKS,
        forge_target="github:RujitRaval/opennosh",
        reason="Source and normalized record reviewed.",
    )


async def run_concurrent_approval(database_url: str) -> None:
    connection = await asyncpg.connect(asyncpg_dsn(database_url))
    try:
        await connection.execute(
            "TRUNCATE governance_decisions, governance_recusals, "
            "governance_publication_pauses, governance_role_assignments, "
            "publication_steps, publication_intents, opennosh_pgqueuer, "
            "opennosh_pgqueuer_log, opennosh_pgqueuer_statistics, "
            "opennosh_pgqueuer_schedules, contribution_drafts, users CASCADE"
        )
        await connection.execute(
            "INSERT INTO users (id, email, password_hash) VALUES "
            "($1, 'contributor@example.test', 'hash'), "
            "($2, 'steward@example.test', 'hash')",
            CONTRIBUTOR,
            STEWARD,
        )
        await connection.execute(
            "INSERT INTO contribution_drafts "
            "(id, user_id, client_draft_id, review_state, fields_json) "
            "VALUES ($1, $2, 'concurrent', 'in_review', '{\"pack_id\":\"global-core\"}')",
            DRAFT,
            CONTRIBUTOR,
        )
        await connection.execute(
            "INSERT INTO governance_role_assignments "
            "(pack_id, actor_id, role, granted_by_actor_id, grant_reason, granted_at) "
            "VALUES ('global-core', $1, 'steward', $1, 'test grant', $2)",
            STEWARD,
            NOW,
        )
    finally:
        await connection.close()

    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    queue = PgQueuerJobQueue(clock=lambda: NOW)

    async def decide() -> object:
        try:
            async with sessions() as session:
                async with session.begin():
                    return await approve_contribution(session, queue, approval(), now=NOW)
        except GovernanceDecisionError as error:
            return error

    try:
        results = await asyncio.gather(decide(), decide())
        errors = [item for item in results if isinstance(item, GovernanceDecisionError)]
        successes = [item for item in results if not isinstance(item, GovernanceDecisionError)]
        assert len(successes) == 1
        assert [error.code for error in errors] == ["contribution_not_in_review"]
        async with engine.connect() as database:
            for table in (
                "governance_decisions",
                "publication_intents",
                PGQUEUER_SETTINGS.queue_table,
            ):
                count = await database.exec_driver_sql(f"SELECT count(*) FROM {table}")
                assert count.scalar_one() == 1
    finally:
        await engine.dispose()


@pytest.mark.skipif(
    INTEGRATION_DATABASE_URL is None,
    reason="INTEGRATION_DATABASE_URL is required for PostgreSQL integration tests",
)
def test_concurrent_steward_decisions_create_exactly_one_publication() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    alembic_command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(run_concurrent_approval(INTEGRATION_DATABASE_URL))
