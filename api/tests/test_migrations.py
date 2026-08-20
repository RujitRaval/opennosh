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


async def seed_pre_snapshot_log_entries(database_url: str) -> dict[str, tuple[str, str]]:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            user_id = await connection.scalar(
                text(
                    """
                    INSERT INTO users (email, password_hash)
                    VALUES ('migration@example.test', 'hash') RETURNING id
                    """
                )
            )
            sources = {
                "foods_reference": (
                    await connection.scalar(
                        text(
                            """
                            INSERT INTO foods_reference (
                                fdc_id, description, nutrients_json, portions_json
                            ) VALUES ('12345', 'Legacy oats', '{}'::jsonb, '[]'::jsonb)
                            RETURNING id
                            """
                        )
                    ),
                    "12345",
                    "Legacy oats",
                ),
                "foods_community": (
                    await connection.scalar(
                        text(
                            """
                            INSERT INTO foods_community (
                                pack_id, pack_version, slug, name, category, provenance,
                                source_license, nutrients_json, portions_json, contributed_by
                            ) VALUES (
                                'migration-pack', '1.0.0', 'legacy-dal', 'Legacy dal',
                                'meal', 'own_measurement', 'contributor-original',
                                '{}'::jsonb, '[]'::jsonb, 'Migration Tester'
                            ) RETURNING id
                            """
                        )
                    ),
                    "legacy-dal",
                    "Legacy dal",
                ),
                "foods_odbl": (
                    await connection.scalar(
                        text(
                            """
                            INSERT INTO foods_odbl (
                                barcode, product_name, nutrients_json, source_url,
                                attribution_text
                            ) VALUES (
                                '0012345678905', 'Legacy cereal', '{}'::jsonb,
                                'https://example.test/product', 'Open Food Facts'
                            ) RETURNING id
                            """
                        )
                    ),
                    "0012345678905",
                    "Legacy cereal",
                ),
                "foods_custom": (
                    await connection.scalar(
                        text(
                            """
                            INSERT INTO foods_custom (
                                user_id, name, nutrients_json, portions_json
                            ) VALUES (
                                :user_id, 'Legacy private food', '{}'::jsonb, '[]'::jsonb
                            ) RETURNING id
                            """
                        ),
                        {"user_id": user_id},
                    ),
                    None,
                    "Legacy private food",
                ),
            }
            snapshot = (
                '{"basis":"computed","grams":"50","nutrients":'
                '{"energy_kcal":"50","protein_g":"5","fat_g":"0",'
                '"carbohydrate_g":"7.5"}}'
            )
            for source_table, (source_id, _, _) in sources.items():
                await connection.execute(
                    text(
                        """
                        INSERT INTO log_entries (
                            user_id, logged_at, meal_slot, food_source_table,
                            food_source_id, grams, computed_nutrients_json
                        ) VALUES (
                            :user_id, '2026-08-20T12:00:00Z', 'lunch', :source_table,
                            :source_id, 50, CAST(:snapshot AS jsonb)
                        )
                        """
                    ),
                    {
                        "user_id": user_id,
                        "source_table": source_table,
                        "source_id": source_id,
                        "snapshot": snapshot,
                    },
                )
            return {
                source_table: (
                    expected_key if expected_key is not None else str(source_id),
                    expected_name,
                )
                for source_table, (source_id, expected_key, expected_name) in sources.items()
            }
    finally:
        await engine.dispose()


async def read_snapshot_backfill(database_url: str) -> dict[str, tuple[str, str, str, str]]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        """
                        SELECT food_source_table, food_source_key, food_name,
                               quantity_amount::text, quantity_unit
                        FROM log_entries
                        """
                    )
                )
            ).mappings()
            return {
                row["food_source_table"]: (
                    row["food_source_key"],
                    row["food_name"],
                    row["quantity_amount"],
                    row["quantity_unit"],
                )
                for row in rows
            }
    finally:
        await engine.dispose()


async def replace_and_read_log_grams(database_url: str, grams: str) -> str:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE log_entries SET grams = CAST(:grams AS numeric)"),
                {"grams": grams},
            )
            return str(await connection.scalar(text("SELECT min(grams) FROM log_entries")))
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
        community_index_names = {index["name"] for index in community_indexes}
        assert {
            "ix_foods_community_search_tsv",
            "ix_foods_community_slug_trgm",
            "ix_foods_community_name_trgm",
            "ix_foods_community_name_local_trgm",
        }.issubset(community_index_names)

        reference_indexes = asyncio.run(
            inspect_database(
                INTEGRATION_DATABASE_URL,
                lambda inspector: inspector.get_indexes("foods_reference"),
            )
        )
        assert {
            "ix_foods_reference_search_tsv",
            "ix_foods_reference_description_trgm",
        }.issubset({index["name"] for index in reference_indexes})

        log_columns = asyncio.run(
            inspect_database(
                INTEGRATION_DATABASE_URL,
                lambda inspector: inspector.get_columns("log_entries"),
            )
        )
        assert {
            "food_source_key",
            "food_name",
            "quantity_amount",
            "quantity_unit",
            "portion_name",
        }.issubset({column["name"] for column in log_columns})
        assert all(
            not column["nullable"]
            for column in log_columns
            if column["name"]
            in {"food_source_key", "food_name", "quantity_amount", "quantity_unit"}
        )

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


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_log_snapshot_migration_preserves_external_food_identity() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    config = migration_config(INTEGRATION_DATABASE_URL)

    command.downgrade(config, "base")
    try:
        command.upgrade(config, "20260820_0004")
        expected = asyncio.run(seed_pre_snapshot_log_entries(INTEGRATION_DATABASE_URL))
        command.upgrade(config, "head")

        actual = asyncio.run(read_snapshot_backfill(INTEGRATION_DATABASE_URL))
        assert actual == {
            source_table: (source_key, source_name, "50.000", "g")
            for source_table, (source_key, source_name) in expected.items()
        }
        assert (
            asyncio.run(replace_and_read_log_grams(INTEGRATION_DATABASE_URL, "0.0001")) == "0.0001"
        )
        command.downgrade(config, "20260820_0004")
        assert (
            asyncio.run(replace_and_read_log_grams(INTEGRATION_DATABASE_URL, "0.0001")) == "0.0001"
        )
    finally:
        command.downgrade(config, "base")
