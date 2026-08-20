from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

INTEGRATION_DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL")

EXPECTED_TABLES = {
    "alembic_version",
    "auth_rate_limits",
    "auth_sessions",
    "users",
    "foods_reference",
    "foods_community",
    "foods_odbl",
    "foods_custom",
    "recipes",
    "recipe_ingredients",
    "log_entries",
    "body_metrics",
    "workouts",
    "workout_sets",
    "exercises",
    "targets",
}


def migration_config(database_url: str) -> Config:
    config = Config("api/alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


async def inspect_database(database_url: str, inspector_fn: Callable[[Any], Any]) -> Any:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(
                lambda sync_connection: inspector_fn(inspect(sync_connection))
            )
    finally:
        await engine.dispose()


async def assert_database_constraints(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        """
                        INSERT INTO foods_community (
                            pack_id, pack_version, slug, name, category, provenance,
                            source_uri, source_license, nutrients_json, portions_json,
                            contributed_by
                        ) VALUES (
                            'test-pack', '1', 'bad-license', 'Bad License', 'test',
                            'own_measurement', 'https://example.test/source', 'proprietary',
                            '{}'::jsonb, '[]'::jsonb, 'tester'
                        )
                        """
                    )
                )

        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO foods_community (
                        pack_id, pack_version, slug, name, category, provenance,
                        source_uri, source_license, nutrients_json, portions_json,
                        contributed_by
                    ) VALUES (
                        'test-pack', '1', 'original-recipe', 'Original Recipe', 'test',
                        'own_measurement', NULL, 'contributor-original',
                        '{}'::jsonb, '[]'::jsonb, 'tester'
                    )
                    """
                )
            )

        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        """
                        INSERT INTO foods_custom (name, nutrients_json, portions_json)
                        VALUES ('Ownerless', '{}'::jsonb, '[]'::jsonb)
                        """
                    )
                )

        async with engine.begin() as connection:
            first_user_id = await connection.scalar(
                text(
                    """
                    INSERT INTO users (email, password_hash)
                    VALUES ('first@example.test', 'hash') RETURNING id
                    """
                )
            )
            second_user_id = await connection.scalar(
                text(
                    """
                    INSERT INTO users (email, password_hash)
                    VALUES ('second@example.test', 'hash') RETURNING id
                    """
                )
            )
            recipe_id = await connection.scalar(
                text(
                    """
                    INSERT INTO recipes (user_id, name, yield_grams)
                    VALUES (:user_id, 'Private recipe', 100) RETURNING id
                    """
                ),
                {"user_id": first_user_id},
            )

        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        """
                        INSERT INTO recipe_ingredients (
                            user_id, recipe_id, food_source_table, food_source_id, grams
                        ) VALUES (
                            :user_id, :recipe_id, 'foods_custom', gen_random_uuid(), 10
                        )
                        """
                    ),
                    {"user_id": second_user_id, "recipe_id": recipe_id},
                )
    finally:
        await engine.dispose()


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_initial_migration_upgrades_and_downgrades_cleanly() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    config = migration_config(INTEGRATION_DATABASE_URL)

    command.downgrade(config, "base")
    try:
        command.upgrade(config, "head")
        command.check(config)
        table_names = asyncio.run(
            inspect_database(
                INTEGRATION_DATABASE_URL,
                lambda inspector: set(inspector.get_table_names()),
            )
        )
        assert table_names == EXPECTED_TABLES

        community_indexes = asyncio.run(
            inspect_database(
                INTEGRATION_DATABASE_URL,
                lambda inspector: inspector.get_indexes("foods_community"),
            )
        )
        assert any(index["column_names"] == ["pack_id"] for index in community_indexes)

        for table_name in (
            "auth_sessions",
            "foods_custom",
            "recipes",
            "recipe_ingredients",
            "log_entries",
            "body_metrics",
            "workouts",
            "workout_sets",
            "targets",
        ):
            indexes = asyncio.run(
                inspect_database(
                    INTEGRATION_DATABASE_URL,
                    lambda inspector, name=table_name: inspector.get_indexes(name),
                )
            )
            assert any("user_id" in index["column_names"] for index in indexes)

            foreign_keys = asyncio.run(
                inspect_database(
                    INTEGRATION_DATABASE_URL,
                    lambda inspector, name=table_name: inspector.get_foreign_keys(name),
                )
            )
            assert any(
                foreign_key["referred_table"] == "users"
                and "user_id" in foreign_key["constrained_columns"]
                for foreign_key in foreign_keys
            )

        asyncio.run(assert_database_constraints(INTEGRATION_DATABASE_URL))
    finally:
        command.downgrade(config, "base")

    remaining_tables = asyncio.run(
        inspect_database(
            INTEGRATION_DATABASE_URL,
            lambda inspector: set(inspector.get_table_names()),
        )
    )
    assert remaining_tables == {"alembic_version"}
