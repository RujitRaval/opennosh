from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError
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
                            user_id, recipe_id, position, food_source_table,
                            food_source_id, food_source_key, food_name, grams,
                            computed_nutrients_json
                        ) VALUES (
                            :user_id, :recipe_id, 0, 'foods_custom', gen_random_uuid(),
                            gen_random_uuid()::text, 'Cross-tenant food', 10,
                            '{"basis":"computed","grams":"10","nutrients":{
                              "energy_kcal":"10","protein_g":"1","fat_g":"0",
                              "carbohydrate_g":"1.5"}}'::jsonb
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


async def replace_and_read_recipe_yield(database_url: str, grams: str) -> str:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE recipes SET yield_grams = CAST(:grams AS numeric)"),
                {"grams": grams},
            )
            return str(await connection.scalar(text("SELECT min(yield_grams) FROM recipes")))
    finally:
        await engine.dispose()


async def seed_pre_snapshot_recipe_ingredients(
    database_url: str,
) -> dict[str, tuple[str, str]]:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            user_id = await connection.scalar(
                text(
                    """
                    INSERT INTO users (email, password_hash)
                    VALUES ('recipe-migration@example.test', 'hash') RETURNING id
                    """
                )
            )
            food_id = await connection.scalar(
                text(
                    """
                    INSERT INTO foods_reference (
                        fdc_id, description, nutrients_json, portions_json
                    ) VALUES (
                        '98765', 'Legacy recipe oats',
                        '{"basis":"per_100g","nutrients":{
                          "energy_kcal":"100","protein_g":"10",
                          "fat_g":"0","carbohydrate_g":"15"}}'::jsonb,
                        '[]'::jsonb
                    ) RETURNING id
                    """
                )
            )
            community_id = await connection.scalar(
                text(
                    """
                    INSERT INTO foods_community (
                        pack_id, pack_version, slug, name, category, provenance,
                        source_license, nutrients_json, portions_json, contributed_by
                    ) VALUES (
                        'recipe-migration', '1.0.0', 'legacy-lentils',
                        'Legacy lentils', 'legume', 'own_measurement',
                        'contributor-original',
                        '{"basis":"per_100g","nutrients":{
                          "energy_kcal":"100","protein_g":"10",
                          "fat_g":"0","carbohydrate_g":"15"}}'::jsonb,
                        '[]'::jsonb, 'Migration Tester'
                    ) RETURNING id
                    """
                )
            )
            odbl_id = await connection.scalar(
                text(
                    """
                    INSERT INTO foods_odbl (
                        barcode, product_name, nutrients_json, source_url,
                        attribution_text
                    ) VALUES (
                        '0098765432105', 'Legacy cereal',
                        '{"basis":"per_100g","nutrients":{
                          "energy_kcal":"100","protein_g":"10",
                          "fat_g":"0","carbohydrate_g":"15"}}'::jsonb,
                        'https://example.test/legacy-cereal', 'Open Food Facts'
                    ) RETURNING id
                    """
                )
            )
            custom_id = await connection.scalar(
                text(
                    """
                    INSERT INTO foods_custom (
                        user_id, name, nutrients_json, portions_json
                    ) VALUES (
                        :user_id, 'Legacy custom food',
                        '{"basis":"per_100g","nutrients":{
                          "energy_kcal":"100","protein_g":"10",
                          "fat_g":"0","carbohydrate_g":"15"}}'::jsonb,
                        '[]'::jsonb
                    ) RETURNING id
                    """
                ),
                {"user_id": user_id},
            )
            recipe_id = await connection.scalar(
                text(
                    """
                    INSERT INTO recipes (user_id, name, yield_grams)
                    VALUES (:user_id, 'Legacy recipe', 300) RETURNING id
                    """
                ),
                {"user_id": user_id},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO recipe_ingredients (
                        user_id, recipe_id, food_source_table, food_source_id, grams
                    ) VALUES (
                        :user_id, :recipe_id, 'foods_reference', :food_id, 150
                    )
                    """
                ),
                {"user_id": user_id, "recipe_id": recipe_id, "food_id": food_id},
            )
            for source_table, source_id in (
                ("foods_community", community_id),
                ("foods_odbl", odbl_id),
                ("foods_custom", custom_id),
            ):
                await connection.execute(
                    text(
                        """
                        INSERT INTO recipe_ingredients (
                            user_id, recipe_id, food_source_table, food_source_id, grams
                        ) VALUES (:user_id, :recipe_id, :source_table, :food_id, 150)
                        """
                    ),
                    {
                        "user_id": user_id,
                        "recipe_id": recipe_id,
                        "source_table": source_table,
                        "food_id": source_id,
                    },
                )
            return {
                "foods_reference": ("98765", "Legacy recipe oats"),
                "foods_community": ("legacy-lentils", "Legacy lentils"),
                "foods_odbl": ("0098765432105", "Legacy cereal"),
                "foods_custom": (str(custom_id), "Legacy custom food"),
            }
    finally:
        await engine.dispose()


async def read_recipe_snapshot_backfill(database_url: str) -> list[dict[str, Any]]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        """
                        SELECT food_source_table, position, food_source_key, food_name,
                               grams::text, computed_nutrients_json
                        FROM recipe_ingredients
                        ORDER BY position
                        """
                    )
                )
            ).mappings().all()
            return [dict(row) for row in rows]
    finally:
        await engine.dispose()


async def seed_legacy_targets(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            user_id = await connection.scalar(
                text(
                    """
                    INSERT INTO users (email, password_hash)
                    VALUES ('legacy-target@example.test', 'hash') RETURNING id
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO targets (
                        user_id, day_type, kcal, protein_g, carb_g, fat_g, active_from
                    ) VALUES
                        (:user_id, 'training', 2400, 180, 250, 70, '2026-08-01'),
                        (:user_id, 'training', 2500, 180, 275, 70, '2026-09-01'),
                        (:user_id, 'rest', 1100, 180, 100, 60, '2026-08-01')
                    """
                ),
                {"user_id": user_id},
            )
    finally:
        await engine.dispose()


async def read_migrated_targets(database_url: str) -> list[dict[str, Any]]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        """
                        SELECT day_type, kcal::text, active_from::text,
                               active_until::text, below_floor_confirmed,
                               safety_review_required,
                               safety_floor_kcal::text
                        FROM targets
                        ORDER BY day_type, active_from
                        """
                    )
                )
            ).mappings().all()
            return [dict(row) for row in rows]
    finally:
        await engine.dispose()


async def assert_overlapping_target_is_rejected(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        user_id: object
        async with engine.connect() as connection:
            user_id = await connection.scalar(
                text("SELECT id FROM users WHERE email = 'legacy-target@example.test'")
            )
        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        """
                        INSERT INTO targets (
                            user_id, day_type, kcal, protein_g, carb_g, fat_g,
                            active_from, active_until, safety_floor_kcal
                        ) VALUES (
                            :user_id, 'training', 2450, 180, 260, 70,
                            '2026-08-15', '2026-09-15', 1200
                        )
                        """
                    ),
                    {"user_id": user_id},
                )
    finally:
        await engine.dispose()


async def assert_unconfirmed_below_floor_target_is_rejected(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            user_id = await connection.scalar(
                text(
                    """
                    INSERT INTO users (email, password_hash)
                    VALUES ('unsafe-target@example.test', 'hash') RETURNING id
                    """
                )
            )
        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        """
                        INSERT INTO targets (
                            user_id, day_type, kcal, protein_g, carb_g, fat_g,
                            active_from
                        ) VALUES (
                            :user_id, 'rest', 1100, 180, 100, 60, '2026-08-01'
                        )
                        """
                    ),
                    {"user_id": user_id},
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

        recipe_ingredient_columns = asyncio.run(
            inspect_database(
                INTEGRATION_DATABASE_URL,
                lambda inspector: inspector.get_columns("recipe_ingredients"),
            )
        )
        assert {
            "position",
            "food_source_key",
            "food_name",
            "computed_nutrients_json",
        }.issubset({column["name"] for column in recipe_ingredient_columns})
        assert all(
            not column["nullable"]
            for column in recipe_ingredient_columns
            if column["name"]
            in {"position", "food_source_key", "food_name", "computed_nutrients_json"}
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


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_recipe_snapshot_migration_preserves_existing_composition() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    config = migration_config(INTEGRATION_DATABASE_URL)

    command.downgrade(config, "base")
    try:
        command.upgrade(config, "20260820_0005")
        expected = asyncio.run(
            seed_pre_snapshot_recipe_ingredients(INTEGRATION_DATABASE_URL)
        )
        command.upgrade(config, "head")

        actual = asyncio.run(read_recipe_snapshot_backfill(INTEGRATION_DATABASE_URL))
        assert {row["position"] for row in actual} == {0, 1, 2, 3}
        for row in actual:
            assert (row["food_source_key"], row["food_name"]) == expected[
                row["food_source_table"]
            ]
            assert row["grams"] == "150.000"
            snapshot = row["computed_nutrients_json"]
            assert snapshot["basis"] == "computed"
            assert Decimal(snapshot["grams"]) == Decimal("150")
            assert {
                code: Decimal(amount) for code, amount in snapshot["nutrients"].items()
            } == {
                "energy_kcal": Decimal("150"),
                "protein_g": Decimal("15"),
                "fat_g": Decimal("0"),
                "carbohydrate_g": Decimal("22.5"),
            }
        assert (
            asyncio.run(
                replace_and_read_recipe_yield(INTEGRATION_DATABASE_URL, "0.0001")
            )
            == "0.0001"
        )
        command.downgrade(config, "20260820_0005")
        assert (
            asyncio.run(
                replace_and_read_recipe_yield(INTEGRATION_DATABASE_URL, "0.0001")
            )
            == "0.0001"
        )
    finally:
        command.downgrade(config, "base")


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_recipe_snapshot_migration_rejects_empty_legacy_recipes() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    config = migration_config(INTEGRATION_DATABASE_URL)

    command.downgrade(config, "base")
    try:
        command.upgrade(config, "20260820_0005")

        async def seed_empty_recipe() -> None:
            engine = create_async_engine(INTEGRATION_DATABASE_URL)
            try:
                async with engine.begin() as connection:
                    user_id = await connection.scalar(
                        text(
                            """
                            INSERT INTO users (email, password_hash)
                            VALUES ('empty-recipe@example.test', 'hash') RETURNING id
                            """
                        )
                    )
                    await connection.execute(
                        text(
                            """
                            INSERT INTO recipes (user_id, name, yield_grams)
                            VALUES (:user_id, 'Empty legacy recipe', 100)
                            """
                        ),
                        {"user_id": user_id},
                    )
            finally:
                await engine.dispose()

        asyncio.run(seed_empty_recipe())
        with pytest.raises(DBAPIError, match="Cannot migrate recipes without ingredients"):
            command.upgrade(config, "head")
    finally:
        command.downgrade(config, "base")


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_target_schedule_migration_preserves_and_bounds_legacy_ranges() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    config = migration_config(INTEGRATION_DATABASE_URL)

    command.downgrade(config, "base")
    try:
        command.upgrade(config, "20260820_0006")
        asyncio.run(seed_legacy_targets(INTEGRATION_DATABASE_URL))
        command.upgrade(config, "head")

        assert asyncio.run(read_migrated_targets(INTEGRATION_DATABASE_URL)) == [
            {
                "day_type": "rest",
                "kcal": "1100.00",
                "active_from": "2026-08-01",
                "active_until": None,
                "below_floor_confirmed": False,
                "safety_review_required": True,
                "safety_floor_kcal": "1200.00",
            },
            {
                "day_type": "training",
                "kcal": "2400.00",
                "active_from": "2026-08-01",
                "active_until": "2026-08-31",
                "below_floor_confirmed": False,
                "safety_review_required": True,
                "safety_floor_kcal": "1200.00",
            },
            {
                "day_type": "training",
                "kcal": "2500.00",
                "active_from": "2026-09-01",
                "active_until": None,
                "below_floor_confirmed": False,
                "safety_review_required": True,
                "safety_floor_kcal": "1200.00",
            },
        ]
        asyncio.run(assert_overlapping_target_is_rejected(INTEGRATION_DATABASE_URL))
        asyncio.run(
            assert_unconfirmed_below_floor_target_is_rejected(
                INTEGRATION_DATABASE_URL
            )
        )

        command.downgrade(config, "20260820_0006")
        target_columns = asyncio.run(
            inspect_database(
                INTEGRATION_DATABASE_URL,
                lambda inspector: {
                    column["name"] for column in inspector.get_columns("targets")
                },
            )
        )
        assert "active_until" not in target_columns
        assert "below_floor_confirmed" not in target_columns
        assert "safety_review_required" not in target_columns
        assert "safety_floor_kcal" not in target_columns
    finally:
        command.downgrade(config, "base")
