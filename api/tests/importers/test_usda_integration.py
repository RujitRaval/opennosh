from __future__ import annotations

import asyncio
import json
import os
from copy import deepcopy
from pathlib import Path

import pytest
from alembic import command
from opennosh_api.importers.usda import import_usda
from opennosh_api.models import FoodReference
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.tests.test_migrations import migration_config

INTEGRATION_DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL")
FIXTURE = Path(__file__).parents[1] / "fixtures" / "usda" / "foundation.json"


async def _exercise_idempotent_import(database_url: str, tmp_path: Path) -> None:
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE foods_reference"))

        async with session_factory() as session, session.begin():
            first = await import_usda(session, [FIXTURE, FIXTURE], batch_size=500)
        async with session_factory() as session, session.begin():
            second = await import_usda(session, [FIXTURE], batch_size=1)

        payload = json.loads(FIXTURE.read_text())
        newer_food = deepcopy(payload["FoundationFoods"][0])
        newer_food["description"] = "Hummus, newer USDA release"
        newer_food["publicationDate"] = "2026-04-30"
        newer_path = tmp_path / "newer-foundation.json"
        newer_path.write_text(json.dumps({"FoundationFoods": [newer_food]}))
        async with session_factory() as session, session.begin():
            newer = await import_usda(session, [newer_path])
        async with session_factory() as session, session.begin():
            stale = await import_usda(session, [FIXTURE])

        async with session_factory() as session:
            count = await session.scalar(select(func.count()).select_from(FoodReference))
            row = await session.scalar(
                select(FoodReference).where(FoodReference.fdc_id == "321358")
            )

        assert first.rows_inserted == 1
        assert first.rows_updated == 1
        assert first.rows_rejected == 4
        assert second.rows_inserted == 0
        assert second.rows_updated == 1
        assert newer.rows_updated == 1
        assert stale.rows_written == 0
        assert stale.rows_skipped_stale == 1
        assert count == 1
        assert row is not None
        assert row.description == "Hummus, newer USDA release"
        assert row.source == "usda"
        assert row.license == "CC0"
        assert row.nutrients_json["nutrients"]["energy_kcal"] == "229"
    finally:
        await engine.dispose()


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_usda_import_is_idempotent_and_preserves_provenance(tmp_path: Path) -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_exercise_idempotent_import(INTEGRATION_DATABASE_URL, tmp_path))
