from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from datetime import UTC, datetime
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
    "food_search_snapshots",
    "food_search_snapshot_items",
    "recipes",
    "recipe_ingredients",
    "log_entries",
    "body_metrics",
    "workouts",
    "workout_sets",
    "exercises",
    "targets",
    "contribution_drafts",
    "contribution_draft_operations",
    "evidence_manifests",
    "evidence_durable_acknowledgements",
    "evidence_removal_tombstones",
    "evidence_upload_sessions",
    "federation_invitations",
    "federation_maintainers",
    "federation_releases",
    "federation_verified_releases",
    "federation_release_status_events",
    "federation_pack_installation_events",
    "federation_projection_checkpoints",
    "federation_projection_releases",
    "federation_projection_foods",
    "federation_projection_activations",
    "federation_role_keys",
    "federation_audit_events",
    "governance_role_assignments",
    "governance_recusals",
    "governance_review_cases",
    "governance_review_events",
    "governance_review_private_notes",
    "governance_decisions",
    "governance_disputes",
    "governance_appeals",
    "governance_merge_authorizations",
    "governance_publication_interventions",
    "governance_publication_pauses",
    "opennosh_pgqueuer",
    "opennosh_pgqueuer_log",
    "opennosh_pgqueuer_statistics",
    "opennosh_pgqueuer_schedules",
    "publication_intents",
    "publication_steps",
    "publication_durable_acknowledgements",
    "publication_receipts",
    "accepted_events",
    "mission_definitions",
    "mission_lifecycle_events",
    "mission_contribution_bindings",
    "mission_progress_checkpoints",
    "mission_progress_records",
    "mission_progress_activations",
    "reuse_declarations",
    "reuse_declaration_events",
    "impact_snapshots",
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
                (
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
                )
                .mappings()
                .all()
            )
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
                (
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
                )
                .mappings()
                .all()
            )
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
        expected = asyncio.run(seed_pre_snapshot_recipe_ingredients(INTEGRATION_DATABASE_URL))
        command.upgrade(config, "head")

        actual = asyncio.run(read_recipe_snapshot_backfill(INTEGRATION_DATABASE_URL))
        assert {row["position"] for row in actual} == {0, 1, 2, 3}
        for row in actual:
            assert (row["food_source_key"], row["food_name"]) == expected[row["food_source_table"]]
            assert row["grams"] == "150.000"
            snapshot = row["computed_nutrients_json"]
            assert snapshot["basis"] == "computed"
            assert Decimal(snapshot["grams"]) == Decimal("150")
            assert {code: Decimal(amount) for code, amount in snapshot["nutrients"].items()} == {
                "energy_kcal": Decimal("150"),
                "protein_g": Decimal("15"),
                "fat_g": Decimal("0"),
                "carbohydrate_g": Decimal("22.5"),
            }
        assert (
            asyncio.run(replace_and_read_recipe_yield(INTEGRATION_DATABASE_URL, "0.0001"))
            == "0.0001"
        )
        command.downgrade(config, "20260820_0005")
        assert (
            asyncio.run(replace_and_read_recipe_yield(INTEGRATION_DATABASE_URL, "0.0001"))
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
        asyncio.run(assert_unconfirmed_below_floor_target_is_rejected(INTEGRATION_DATABASE_URL))

        command.downgrade(config, "20260820_0006")
        target_columns = asyncio.run(
            inspect_database(
                INTEGRATION_DATABASE_URL,
                lambda inspector: {column["name"] for column in inspector.get_columns("targets")},
            )
        )
        assert "active_until" not in target_columns
        assert "below_floor_confirmed" not in target_columns
        assert "safety_review_required" not in target_columns
        assert "safety_floor_kcal" not in target_columns
    finally:
        command.downgrade(config, "base")


async def seed_legacy_body_metrics(database_url: str, *, invalid_kind: str | None = None) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            user_id = await connection.scalar(
                text(
                    """
                    INSERT INTO users (email, password_hash)
                    VALUES ('legacy-metric@example.test', 'hash') RETURNING id
                    """
                )
            )
            if invalid_kind is None:
                await connection.execute(
                    text(
                        """
                        INSERT INTO body_metrics (
                            user_id, recorded_at, metric_type, value, unit
                        ) VALUES
                            (:user_id, '2026-08-20T12:00:00Z', 'body_weight', 80.125, 'kg'),
                            (:user_id, '2026-08-21T12:00:00Z', 'waist_circumference', 84.2, 'cm')
                        """
                    ),
                    {"user_id": user_id},
                )
            elif invalid_kind == "contract":
                await connection.execute(
                    text(
                        """
                        INSERT INTO body_metrics (
                            user_id, recorded_at, metric_type, value, unit
                        ) VALUES (:user_id, '2026-08-20T12:00:00Z', 'weight', 80, 'percent')
                        """
                    ),
                    {"user_id": user_id},
                )
            elif invalid_kind == "timestamp":
                await connection.execute(
                    text(
                        """
                        INSERT INTO body_metrics (
                            user_id, recorded_at, metric_type, value, unit
                        ) VALUES (:user_id, 'infinity', 'body_weight', 80, 'kg')
                        """
                    ),
                    {"user_id": user_id},
                )
            elif invalid_kind == "negative_timestamp":
                await connection.execute(
                    text(
                        """
                        INSERT INTO body_metrics (
                            user_id, recorded_at, metric_type, value, unit
                        ) VALUES (:user_id, '-infinity', 'body_weight', 80, 'kg')
                        """
                    ),
                    {"user_id": user_id},
                )
            else:
                raise ValueError(f"unsupported invalid body metric kind: {invalid_kind}")
    finally:
        await engine.dispose()


async def read_body_metrics(database_url: str) -> list[dict[str, str]]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        """
                        SELECT metric_type, value, unit
                        FROM body_metrics
                        ORDER BY recorded_at
                        """
                    )
                )
            ).mappings()
            return [
                {
                    "metric_type": str(row["metric_type"]),
                    "value": format(row["value"], "f"),
                    "unit": str(row["unit"]),
                }
                for row in rows
            ]
    finally:
        await engine.dispose()


async def assert_invalid_body_metric_is_rejected(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                user_id = await connection.scalar(text("SELECT id FROM users LIMIT 1"))
                await connection.execute(
                    text(
                        """
                        INSERT INTO body_metrics (
                            user_id, recorded_at, metric_type, value, unit
                        ) VALUES (:user_id, now(), 'body_weight', 80, 'percent')
                        """
                    ),
                    {"user_id": user_id},
                )
    finally:
        await engine.dispose()


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_body_metric_migration_preserves_valid_rows_and_enforces_contract() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    config = migration_config(INTEGRATION_DATABASE_URL)

    command.downgrade(config, "base")
    try:
        command.upgrade(config, "20260820_0007")
        asyncio.run(seed_legacy_body_metrics(INTEGRATION_DATABASE_URL))
        command.upgrade(config, "head")

        assert asyncio.run(read_body_metrics(INTEGRATION_DATABASE_URL)) == [
            {"metric_type": "body_weight", "value": "80.1250", "unit": "kg"},
            {
                "metric_type": "waist_circumference",
                "value": "84.2000",
                "unit": "cm",
            },
        ]
        constraint_names = asyncio.run(
            inspect_database(
                INTEGRATION_DATABASE_URL,
                lambda inspector: {
                    constraint["name"]
                    for constraint in inspector.get_check_constraints("body_metrics")
                },
            )
        )
        assert {
            "ck_body_metrics_metric_type_allowed",
            "ck_body_metrics_unit_allowed",
            "ck_body_metrics_type_unit_valid",
            "ck_body_metrics_value_bounded",
            "ck_body_metrics_recorded_at_supported",
        }.issubset(constraint_names)
        asyncio.run(assert_invalid_body_metric_is_rejected(INTEGRATION_DATABASE_URL))

        command.downgrade(config, "20260820_0007")
        downgraded_constraint_names = asyncio.run(
            inspect_database(
                INTEGRATION_DATABASE_URL,
                lambda inspector: {
                    constraint["name"]
                    for constraint in inspector.get_check_constraints("body_metrics")
                },
            )
        )
        assert not any(name.startswith("ck_body_metrics_") for name in downgraded_constraint_names)
        assert len(asyncio.run(read_body_metrics(INTEGRATION_DATABASE_URL))) == 2
    finally:
        command.downgrade(config, "base")


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
@pytest.mark.parametrize("invalid_kind", ["contract", "timestamp", "negative_timestamp"])
def test_body_metric_migration_rejects_invalid_legacy_rows(
    invalid_kind: str,
) -> None:
    assert INTEGRATION_DATABASE_URL is not None
    config = migration_config(INTEGRATION_DATABASE_URL)

    command.downgrade(config, "base")
    try:
        command.upgrade(config, "20260820_0007")
        asyncio.run(seed_legacy_body_metrics(INTEGRATION_DATABASE_URL, invalid_kind=invalid_kind))
        with pytest.raises(DBAPIError, match="Cannot migrate invalid legacy body metrics"):
            command.upgrade(config, "head")
    finally:
        command.downgrade(config, "base")


async def seed_legacy_workout(database_url: str, *, invalid_kind: str | None = None) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            user_id = await connection.scalar(
                text(
                    """
                    INSERT INTO users (email, password_hash)
                    VALUES ('legacy-workout@example.test', 'hash') RETURNING id
                    """
                )
            )
            exercise_id = await connection.scalar(
                text(
                    """
                    INSERT INTO exercises (
                        slug, name, source, source_id, source_url, license_spdx,
                        license_url, attribution_text
                    ) VALUES (
                        'legacy-squat', 'Legacy squat', 'wger', 'legacy-1',
                        'https://example.test/exercise', 'CC-BY-SA-3.0',
                        'https://creativecommons.org/licenses/by-sa/3.0/',
                        'Legacy attribution'
                    ) RETURNING id
                    """
                )
            )
            performed_at = (
                datetime.max.replace(tzinfo=UTC)
                if invalid_kind == "timestamp"
                else datetime(2026, 8, 20, 12, tzinfo=UTC)
            )
            notes = "x" * 5001 if invalid_kind == "notes" else "legacy notes"
            workout_id = await connection.scalar(
                text(
                    """
                    INSERT INTO workouts (user_id, performed_at, notes)
                    VALUES (:user_id, CAST(:performed_at AS timestamptz), :notes)
                    RETURNING id
                    """
                ),
                {"user_id": user_id, "performed_at": performed_at, "notes": notes},
            )
            if invalid_kind == "load_unit":
                await connection.execute(
                    text(
                        "ALTER TABLE workout_sets DROP CONSTRAINT ck_workout_sets_load_unit_allowed"
                    )
                )
            set_index = 500 if invalid_kind == "set_index" else 0
            reps = 100_001 if invalid_kind == "reps" else 5
            load_value = None if invalid_kind in {"contract", "rpe_null"} else Decimal("100")
            if invalid_kind == "load_value":
                load_value = Decimal("1000000.001")
            load_unit = {
                "rpe_null": "rpe_only",
                "load_unit": "unknown",
            }.get(invalid_kind, "kg")
            await connection.execute(
                text(
                    """
                    INSERT INTO workout_sets (
                        user_id, workout_id, exercise_id, set_index, reps,
                        load_value, load_unit
                    ) VALUES (
                        :user_id, :workout_id, :exercise_id, :set_index, :reps,
                        CAST(:load_value AS numeric), :load_unit
                    )
                    """
                ),
                {
                    "user_id": user_id,
                    "workout_id": workout_id,
                    "exercise_id": exercise_id,
                    "set_index": set_index,
                    "reps": reps,
                    "load_value": load_value,
                    "load_unit": load_unit,
                },
            )
            if invalid_kind is None:
                await connection.execute(
                    text(
                        """
                        INSERT INTO workout_sets (
                            user_id, workout_id, exercise_id, set_index, reps,
                            load_value, load_unit
                        ) VALUES (
                            :user_id, :workout_id, :exercise_id, 1, 8, NULL,
                            'bodyweight'
                        )
                        """
                    ),
                    {
                        "user_id": user_id,
                        "workout_id": workout_id,
                        "exercise_id": exercise_id,
                    },
                )
    finally:
        await engine.dispose()


async def read_legacy_workout(database_url: str) -> list[dict[str, str | None]]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        """
                        SELECT ws.set_index, ws.reps, ws.load_value, ws.load_unit
                        FROM workout_sets ws
                        JOIN workouts w ON w.id = ws.workout_id
                        WHERE w.notes = 'legacy notes'
                        ORDER BY ws.set_index
                        """
                    )
                )
            ).mappings()
            return [
                {
                    "set_index": str(row["set_index"]),
                    "reps": str(row["reps"]),
                    "load_value": (
                        None if row["load_value"] is None else format(row["load_value"], "f")
                    ),
                    "load_unit": str(row["load_unit"]),
                }
                for row in rows
            ]
    finally:
        await engine.dispose()


async def assert_invalid_workout_sets_are_rejected(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        for position, load_value, load_unit in (
            (2, Decimal("1"), "bodyweight"),
            (3, None, "rpe_only"),
        ):
            with pytest.raises(IntegrityError):
                async with engine.begin() as connection:
                    row = (
                        (
                            await connection.execute(
                                text(
                                    """
                                SELECT w.user_id, w.id AS workout_id, ws.exercise_id
                                FROM workouts w
                                JOIN workout_sets ws ON ws.workout_id = w.id
                                LIMIT 1
                                """
                                )
                            )
                        )
                        .mappings()
                        .one()
                    )
                    await connection.execute(
                        text(
                            """
                            INSERT INTO workout_sets (
                                user_id, workout_id, exercise_id, set_index, reps,
                                load_value, load_unit
                            ) VALUES (
                                :user_id, :workout_id, :exercise_id, :position, 8,
                                CAST(:load_value AS numeric), :load_unit
                            )
                            """
                        ),
                        {
                            **dict(row),
                            "position": position,
                            "load_value": load_value,
                            "load_unit": load_unit,
                        },
                    )
    finally:
        await engine.dispose()


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_workout_migration_preserves_valid_rows_and_enforces_contract() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    config = migration_config(INTEGRATION_DATABASE_URL)

    command.downgrade(config, "base")
    try:
        command.upgrade(config, "20260820_0008")
        asyncio.run(seed_legacy_workout(INTEGRATION_DATABASE_URL))
        command.upgrade(config, "head")

        assert asyncio.run(read_legacy_workout(INTEGRATION_DATABASE_URL)) == [
            {
                "set_index": "0",
                "reps": "5",
                "load_value": "100.000",
                "load_unit": "kg",
            },
            {
                "set_index": "1",
                "reps": "8",
                "load_value": None,
                "load_unit": "bodyweight",
            },
        ]
        workout_constraints = asyncio.run(
            inspect_database(
                INTEGRATION_DATABASE_URL,
                lambda inspector: {
                    constraint["name"] for constraint in inspector.get_check_constraints("workouts")
                },
            )
        )
        set_constraints = asyncio.run(
            inspect_database(
                INTEGRATION_DATABASE_URL,
                lambda inspector: {
                    constraint["name"]
                    for constraint in inspector.get_check_constraints("workout_sets")
                },
            )
        )
        assert {
            "ck_workouts_performed_at_supported",
            "ck_workouts_notes_bounded",
        }.issubset(workout_constraints)
        assert {
            "ck_workout_sets_set_index_bounded",
            "ck_workout_sets_reps_bounded",
            "ck_workout_sets_load_value_bounded",
            "ck_workout_sets_load_contract_valid",
        }.issubset(set_constraints)
        asyncio.run(assert_invalid_workout_sets_are_rejected(INTEGRATION_DATABASE_URL))

        command.downgrade(config, "20260820_0008")
        downgraded = asyncio.run(
            inspect_database(
                INTEGRATION_DATABASE_URL,
                lambda inspector: {
                    constraint["name"]
                    for constraint in inspector.get_check_constraints("workout_sets")
                },
            )
        )
        assert "ck_workout_sets_load_contract_valid" not in downgraded
        assert len(asyncio.run(read_legacy_workout(INTEGRATION_DATABASE_URL))) == 2
    finally:
        command.downgrade(config, "base")


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
@pytest.mark.parametrize(
    "invalid_kind",
    [
        "contract",
        "rpe_null",
        "timestamp",
        "notes",
        "set_index",
        "reps",
        "load_value",
        "load_unit",
    ],
)
def test_workout_migration_rejects_invalid_legacy_rows(invalid_kind: str) -> None:
    assert INTEGRATION_DATABASE_URL is not None
    config = migration_config(INTEGRATION_DATABASE_URL)

    command.downgrade(config, "base")
    try:
        command.upgrade(config, "20260820_0008")
        asyncio.run(seed_legacy_workout(INTEGRATION_DATABASE_URL, invalid_kind=invalid_kind))
        with pytest.raises(DBAPIError, match="Cannot migrate invalid legacy workouts"):
            command.upgrade(config, "head")
    finally:
        command.downgrade(config, "base")


async def seed_legacy_exercise(database_url: str, *, invalid_kind: str | None = None) -> None:
    engine = create_async_engine(database_url)
    slug = "legacy-wger-squat" if invalid_kind is None else f"invalid-wger-{invalid_kind}"
    source_id = "legacy-101" if invalid_kind is None else f"invalid-{invalid_kind}"
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO exercises (
                        slug, name, muscle_groups, equipment, source, source_id,
                        source_url, derivative_source_url, license_spdx, license_url,
                        author, author_url, attribution_text,
                        translation_attribution_json
                    ) VALUES (
                        :slug, 'Legacy wger squat', '["quads"]'::jsonb,
                        '["barbell"]'::jsonb, 'wger', :source_id, :source_url,
                        'https://example.test/source', :license_spdx,
                        'https://creativecommons.org/licenses/by-sa/3.0/',
                        'Legacy author', :author_url, 'Legacy attribution',
                        CAST(:translation_attribution AS jsonb)
                    )
                    """
                ),
                {
                    "slug": slug,
                    "source_id": source_id,
                    "source_url": (
                        "javascript:alert(1)"
                        if invalid_kind == "source_url"
                        else "https://?bad"
                        if invalid_kind == "source_missing_host"
                        else "https://wger.de:bad/item"
                        if invalid_kind == "source_bad_port"
                        else "https://wger.de\\evil"
                        if invalid_kind == "source_backslash"
                        else "https://" + "user" + ":" + "pass" + "@wger.de/item/101/"
                        if invalid_kind == "source_credentials"
                        else "https://wger.de/api/v2/exerciseinfo/101/"
                    ),
                    "license_spdx": (
                        "CC-BY-SA-4.0" if invalid_kind == "license" else "CC-BY-SA-3.0"
                    ),
                    "author_url": (
                        "javascript:alert(1)"
                        if invalid_kind == "author_url"
                        else "https://wger.de/"
                    ),
                    "translation_attribution": (
                        '[{"source_id":"101","language_id":"2",'
                        '"license_spdx":"CC-BY-SA-3.0",'
                        '"license_url":"javascript:alert(1)",'
                        '"author":"wger","attribution_text":"credit"}]'
                        if invalid_kind == "translation_url"
                        else "[]"
                    ),
                },
            )
    finally:
        await engine.dispose()


async def read_legacy_exercise(database_url: str) -> dict[str, object] | None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            """
                        SELECT slug, search_text, translations_json, source_updated_at
                        FROM exercises
                        WHERE source = 'wger' AND source_id = 'legacy-101'
                        """
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            return dict(row) if row is not None else None
    finally:
        await engine.dispose()


async def write_invalid_exercise_value(database_url: str, invalid_kind: str) -> None:
    statements = {
        "source_url": "UPDATE exercises SET source_url = 'https://?bad'",
        "author_url": "UPDATE exercises SET author_url = 'javascript:alert(1)'",
        "timestamp": (
            "UPDATE exercises SET source_updated_at = TIMESTAMPTZ '9999-12-31 23:59:59.999999+00'"
        ),
        "muscle_type": "UPDATE exercises SET muscle_groups = '[1]'::jsonb",
        "muscle_markup": "UPDATE exercises SET muscle_groups = '[\"<img>\"]'::jsonb",
        "translation_type": "UPDATE exercises SET translations_json = '[1]'::jsonb",
    }
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(f"{statements[invalid_kind]} WHERE source_id = 'legacy-101'")
            )
    finally:
        await engine.dispose()


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_wger_catalogue_migration_preserves_rows_enforces_contract_and_downgrades() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    config = migration_config(INTEGRATION_DATABASE_URL)

    command.downgrade(config, "base")
    try:
        command.upgrade(config, "20260820_0009")
        asyncio.run(seed_legacy_exercise(INTEGRATION_DATABASE_URL))
        command.upgrade(config, "head")

        row = asyncio.run(read_legacy_exercise(INTEGRATION_DATABASE_URL))
        assert row is not None
        assert row["slug"] == "legacy-wger-squat"
        assert row["search_text"] == ""
        assert row["translations_json"] == []
        assert row["source_updated_at"] is None
        constraints = asyncio.run(
            inspect_database(
                INTEGRATION_DATABASE_URL,
                lambda inspector: {
                    constraint["name"]
                    for constraint in inspector.get_check_constraints("exercises")
                },
            )
        )
        indexes = asyncio.run(
            inspect_database(
                INTEGRATION_DATABASE_URL,
                lambda inspector: {index["name"] for index in inspector.get_indexes("exercises")},
            )
        )
        assert {
            "ck_exercises_wger_license_allowed",
            "ck_exercises_source_url_http",
            "ck_exercises_author_url_http",
            "ck_exercises_translations_array",
            "ck_exercises_muscles_strings",
            "ck_exercises_muscles_plain",
            "ck_exercises_translations_objects",
            "ck_exercises_source_updated_at_supported",
        }.issubset(constraints)
        assert {
            "ix_exercises_search_tsv",
            "ix_exercises_name_trgm",
            "ix_exercises_muscle_groups_gin",
            "ix_exercises_equipment_gin",
        }.issubset(indexes)

        with pytest.raises(IntegrityError):
            asyncio.run(seed_legacy_exercise(INTEGRATION_DATABASE_URL, invalid_kind="license"))
        for invalid_kind in (
            "source_url",
            "author_url",
            "timestamp",
            "muscle_type",
            "muscle_markup",
            "translation_type",
        ):
            with pytest.raises(IntegrityError):
                asyncio.run(write_invalid_exercise_value(INTEGRATION_DATABASE_URL, invalid_kind))

        command.downgrade(config, "20260820_0009")
        columns = asyncio.run(
            inspect_database(
                INTEGRATION_DATABASE_URL,
                lambda inspector: {column["name"] for column in inspector.get_columns("exercises")},
            )
        )
        assert "translations_json" not in columns
        assert "search_text" not in columns
        engine = create_async_engine(INTEGRATION_DATABASE_URL)

        async def legacy_count() -> int:
            try:
                async with engine.connect() as connection:
                    value = await connection.scalar(
                        text("SELECT count(*) FROM exercises WHERE source_id = 'legacy-101'")
                    )
                    return int(value or 0)
            finally:
                await engine.dispose()

        assert asyncio.run(legacy_count()) == 1
    finally:
        command.downgrade(config, "base")


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
@pytest.mark.parametrize(
    "invalid_kind",
    [
        "license",
        "source_url",
        "source_missing_host",
        "source_bad_port",
        "source_backslash",
        "source_credentials",
        "author_url",
        "translation_url",
    ],
)
def test_wger_catalogue_migration_rejects_invalid_legacy_rows(invalid_kind: str) -> None:
    assert INTEGRATION_DATABASE_URL is not None
    config = migration_config(INTEGRATION_DATABASE_URL)

    command.downgrade(config, "base")
    try:
        command.upgrade(config, "20260820_0009")
        asyncio.run(seed_legacy_exercise(INTEGRATION_DATABASE_URL, invalid_kind=invalid_kind))
        with pytest.raises(DBAPIError, match="Cannot migrate invalid legacy exercise"):
            command.upgrade(config, "head")
    finally:
        command.downgrade(config, "base")


async def service_principal_migration_checks(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            person = (
                await connection.execute(
                    text(
                        """
                        SELECT actor_kind, login_disabled_at
                        FROM users WHERE email = 'existing-person@example.test'
                        """
                    )
                )
            ).one()
        assert tuple(person) == ("person", None)

        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        """
                        INSERT INTO users (
                            email, password_hash, actor_kind, login_disabled_at
                        ) VALUES (
                            'invalid-service@actors.opennosh.invalid', 'hash',
                            'service', NULL
                        )
                        """
                    )
                )

        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO users (
                        email, password_hash, recovery_token_hash,
                        actor_kind, login_disabled_at
                    ) VALUES (
                        'valid-service@actors.opennosh.invalid', 'hash', NULL,
                        'service', now()
                    )
                    """
                )
            )
    finally:
        await engine.dispose()


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_service_principal_migration_preserves_people_enforces_and_downgrades() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    config = migration_config(INTEGRATION_DATABASE_URL)
    command.downgrade(config, "base")
    try:
        command.upgrade(config, "20260826_0018")
        engine = create_async_engine(INTEGRATION_DATABASE_URL)

        async def seed_person() -> None:
            try:
                async with engine.begin() as connection:
                    await connection.execute(
                        text(
                            """
                            INSERT INTO users (email, password_hash)
                            VALUES ('existing-person@example.test', 'hash')
                            """
                        )
                    )
            finally:
                await engine.dispose()

        asyncio.run(seed_person())
        command.upgrade(config, "head")
        asyncio.run(service_principal_migration_checks(INTEGRATION_DATABASE_URL))

        constraints = asyncio.run(
            inspect_database(
                INTEGRATION_DATABASE_URL,
                lambda inspector: {
                    constraint["name"] for constraint in inspector.get_check_constraints("users")
                },
            )
        )
        assert {
            "ck_users_actor_kind_allowed",
            "ck_users_service_login_disabled",
        }.issubset(constraints)

        command.downgrade(config, "20260826_0018")
        columns = asyncio.run(
            inspect_database(
                INTEGRATION_DATABASE_URL,
                lambda inspector: {column["name"] for column in inspector.get_columns("users")},
            )
        )
        assert "actor_kind" not in columns
        assert "login_disabled_at" not in columns
    finally:
        command.downgrade(config, "base")


async def assert_evidence_upload_insert_state_shape(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            user_id = await connection.scalar(
                text(
                    "INSERT INTO users (email, password_hash) "
                    "VALUES ('upload-migration@example.test', 'hash') RETURNING id"
                )
            )
            draft_id = await connection.scalar(
                text(
                    "INSERT INTO contribution_drafts (user_id, client_draft_id) "
                    "VALUES (:user_id, 'upload-migration') RETURNING id"
                ),
                {"user_id": user_id},
            )

        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        """
                        INSERT INTO evidence_upload_sessions (
                            user_id, draft_id, source_draft_version, state, object_key,
                            declared_media_type, declared_byte_length, capability_hash,
                            idempotency_key_hash, request_hash, expires_at
                        ) VALUES (
                            :user_id, :draft_id, 1, 'uploaded',
                            'quarantine/00000000-0000-4000-8000-000000000001',
                            'image/png', 8, repeat('a', 64), repeat('b', 64),
                            repeat('c', 64), now() + interval '5 minutes'
                        )
                        """
                    ),
                    {"user_id": user_id, "draft_id": draft_id},
                )
    finally:
        await engine.dispose()


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_evidence_upload_migration_enforces_insert_shape_and_downgrades() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    config = migration_config(INTEGRATION_DATABASE_URL)
    command.downgrade(config, "base")
    try:
        command.upgrade(config, "20260829_0021")
        command.upgrade(config, "20260831_0022")
        asyncio.run(assert_evidence_upload_insert_state_shape(INTEGRATION_DATABASE_URL))
        command.downgrade(config, "20260829_0021")
        tables = asyncio.run(
            inspect_database(
                INTEGRATION_DATABASE_URL,
                lambda inspector: set(inspector.get_table_names()),
            )
        )
        assert "evidence_upload_sessions" not in tables
    finally:
        command.downgrade(config, "base")


async def seed_uploaded_evidence_session(database_url: str) -> str:
    engine = create_async_engine(database_url)
    upload_id = "00000000-0000-4000-8000-000000000023"
    try:
        async with engine.begin() as connection:
            user_id = await connection.scalar(
                text(
                    "INSERT INTO users (email, password_hash) "
                    "VALUES ('sanitization-migration@example.test', 'hash') RETURNING id"
                )
            )
            draft_id = await connection.scalar(
                text(
                    "INSERT INTO contribution_drafts (user_id, client_draft_id) "
                    "VALUES (:user_id, 'sanitization-migration') RETURNING id"
                ),
                {"user_id": user_id},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO evidence_upload_sessions (
                        id, user_id, draft_id, source_draft_version, object_key,
                        declared_media_type, declared_byte_length, capability_hash,
                        idempotency_key_hash, request_hash, expires_at
                    ) VALUES (
                        :upload_id, :user_id, :draft_id, 1,
                        'quarantine/00000000-0000-4000-8000-000000000023',
                        'image/png', 8, repeat('a', 64), repeat('b', 64),
                        repeat('c', 64), now() + interval '5 minutes'
                    )
                    """
                ),
                {"upload_id": upload_id, "user_id": user_id, "draft_id": draft_id},
            )
            await connection.execute(
                text(
                    """
                    UPDATE evidence_upload_sessions
                    SET state = 'uploaded', observed_byte_length = 8,
                        observed_sha256 = repeat('d', 64), uploaded_at = now(),
                        version = version + 1, updated_at = now()
                    WHERE id = :upload_id
                    """
                ),
                {"upload_id": upload_id},
            )
        return upload_id
    finally:
        await engine.dispose()


async def assert_evidence_sanitization_workflow(database_url: str, upload_id: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE evidence_upload_sessions
                    SET state = 'sanitizing', version = version + 1, updated_at = now()
                    WHERE id = :upload_id
                    """
                ),
                {"upload_id": upload_id},
            )

        with pytest.raises(DBAPIError):
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        """
                        UPDATE evidence_upload_sessions
                        SET state = 'sanitized', observed_byte_length = 9,
                            sanitized_object_key = 'sanitized/' || repeat('e', 64) || '.png',
                            sanitized_media_type = 'image/png', sanitized_byte_length = 7,
                            sanitized_sha256 = repeat('e', 64), sanitized_width = 1,
                            sanitized_height = 1, sanitized_at = now(),
                            version = version + 1, updated_at = now()
                        WHERE id = :upload_id
                        """
                    ),
                    {"upload_id": upload_id},
                )

        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        """
                        INSERT INTO evidence_upload_sessions (
                            id, user_id, draft_id, source_draft_version, state, object_key,
                            declared_media_type, declared_byte_length, capability_hash,
                            idempotency_key_hash, request_hash, expires_at, preserved_at
                        )
                        SELECT '00000000-0000-4000-8000-000000000024', user_id, draft_id, 1,
                            'initiated',
                            'quarantine/00000000-0000-4000-8000-000000000024',
                            'image/png', 8, repeat('1', 64), repeat('2', 64),
                            repeat('3', 64), now() + interval '5 minutes', now()
                        FROM evidence_upload_sessions WHERE id = :upload_id
                        """
                    ),
                    {"upload_id": upload_id},
                )

        async with engine.begin() as connection:
            row = await connection.execute(
                text(
                    """
                    UPDATE evidence_upload_sessions
                    SET state = 'sanitized',
                        sanitized_object_key = 'sanitized/' || repeat('e', 64) || '.png',
                        sanitized_media_type = 'image/png', sanitized_byte_length = 7,
                        sanitized_sha256 = repeat('e', 64), sanitized_width = 1,
                        sanitized_height = 1, sanitized_at = now(),
                        version = version + 1, updated_at = now()
                    WHERE id = :upload_id
                    RETURNING draft_id, sanitized_at
                    """
                ),
                {"upload_id": upload_id},
            )
            draft_id, sanitized_at = row.one()
            evidence_id = await connection.scalar(
                text(
                    """
                    INSERT INTO evidence_manifests (
                        source_draft_id, source_draft_version, schema_version,
                        evidence_class, manifest_digest, manifest_json
                    ) VALUES (
                        :draft_id, 1, '1.0', 'sanitized_media', repeat('f', 64), '{}'::jsonb
                    ) RETURNING id
                    """
                ),
                {"draft_id": draft_id},
            )
            await connection.execute(
                text(
                    """
                    UPDATE evidence_upload_sessions
                    SET state = 'attached', attached_evidence_id = :evidence_id,
                        attached_at = :sanitized_at, version = version + 1,
                        updated_at = :sanitized_at
                    WHERE id = :upload_id
                    """
                ),
                {
                    "upload_id": upload_id,
                    "evidence_id": evidence_id,
                    "sanitized_at": sanitized_at,
                },
            )
            state = await connection.scalar(
                text(
                    """
                    UPDATE evidence_upload_sessions
                    SET state = 'preserved', preserved_at = :sanitized_at,
                        version = version + 1, updated_at = :sanitized_at
                    WHERE id = :upload_id RETURNING state
                    """
                ),
                {"upload_id": upload_id, "sanitized_at": sanitized_at},
            )
            assert state == "preserved"
    finally:
        await engine.dispose()


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_evidence_sanitization_migration_preserves_uploaded_rows_and_downgrades() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    config = migration_config(INTEGRATION_DATABASE_URL)
    command.downgrade(config, "base")
    try:
        command.upgrade(config, "20260831_0022")
        upload_id = asyncio.run(seed_uploaded_evidence_session(INTEGRATION_DATABASE_URL))
        command.upgrade(config, "20260901_0023")
        columns = asyncio.run(
            inspect_database(
                INTEGRATION_DATABASE_URL,
                lambda inspector: {
                    column["name"] for column in inspector.get_columns("evidence_upload_sessions")
                },
            )
        )
        assert {
            "sanitized_object_key",
            "sanitized_sha256",
            "attached_evidence_id",
            "preserved_at",
        }.issubset(columns)
        asyncio.run(assert_evidence_sanitization_workflow(INTEGRATION_DATABASE_URL, upload_id))
        command.downgrade(config, "20260831_0022")
        columns = asyncio.run(
            inspect_database(
                INTEGRATION_DATABASE_URL,
                lambda inspector: {
                    column["name"] for column in inspector.get_columns("evidence_upload_sessions")
                },
            )
        )
        assert "sanitized_object_key" not in columns
        assert "attached_evidence_id" not in columns
    finally:
        command.downgrade(config, "base")
