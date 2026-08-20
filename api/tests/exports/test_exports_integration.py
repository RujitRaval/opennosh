from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from alembic import command
from fastapi.testclient import TestClient
from opennosh_api.main import create_app
from opennosh_api.settings import Settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from api.tests.test_migrations import migration_config

INTEGRATION_DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL")

NUTRIENTS = {
    "basis": "per_100g",
    "nutrients": {
        "energy_kcal": "125",
        "protein_g": "7",
        "carbohydrate_g": "18",
        "fat_g": "3",
    },
}


@dataclass(frozen=True)
class ExportClients:
    owner: TestClient
    attacker: TestClient
    anonymous: TestClient
    owner_id: str
    attacker_id: str


async def _reset(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "TRUNCATE users, foods_reference, foods_community, foods_odbl, "
                    "exercises, auth_rate_limits CASCADE"
                )
            )
    finally:
        await engine.dispose()


async def _seed(database_url: str, owner_id: str, attacker_id: str) -> None:
    engine = create_async_engine(database_url)
    payload = json.dumps(NUTRIENTS)
    snapshot = json.dumps(
        {
            "grams": "100",
            "nutrients": NUTRIENTS["nutrients"],
        }
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE users SET settings_json = "
                    "jsonb_build_object('timezone', 'America/New_York') "
                    "WHERE id = CAST(:owner_id AS uuid)"
                ),
                {"owner_id": owner_id},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO foods_community (
                        pack_id, pack_version, slug, name, name_local, locale,
                        category, provenance, source_uri, source_license,
                        source_note, nutrients_json, portions_json, pack_license,
                        contributed_by
                    ) VALUES (
                        'south-asian-staples', '1.2.3', 'fixture-dal', 'Fixture dal',
                        'परीक्षण दाल', 'hi-IN', 'legume', 'own_measurement',
                        'https://example.test/fixture-dal', 'contributor-original',
                        'Measured by the contributor', CAST(:nutrients AS jsonb),
                        '[{"name":"1 bowl","grams":"180"}]'::jsonb,
                        'CC0-1.0', 'Visible Contributor'
                    )
                    """
                ),
                {"nutrients": payload},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO foods_odbl (
                        barcode, product_name, brand, nutrients_json, source_url,
                        attribution_text
                    ) VALUES (
                        '0012345678905', 'ODbL fixture', 'Fixture Brand',
                        CAST(:nutrients AS jsonb),
                        'https://world.openfoodfacts.org/product/0012345678905',
                        'Open Food Facts contributors'
                    )
                    """
                ),
                {"nutrients": payload},
            )
            exercise_id = await connection.scalar(
                text(
                    """
                    INSERT INTO exercises (
                        slug, name, muscle_groups, equipment, search_text, source,
                        source_id, source_url, license_spdx, license_url, author,
                        attribution_text, translations_json,
                        translation_attribution_json
                    ) VALUES (
                        'wger-fixture-squat', 'Fixture squat', '["quadriceps"]'::jsonb,
                        '["barbell"]'::jsonb, 'fixture squat quadriceps barbell',
                        'wger', '9001', 'https://wger.de/api/v2/exerciseinfo/9001/',
                        'CC-BY-SA-3.0',
                        'https://creativecommons.org/licenses/by-sa/3.0/',
                        'wger contributors', 'wger contributors, CC BY-SA 3.0',
                        '[]'::jsonb, '[]'::jsonb
                    ) RETURNING id
                    """
                )
            )
            for user_id, label in ((owner_id, "owner"), (attacker_id, "attacker")):
                custom_id = await connection.scalar(
                    text(
                        """
                        INSERT INTO foods_custom (
                            user_id, name, nutrients_json, portions_json
                        ) VALUES (
                            CAST(:user_id AS uuid), :name, CAST(:nutrients AS jsonb),
                            '[]'::jsonb
                        ) RETURNING id
                        """
                    ),
                    {
                        "user_id": user_id,
                        "name": f"Private {label} food",
                        "nutrients": payload,
                    },
                )
                recipe_id = await connection.scalar(
                    text(
                        """
                        INSERT INTO recipes (user_id, name, yield_grams, is_public)
                        VALUES (CAST(:user_id AS uuid), :name, 250, false)
                        RETURNING id
                        """
                    ),
                    {"user_id": user_id, "name": f"Private {label} recipe"},
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO recipe_ingredients (
                            user_id, recipe_id, position, food_source_table,
                            food_source_id, food_source_key, food_name, grams,
                            computed_nutrients_json
                        ) VALUES (
                            CAST(:user_id AS uuid), CAST(:recipe_id AS uuid), 0,
                            'foods_custom', CAST(:custom_id AS uuid), :custom_id,
                            :food_name, 100, CAST(:snapshot AS jsonb)
                        )
                        """
                    ),
                    {
                        "user_id": user_id,
                        "recipe_id": str(recipe_id),
                        "custom_id": str(custom_id),
                        "food_name": f"Private {label} food",
                        "snapshot": snapshot,
                    },
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO log_entries (
                            user_id, logged_at, meal_slot, food_source_table,
                            food_source_id, food_source_key, food_name,
                            quantity_amount, quantity_unit, grams,
                            computed_nutrients_json
                        ) VALUES (
                            CAST(:user_id AS uuid), '2026-08-20T12:00:00Z', 'lunch',
                            'foods_custom', CAST(:custom_id AS uuid), :custom_id,
                            :food_name, 1, 'g', 1, CAST(:snapshot AS jsonb)
                        )
                        """
                    ),
                    {
                        "user_id": user_id,
                        "custom_id": str(custom_id),
                        "food_name": f"Private {label} food",
                        "snapshot": snapshot,
                    },
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO targets (
                            user_id, day_type, kcal, protein_g, carb_g, fat_g,
                            active_from, below_floor_confirmed,
                            safety_review_required, safety_floor_kcal
                        ) VALUES (
                            CAST(:user_id AS uuid), 'training', 2000, 150, 220, 60,
                            '2026-08-01', false, false, 1200
                        )
                        """
                    ),
                    {"user_id": user_id},
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO body_metrics (
                            user_id, recorded_at, metric_type, value, unit
                        ) VALUES (
                            CAST(:user_id AS uuid), '2026-08-20T10:00:00Z',
                            'body_weight', 80.5, 'kg'
                        )
                        """
                    ),
                    {"user_id": user_id},
                )
                workout_id = await connection.scalar(
                    text(
                        """
                        INSERT INTO workouts (user_id, performed_at, notes)
                        VALUES (
                            CAST(:user_id AS uuid), '2026-08-20T11:00:00Z', :notes
                        ) RETURNING id
                        """
                    ),
                    {"user_id": user_id, "notes": f"Private {label} workout"},
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO workout_sets (
                            user_id, workout_id, exercise_id, set_index, reps,
                            load_value, load_unit
                        ) VALUES (
                            CAST(:user_id AS uuid), CAST(:workout_id AS uuid),
                            CAST(:exercise_id AS uuid), 0, 5, 100, 'kg'
                        )
                        """
                    ),
                    {
                        "user_id": user_id,
                        "workout_id": str(workout_id),
                        "exercise_id": str(exercise_id),
                    },
                )
    finally:
        await engine.dispose()


@pytest.fixture
def export_clients() -> Iterator[ExportClients]:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_reset(INTEGRATION_DATABASE_URL))
    settings = Settings(
        database_url=INTEGRATION_DATABASE_URL,
        app_environment="test",
        auth_rate_limit_attempts=50,
        _env_file=None,
    )
    with (
        TestClient(create_app(settings)) as owner,
        TestClient(create_app(settings)) as attacker,
        TestClient(create_app(settings)) as anonymous,
    ):
        owner_registration = owner.post(
            "/api/v1/auth/register",
            json={"email": "owner@example.test", "password": "owner password 123"},
        )
        attacker_registration = attacker.post(
            "/api/v1/auth/register",
            json={"email": "attacker@example.test", "password": "attacker password 123"},
        )
        assert owner_registration.status_code == 201
        assert attacker_registration.status_code == 201
        owner_id = owner_registration.json()["user"]["id"]
        attacker_id = attacker_registration.json()["user"]["id"]
        asyncio.run(_seed(INTEGRATION_DATABASE_URL, owner_id, attacker_id))
        yield ExportClients(owner, attacker, anonymous, owner_id, attacker_id)


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_private_export_is_authenticated_cache_proof_and_tenant_isolated(
    export_clients: ExportClients,
) -> None:
    unauthorized = export_clients.anonymous.get("/api/v1/export/me")
    owner_response = export_clients.owner.get("/api/v1/export/me")
    attacker_response = export_clients.attacker.get("/api/v1/export/me")

    assert unauthorized.status_code == 401
    assert unauthorized.headers["cache-control"] == "no-store"
    assert owner_response.status_code == 200
    assert owner_response.headers["cache-control"] == "no-store"
    assert owner_response.headers["content-type"].startswith("application/json")
    assert "content-length" not in owner_response.headers
    assert "opennosh-private-data.json" in owner_response.headers["content-disposition"]

    owner = owner_response.json()
    attacker = attacker_response.json()
    assert list(owner) == [
        "schema_version",
        "dataset",
        "access",
        "notice",
        "account",
        "custom_foods",
        "recipes",
        "recipe_ingredients",
        "log_entries",
        "targets",
        "body_metrics",
        "workouts",
        "workout_sets",
    ]
    assert owner["account"]["id"] == export_clients.owner_id
    assert owner["account"]["settings"] == {"timezone": "America/New_York"}
    assert attacker["account"]["id"] == export_clients.attacker_id
    for section in (
        "custom_foods",
        "recipes",
        "recipe_ingredients",
        "log_entries",
        "targets",
        "body_metrics",
        "workouts",
        "workout_sets",
    ):
        assert len(owner[section]) == 1
        assert len(attacker[section]) == 1
    serialized_owner = owner_response.text
    assert "Private owner" in serialized_owner
    assert "Private attacker" not in serialized_owner
    assert "password" not in serialized_owner.casefold()
    assert "csrf" not in serialized_owner.casefold()
    assert "token" not in serialized_owner.casefold()


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_private_export_has_an_independent_per_account_rate_limit() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_reset(INTEGRATION_DATABASE_URL))
    settings = Settings(
        database_url=INTEGRATION_DATABASE_URL,
        app_environment="test",
        auth_rate_limit_attempts=50,
        private_export_rate_limit_attempts=1,
        _env_file=None,
    )

    with TestClient(create_app(settings)) as client:
        registered = client.post(
            "/api/v1/auth/register",
            json={"email": "rate-limit@example.test", "password": "owner password 123"},
        )
        assert registered.status_code == 201
        exported = client.get("/api/v1/export/me")
        limited = client.get("/api/v1/export/me")

    assert exported.status_code == 200
    assert limited.status_code == 429
    assert limited.headers["cache-control"] == "no-store"
    assert int(limited.headers["retry-after"]) > 0
    assert limited.json() == {
        "detail": "Too many private data exports. Try again later."
    }


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_public_exports_have_stable_separate_license_boundaries(
    export_clients: ExportClients,
) -> None:
    community_response = export_clients.anonymous.get("/api/v1/export/foods/community")
    odbl_response = export_clients.anonymous.get("/api/v1/export/foods/odbl")
    legacy_odbl_response = export_clients.anonymous.get(
        "/api/v1/export/foods/openfoodfacts"
    )
    exercise_response = export_clients.anonymous.get("/api/v1/export/exercises")

    assert community_response.status_code == 200
    assert odbl_response.status_code == 200
    assert legacy_odbl_response.json() == odbl_response.json()
    assert exercise_response.status_code == 200
    for response in (community_response, odbl_response, exercise_response):
        assert "content-length" not in response.headers

    community = community_response.json()
    assert list(community) == [
        "schema_version",
        "dataset",
        "license",
        "license_url",
        "notice",
        "entries",
    ]
    entry = community["entries"][0]
    assert entry["source"] == "community"
    assert entry["pack_id"] == "south-asian-staples"
    assert entry["pack_version"] == "1.2.3"
    assert entry["pack_license"] == "CC0-1.0"
    assert entry["source_license"] == "contributor-original"
    assert entry["source_uri"] == "https://example.test/fixture-dal"
    assert entry["contributed_by"] == "Visible Contributor"

    odbl = odbl_response.json()
    assert odbl["database_license"] == "ODbL-1.0"
    assert odbl["contents_license"] == "DbCL-1.0"
    assert odbl["entries"][0]["source"] == "openfoodfacts"
    assert odbl["entries"][0]["attribution_text"] == "Open Food Facts contributors"

    exercises = exercise_response.json()
    assert exercises["license_spdx"] == "CC-BY-SA-3.0"
    assert exercises["entries"][0]["attribution"]["source"] == "wger"
    assert exercises["entries"][0]["attribution"]["author"] == "wger contributors"

    public_payloads = " ".join(
        response.text for response in (community_response, odbl_response, exercise_response)
    )
    assert "Private owner" not in public_payloads
    assert "Private attacker" not in public_payloads
