from __future__ import annotations

import asyncio
import json
import os
from copy import deepcopy
from pathlib import Path

import pytest
from alembic import command
from opennosh_api.importers.wger import import_wger
from opennosh_api.models import Exercise
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.tests.test_migrations import migration_config

INTEGRATION_DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL")
FIXTURE = Path(__file__).parents[1] / "fixtures" / "wger" / "valid.json"


async def _exercise_idempotent_import(database_url: str, tmp_path: Path) -> None:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE workout_sets, workouts, exercises CASCADE"))

        async with factory() as session, session.begin():
            first = await import_wger(session, [FIXTURE, FIXTURE], batch_size=1)

        payload = json.loads(FIXTURE.read_text())
        newer = deepcopy(payload)
        newer["results"][0]["translations"][0]["name"] = "Updated Barbell Back Squat"
        newer["results"][0]["last_update_global"] = "2026-08-20T12:00:00+00:00"
        newer_path = tmp_path / "newer.json"
        newer_path.write_text(json.dumps(newer))
        async with factory() as session, session.begin():
            second = await import_wger(session, [newer_path])
        async with factory() as session, session.begin():
            stale = await import_wger(session, [FIXTURE])

        async with factory() as session:
            count = await session.scalar(select(func.count()).select_from(Exercise))
            squat = await session.scalar(
                select(Exercise).where(Exercise.source == "wger", Exercise.source_id == "101")
            )

        assert first.rows_seen == 4
        assert first.rows_inserted == 2
        assert first.rows_skipped_stale == 2
        assert first.rows_rejected == 0
        assert second.rows_updated == 1
        assert second.rows_skipped_stale == 1
        assert stale.rows_written == 0
        assert stale.rows_skipped_stale == 2
        assert count == 2
        assert squat is not None
        assert squat.name == "Updated Barbell Back Squat"
        assert squat.license_spdx == "CC-BY-SA-3.0"
        assert squat.translations_json[0]["name"] == "Updated Barbell Back Squat"
    finally:
        await engine.dispose()


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_wger_import_is_idempotent_license_safe_and_stale_aware(tmp_path: Path) -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_exercise_idempotent_import(INTEGRATION_DATABASE_URL, tmp_path))


async def _exercise_concurrent_import(database_url: str, tmp_path: Path) -> None:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    payload = json.loads(FIXTURE.read_text())
    older_path = tmp_path / "older.json"
    newer_path = tmp_path / "newer.json"
    older_path.write_text(json.dumps(payload))
    newer = deepcopy(payload)
    newer["results"][0]["translations"][0]["name"] = "Concurrent newest squat"
    newer["results"][0]["last_update_global"] = "2026-08-20T12:00:00+00:00"
    newer_path.write_text(json.dumps(newer))

    async def run(path: Path) -> None:
        async with factory() as session, session.begin():
            await import_wger(session, [path], batch_size=1)

    try:
        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE workout_sets, workouts, exercises CASCADE"))
        await asyncio.gather(run(older_path), run(newer_path))
        async with factory() as session:
            rows = list(
                (
                    await session.scalars(
                        select(Exercise).where(Exercise.source == "wger")
                    )
                ).all()
            )
        assert len(rows) == 2
        squat = next(row for row in rows if row.source_id == "101")
        assert squat.name == "Concurrent newest squat"
    finally:
        await engine.dispose()


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_wger_import_is_concurrency_safe_and_newest_wins(tmp_path: Path) -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_exercise_concurrent_import(INTEGRATION_DATABASE_URL, tmp_path))


def test_wger_import_rejects_unsafe_batch_sizes() -> None:
    async def run(batch_size: int) -> None:
        with pytest.raises(ValueError, match="batch_size must be between 1 and 1000"):
            await import_wger(None, [FIXTURE], batch_size=batch_size)  # type: ignore[arg-type]

    asyncio.run(run(0))
    asyncio.run(run(1001))
