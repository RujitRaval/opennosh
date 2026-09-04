from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

import pytest
from alembic import command as alembic_command
from opennosh_api.impact.contracts import signed_impact_snapshot
from opennosh_api.impact.service import latest_impact_snapshot, persist_impact_snapshot
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.tests.test_migrations import migration_config

INTEGRATION_DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL")


async def _exercise_snapshot_store(database_url: str) -> None:
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    snapshot = signed_impact_snapshot(
        state="live",
        reason=None,
        observed_at=datetime(2026, 9, 4, 4, tzinfo=UTC),
        source_checkpoint_id="integration-impact-checkpoint",
        global_={"verified_adopters": 10, "accepted_contributions": 14},
        regions=(
            {
                "level": "country",
                "region_code": "US",
                "verified_adopters": 10,
                "community_declarations": 10,
            },
        ),
    )
    try:
        async with sessions() as session, session.begin():
            first = await persist_impact_snapshot(session, snapshot)
        async with sessions() as session, session.begin():
            replay = await persist_impact_snapshot(session, snapshot)
        assert first.id == replay.id

        async with sessions() as session:
            loaded = await latest_impact_snapshot(session)
            assert loaded == snapshot
            persisted = await session.scalar(
                text("SELECT snapshot_json::text FROM impact_snapshots WHERE id = :id"),
                {"id": first.id},
            )
            assert persisted is not None
            for forbidden in (
                "actor_id",
                "user_id",
                "organization_key",
                "declaration_id",
                "accepted_event_id",
            ):
                assert forbidden not in persisted

        async with engine.begin() as connection:
            with pytest.raises(DBAPIError, match="append-only"):
                await connection.execute(
                    text("UPDATE impact_snapshots SET state = 'zero' WHERE id = :id"),
                    {"id": first.id},
                )
        async with engine.begin() as connection:
            with pytest.raises(DBAPIError, match="append-only"):
                await connection.execute(
                    text("DELETE FROM impact_snapshots WHERE id = :id"),
                    {"id": first.id},
                )
    finally:
        await engine.dispose()


@pytest.mark.skipif(
    INTEGRATION_DATABASE_URL is None,
    reason="INTEGRATION_DATABASE_URL is required for PostgreSQL integration tests",
)
def test_impact_snapshot_store_is_idempotent_sanitized_and_append_only() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    alembic_command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_exercise_snapshot_store(INTEGRATION_DATABASE_URL))
