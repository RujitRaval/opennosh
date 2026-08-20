from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

import pytest
import yaml
from alembic import command
from opennosh_api.foodpacks.loader import (
    load_food_pack_root_with_retries,
    load_food_pack_with_retries,
)
from opennosh_api.models import FoodCommunity
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.tests.test_migrations import migration_config

INTEGRATION_DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL")
FIXTURE = Path(__file__).parent / "fixtures" / "valid" / "balanced-pack"


def _copy_pack(tmp_path: Path, name: str = "balanced-pack") -> Path:
    destination = tmp_path / name
    shutil.copytree(FIXTURE, destination)
    return destination


def _update_pack(path: Path, *, version: str, invalid_second: bool = False) -> None:
    manifest_path = path / "pack.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["version"] = version
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

    foods_path = path / "foods" / "foods.yaml"
    foods = yaml.safe_load(foods_path.read_text(encoding="utf-8"))
    foods[0]["name"] = f"Balanced thepla {version}"
    if invalid_second:
        foods[1]["name"] = ""
    foods_path.write_text(yaml.safe_dump(foods, sort_keys=False), encoding="utf-8")


async def _exercise_loader(database_url: str, tmp_path: Path) -> None:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    pack = _copy_pack(tmp_path)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE foods_community"))

        root = await load_food_pack_root_with_retries(factory, tmp_path)
        first = root.packs[0]
        unchanged = await load_food_pack_with_retries(factory, pack)

        _update_pack(pack, version="1.1.0")
        updated = await load_food_pack_with_retries(factory, pack)

        _update_pack(pack, version="1.2.0", invalid_second=True)
        partial = await load_food_pack_with_retries(factory, pack)

        stale_pack = _copy_pack(tmp_path, "stale-pack")
        stale_foods_path = stale_pack / "foods" / "foods.yaml"
        stale_foods = yaml.safe_load(stale_foods_path.read_text(encoding="utf-8"))
        stale_foods[1]["slug"] = "stale-only-lassi"
        stale_foods_path.write_text(
            yaml.safe_dump(stale_foods, sort_keys=False), encoding="utf-8"
        )
        stale = await load_food_pack_with_retries(factory, stale_pack)

        colliding_pack = _copy_pack(tmp_path, "colliding-pack")
        colliding_manifest_path = colliding_pack / "pack.yaml"
        colliding_manifest = yaml.safe_load(
            colliding_manifest_path.read_text(encoding="utf-8")
        )
        colliding_manifest["id"] = "colliding-pack"
        colliding_manifest_path.write_text(
            yaml.safe_dump(colliding_manifest, sort_keys=False), encoding="utf-8"
        )
        collision = await load_food_pack_with_retries(factory, colliding_pack)

        async with factory() as session:
            rows = list(
                (
                    await session.scalars(
                        select(FoodCommunity).order_by(FoodCommunity.slug)
                    )
                ).all()
            )

        assert first.entries_inserted == 2
        assert unchanged.entries_written == 0
        assert unchanged.entries_unchanged == 2
        assert updated.entries_updated == 2
        assert partial.entries_updated == 1
        assert partial.entries_rejected == 1
        assert stale.entries_written == 0
        assert stale.entries_skipped_stale == 2
        assert collision.entries_rejected == 2
        assert {issue.code for issue in collision.issues} == {"slug_collision"}
        assert len(rows) == 2
        by_slug = {row.slug: row for row in rows}
        assert "stale-only-lassi" not in by_slug
        assert by_slug["balanced-thepla"].pack_version == "1.2.0"
        assert by_slug["public-domain-lassi"].pack_version == "1.1.0"
        assert by_slug["public-domain-lassi"].contributed_by == "test-contributor"
        assert by_slug["public-domain-lassi"].source_license == "public-domain"
        assert by_slug["public-domain-lassi"].source_uri == "https://example.gov/foods/lassi"
    finally:
        await engine.dispose()


async def _exercise_concurrent_retry(database_url: str) -> None:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE foods_community"))

        reports = await asyncio.gather(
            load_food_pack_with_retries(factory, FIXTURE),
            load_food_pack_with_retries(factory, FIXTURE),
        )
        async with factory() as session:
            count = await session.scalar(select(func.count()).select_from(FoodCommunity))

        assert count == 2
        assert sum(report.entries_inserted for report in reports) == 2
        assert sum(report.entries_unchanged for report in reports) == 2
        assert not any(report.entries_rejected for report in reports)
    finally:
        await engine.dispose()


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_food_pack_loader_is_idempotent_versioned_and_partial(tmp_path: Path) -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_exercise_loader(INTEGRATION_DATABASE_URL, tmp_path))


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_concurrent_retries_do_not_duplicate_slugs() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_exercise_concurrent_retry(INTEGRATION_DATABASE_URL))
