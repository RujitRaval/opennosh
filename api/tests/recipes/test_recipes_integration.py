from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from dataclasses import dataclass
from types import SimpleNamespace
from uuid import UUID

import pytest
from alembic import command
from fastapi.testclient import TestClient
from opennosh_api.main import create_app
from opennosh_api.recipes import service as recipe_service
from opennosh_api.recipes.schemas import RecipeWrite
from opennosh_api.settings import Settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.tests.test_migrations import migration_config

INTEGRATION_DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL")

_NUTRIENTS = """{
  "basis": "per_100g",
  "nutrients": {
    "energy_kcal": "100",
    "protein_g": "10",
    "carbohydrate_g": "15",
    "fat_g": "0"
  }
}"""
_RICH_NUTRIENTS = """{
  "basis": "per_100g",
  "nutrients": {
    "energy_kcal": "190",
    "protein_g": "10",
    "carbohydrate_g": "15",
    "fat_g": "10"
  }
}"""


@dataclass(frozen=True)
class RecipeClients:
    owner: TestClient
    attacker: TestClient
    owner_csrf: str
    attacker_csrf: str
    owner_user_id: str
    owner_custom_food_id: str
    attacker_custom_food_id: str


async def _reset_database(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    TRUNCATE auth_rate_limits, auth_sessions, log_entries,
                             recipe_ingredients, recipes, foods_reference,
                             foods_community, foods_odbl, foods_custom, users CASCADE
                    """
                )
            )
    finally:
        await engine.dispose()


async def _seed_foods(
    database_url: str, owner_user_id: str, attacker_user_id: str
) -> tuple[str, str]:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO foods_reference (
                        fdc_id, description, food_category, nutrients_json, portions_json
                    ) VALUES (
                        '100', 'Stable oats', 'grain', CAST(:nutrients AS jsonb), '[]'::jsonb
                    )
                    """
                ),
                {"nutrients": _NUTRIENTS},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO foods_community (
                        pack_id, pack_version, slug, name, locale, category,
                        provenance, source_license, nutrients_json, portions_json,
                        contributed_by
                    ) VALUES (
                        'recipe-test', '1.0.0', 'community-lentils',
                        'Community lentils', 'en', 'legume', 'own_measurement',
                        'contributor-original', CAST(:nutrients AS jsonb),
                        '[]'::jsonb, 'Recipe Tester'
                    )
                    """
                ),
                {"nutrients": _RICH_NUTRIENTS},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO foods_odbl (
                        barcode, product_name, nutrients_json, source_url,
                        attribution_text
                    ) VALUES (
                        '0012345678905', 'Packaged lentils', CAST(:nutrients AS jsonb),
                        'https://world.openfoodfacts.org/product/0012345678905',
                        'Open Food Facts contributors'
                    )
                    """
                ),
                {"nutrients": _RICH_NUTRIENTS},
            )
            owner_food_id = await connection.scalar(
                text(
                    """
                    INSERT INTO foods_custom (
                        user_id, name, nutrients_json, portions_json
                    ) VALUES (
                        CAST(:user_id AS uuid), 'Owner lentils',
                        CAST(:nutrients AS jsonb), '[]'::jsonb
                    ) RETURNING id
                    """
                ),
                {"user_id": owner_user_id, "nutrients": _RICH_NUTRIENTS},
            )
            attacker_food_id = await connection.scalar(
                text(
                    """
                    INSERT INTO foods_custom (
                        user_id, name, nutrients_json, portions_json
                    ) VALUES (
                        CAST(:user_id AS uuid), 'Attacker lentils',
                        CAST(:nutrients AS jsonb), '[]'::jsonb
                    ) RETURNING id
                    """
                ),
                {"user_id": attacker_user_id, "nutrients": _RICH_NUTRIENTS},
            )
            return str(owner_food_id), str(attacker_food_id)
    finally:
        await engine.dispose()


async def _mutate_and_delete_sources(database_url: str, owner_food_id: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE foods_reference
                    SET description = 'Changed oats',
                        nutrients_json = jsonb_set(
                            nutrients_json, '{nutrients,energy_kcal}', '"999"'::jsonb
                        )
                    WHERE fdc_id = '100'
                    """
                )
            )
            await connection.execute(
                text("DELETE FROM foods_custom WHERE id = CAST(:food_id AS uuid)"),
                {"food_id": owner_food_id},
            )
    finally:
        await engine.dispose()


async def _read_log_grams(database_url: str, log_id: str) -> str:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            return str(
                await connection.scalar(
                    text("SELECT grams FROM log_entries WHERE id = CAST(:id AS uuid)"),
                    {"id": log_id},
                )
            )
    finally:
        await engine.dispose()


async def _assert_updates_serialize(
    database_url: str,
    recipe_id: str,
    user_id: str,
    first_payload: RecipeWrite,
    second_payload: RecipeWrite,
) -> None:
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    first_entered = asyncio.Event()
    second_entered = asyncio.Event()
    release_first = asyncio.Event()
    calls = 0
    original = recipe_service._build_ingredients

    async def gated_build(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            first_entered.set()
            await release_first.wait()
        else:
            second_entered.set()
        return await original(*args, **kwargs)

    recipe_service._build_ingredients = gated_build
    current = SimpleNamespace(user_id=UUID(user_id))
    try:
        async with sessions() as first_database, sessions() as second_database:
            first = asyncio.create_task(
                recipe_service.update_recipe(
                    first_database, UUID(recipe_id), first_payload, current
                )
            )
            await asyncio.wait_for(first_entered.wait(), timeout=2)
            second = asyncio.create_task(
                recipe_service.update_recipe(
                    second_database, UUID(recipe_id), second_payload, current
                )
            )
            await asyncio.sleep(0.1)
            assert not second_entered.is_set()
            release_first.set()
            first_result, second_result = await asyncio.gather(first, second)
            assert first_result is not None
            assert second_result is not None
            assert second_result.name == second_payload.name
    finally:
        recipe_service._build_ingredients = original
        await engine.dispose()


@pytest.fixture
def recipe_clients() -> Iterator[RecipeClients]:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_reset_database(INTEGRATION_DATABASE_URL))
    settings = Settings(
        database_url=INTEGRATION_DATABASE_URL,
        app_environment="test",
        auth_rate_limit_attempts=50,
        _env_file=None,
    )
    with (
        TestClient(create_app(settings)) as owner,
        TestClient(create_app(settings)) as attacker,
    ):
        owner_registration = owner.post(
            "/api/v1/auth/register",
            json={"email": "recipe-owner@example.test", "password": "owner password 123"},
        )
        attacker_registration = attacker.post(
            "/api/v1/auth/register",
            json={
                "email": "recipe-attacker@example.test",
                "password": "attacker password 123",
            },
        )
        assert owner_registration.status_code == 201
        assert attacker_registration.status_code == 201
        owner_food_id, attacker_food_id = asyncio.run(
            _seed_foods(
                INTEGRATION_DATABASE_URL,
                owner_registration.json()["user"]["id"],
                attacker_registration.json()["user"]["id"],
            )
        )
        yield RecipeClients(
            owner=owner,
            attacker=attacker,
            owner_csrf=owner_registration.json()["csrf_token"],
            attacker_csrf=attacker_registration.json()["csrf_token"],
            owner_user_id=owner_registration.json()["user"]["id"],
            owner_custom_food_id=owner_food_id,
            attacker_custom_food_id=attacker_food_id,
        )


def _recipe_payload(custom_food_id: str) -> dict[str, object]:
    return {
        "name": "Sunday dal",
        "yield_grams": "300",
        "ingredients": [
            {"food": {"source": "usda", "source_id": "100"}, "grams": "100"},
            {
                "food": {"source": "custom", "source_id": custom_food_id},
                "grams": "50",
            },
        ],
    }


def _create_recipe(clients: RecipeClients):
    return clients.owner.post(
        "/api/v1/recipes",
        headers={"X-CSRF-Token": clients.owner_csrf},
        json=_recipe_payload(clients.owner_custom_food_id),
    )


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_recipe_crud_composes_exact_snapshots_and_preserves_order(
    recipe_clients: RecipeClients,
) -> None:
    created = _create_recipe(recipe_clients)

    assert created.status_code == 201
    assert created.headers["cache-control"] == "no-store"
    body = created.json()
    assert body["name"] == "Sunday dal"
    assert body["yield_grams"] == "300"
    assert body["is_public"] is False
    assert [item["position"] for item in body["ingredients"]] == [0, 1]
    assert [item["snapshot"]["grams"] for item in body["ingredients"]] == [
        "100.00",
        "50.00",
    ]
    assert body["total"]["grams"] == "300.00"
    assert body["total"]["nutrients"]["energy_kcal"] == "195.00"
    assert body["nutrients_per_100g"]["energy_kcal"] == "65.00"

    listed = recipe_clients.owner.get("/api/v1/recipes")
    assert listed.status_code == 200
    assert listed.headers["cache-control"] == "no-store"
    assert [item["id"] for item in listed.json()["items"]] == [body["id"]]
    assert recipe_clients.owner.get(f"/api/v1/recipes/{body['id']}").json() == body

    updated = recipe_clients.owner.put(
        f"/api/v1/recipes/{body['id']}",
        headers={"X-CSRF-Token": recipe_clients.owner_csrf},
        json={
            "name": "Oat bowl",
            "yield_grams": "200",
            "ingredients": [
                {"food": {"source": "usda", "source_id": "100"}, "grams": "100"}
            ],
        },
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Oat bowl"
    assert len(updated.json()["ingredients"]) == 1

    failed_update = recipe_clients.owner.put(
        f"/api/v1/recipes/{body['id']}",
        headers={"X-CSRF-Token": recipe_clients.owner_csrf},
        json={
            "name": "Must not persist",
            "yield_grams": "999",
            "ingredients": [
                {
                    "food": {
                        "source": "custom",
                        "source_id": recipe_clients.attacker_custom_food_id,
                    },
                    "grams": "1",
                }
            ],
        },
    )
    assert failed_update.status_code == 404
    after_failed_update = recipe_clients.owner.get(f"/api/v1/recipes/{body['id']}")
    assert after_failed_update.json()["name"] == "Oat bowl"
    assert after_failed_update.json()["yield_grams"] == "200"
    assert len(after_failed_update.json()["ingredients"]) == 1

    deleted = recipe_clients.owner.delete(
        f"/api/v1/recipes/{body['id']}",
        headers={"X-CSRF-Token": recipe_clients.owner_csrf},
    )
    assert deleted.status_code == 204
    assert recipe_clients.owner.get(f"/api/v1/recipes/{body['id']}").status_code == 404


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_recipe_composition_supports_every_v1_food_store(
    recipe_clients: RecipeClients,
) -> None:
    created = recipe_clients.owner.post(
        "/api/v1/recipes",
        headers={"X-CSRF-Token": recipe_clients.owner_csrf},
        json={
            "name": "Four-store bowl",
            "yield_grams": "100",
            "ingredients": [
                {"food": {"source": "usda", "source_id": "100"}, "grams": "10"},
                {
                    "food": {"source": "community", "source_id": "community-lentils"},
                    "grams": "10",
                },
                {
                    "food": {"source": "openfoodfacts", "source_id": "0012345678905"},
                    "grams": "10",
                },
                {
                    "food": {
                        "source": "custom",
                        "source_id": recipe_clients.owner_custom_food_id,
                    },
                    "grams": "10",
                },
            ],
        },
    )

    assert created.status_code == 201
    assert [item["food"]["source"] for item in created.json()["ingredients"]] == [
        "usda",
        "community",
        "openfoodfacts",
        "custom",
    ]
    assert created.json()["total"]["nutrients"]["energy_kcal"] == "67.00"


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_exact_large_yield_and_noncanonical_custom_uuid_remain_loggable(
    recipe_clients: RecipeClients,
) -> None:
    custom_id = recipe_clients.owner_custom_food_id.replace("-", "").upper()
    direct_log = recipe_clients.owner.post(
        "/api/v1/logs",
        headers={"X-CSRF-Token": recipe_clients.owner_csrf},
        json={
            "logged_at": "2026-08-20T09:00:00Z",
            "meal_slot": "breakfast",
            "food": {"source": "custom", "source_id": custom_id},
            "quantity": {"amount": "1", "unit": "g"},
        },
    )
    assert direct_log.status_code == 201
    assert direct_log.json()["food"]["source_id"] == recipe_clients.owner_custom_food_id

    created = recipe_clients.owner.post(
        "/api/v1/recipes",
        headers={"X-CSRF-Token": recipe_clients.owner_csrf},
        json={
            "name": "Large exact batch",
            "yield_grams": "10001.0001",
            "ingredients": [
                {
                    "food": {"source": "custom", "source_id": custom_id},
                    "grams": "10.0001",
                }
            ],
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["yield_grams"] == "10001.0001"
    assert body["ingredients"][0]["grams"] == "10.0001"
    assert body["ingredients"][0]["food"]["source_id"] == recipe_clients.owner_custom_food_id
    assert recipe_clients.owner.get(f"/api/v1/recipes/{body['id']}").json() == body

    logged = recipe_clients.owner.post(
        "/api/v1/logs",
        headers={"X-CSRF-Token": recipe_clients.owner_csrf},
        json={
            "logged_at": "2026-08-20T12:00:00Z",
            "meal_slot": "lunch",
            "food": {"source": "recipe", "source_id": body["id"]},
            "quantity": {
                "amount": "0.5",
                "unit": "portion",
                "portion_name": "whole recipe",
            },
        },
    )
    assert logged.status_code == 201
    assert logged.json()["snapshot"]["grams"] == "5000.50"


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_sub_milligram_recipe_yield_round_trips_and_logs_exactly(
    recipe_clients: RecipeClients,
) -> None:
    created = recipe_clients.owner.post(
        "/api/v1/recipes",
        headers={"X-CSRF-Token": recipe_clients.owner_csrf},
        json={
            "name": "Tiny exact batch",
            "yield_grams": "0.0001",
            "ingredients": [
                {"food": {"source": "usda", "source_id": "100"}, "grams": "0.0001"}
            ],
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["yield_grams"] == "0.0001"
    assert body["ingredients"][0]["grams"] == "0.0001"
    assert recipe_clients.owner.get(f"/api/v1/recipes/{body['id']}").json() == body

    logged = recipe_clients.owner.post(
        "/api/v1/logs",
        headers={"X-CSRF-Token": recipe_clients.owner_csrf},
        json={
            "logged_at": "2026-08-20T12:00:00Z",
            "meal_slot": "lunch",
            "food": {"source": "recipe", "source_id": body["id"]},
            "quantity": {
                "amount": "1",
                "unit": "portion",
                "portion_name": "whole recipe",
            },
        },
    )
    assert logged.status_code == 201
    assert INTEGRATION_DATABASE_URL is not None
    assert asyncio.run(_read_log_grams(INTEGRATION_DATABASE_URL, logged.json()["id"])) == "0.0001"


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_concurrent_recipe_updates_are_serialized(
    recipe_clients: RecipeClients,
) -> None:
    created = _create_recipe(recipe_clients)
    assert created.status_code == 201
    first_payload = RecipeWrite.model_validate(
        {
            "name": "First update",
            "yield_grams": "200",
            "ingredients": [
                {"food": {"source": "usda", "source_id": "100"}, "grams": "50"}
            ],
        }
    )
    second_payload = RecipeWrite.model_validate(
        {
            "name": "Second update",
            "yield_grams": "400",
            "ingredients": [
                {
                    "food": {
                        "source": "custom",
                        "source_id": recipe_clients.owner_custom_food_id,
                    },
                    "grams": "75",
                }
            ],
        }
    )
    assert INTEGRATION_DATABASE_URL is not None
    asyncio.run(
        _assert_updates_serialize(
            INTEGRATION_DATABASE_URL,
            created.json()["id"],
            recipe_clients.owner_user_id,
            first_payload,
            second_payload,
        )
    )

    final = recipe_clients.owner.get(f"/api/v1/recipes/{created.json()['id']}")
    assert final.status_code == 200
    assert final.json()["name"] == "Second update"
    assert final.json()["yield_grams"] == "400"
    assert final.json()["ingredients"][0]["food"]["source"] == "custom"


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_recipe_snapshots_survive_source_mutation_and_drive_deterministic_logs(
    recipe_clients: RecipeClients,
) -> None:
    created = _create_recipe(recipe_clients)
    assert created.status_code == 201
    recipe_id = created.json()["id"]

    first_log = recipe_clients.owner.post(
        "/api/v1/logs",
        headers={"X-CSRF-Token": recipe_clients.owner_csrf},
        json={
            "logged_at": "2026-08-20T12:00:00Z",
            "meal_slot": "lunch",
            "food": {"source": "recipe", "source_id": recipe_id},
            "quantity": {
                "amount": "0.5",
                "unit": "portion",
                "portion_name": "whole recipe",
            },
        },
    )
    assert first_log.status_code == 201
    assert first_log.json()["snapshot"]["grams"] == "150.00"
    assert first_log.json()["snapshot"]["nutrients"]["energy_kcal"] == "97.50"

    invalid_portion = recipe_clients.owner.post(
        "/api/v1/logs",
        headers={"X-CSRF-Token": recipe_clients.owner_csrf},
        json={
            "logged_at": "2026-08-20T13:00:00Z",
            "meal_slot": "lunch",
            "food": {"source": "recipe", "source_id": recipe_id},
            "quantity": {
                "amount": "1",
                "unit": "portion",
                "portion_name": "scoop",
            },
        },
    )
    assert invalid_portion.status_code == 422
    assert invalid_portion.json()["detail"] == "Unknown household portion: scoop"

    assert INTEGRATION_DATABASE_URL is not None
    asyncio.run(
        _mutate_and_delete_sources(
            INTEGRATION_DATABASE_URL, recipe_clients.owner_custom_food_id
        )
    )
    unchanged = recipe_clients.owner.get(f"/api/v1/recipes/{recipe_id}")
    assert unchanged.status_code == 200
    assert unchanged.json()["total"]["nutrients"]["energy_kcal"] == "195.00"
    assert unchanged.json()["ingredients"][0]["food"]["name"] == "Stable oats"

    gram_log = recipe_clients.owner.post(
        "/api/v1/logs",
        headers={"X-CSRF-Token": recipe_clients.owner_csrf},
        json={
            "logged_at": "2026-08-20T18:00:00Z",
            "meal_slot": "dinner",
            "food": {"source": "recipe", "source_id": recipe_id},
            "quantity": {"amount": "75", "unit": "g"},
        },
    )
    assert gram_log.status_code == 201
    assert gram_log.json()["snapshot"]["nutrients"]["energy_kcal"] == "48.75"

    assert (
        recipe_clients.owner.delete(
            f"/api/v1/recipes/{recipe_id}",
            headers={"X-CSRF-Token": recipe_clients.owner_csrf},
        ).status_code
        == 204
    )
    historical = recipe_clients.owner.get(f"/api/v1/logs/{first_log.json()['id']}")
    assert historical.status_code == 200
    assert historical.json()["food"]["name"] == "Sunday dal"
    assert historical.json()["snapshot"]["nutrients"]["energy_kcal"] == "97.50"


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_every_recipe_operation_enforces_tenant_ownership(
    recipe_clients: RecipeClients,
) -> None:
    created = _create_recipe(recipe_clients)
    assert created.status_code == 201
    recipe_id = created.json()["id"]

    assert recipe_clients.attacker.get(f"/api/v1/recipes/{recipe_id}").status_code == 404
    assert recipe_clients.attacker.get("/api/v1/recipes").json()["items"] == []
    assert (
        recipe_clients.attacker.put(
            f"/api/v1/recipes/{recipe_id}",
            headers={"X-CSRF-Token": recipe_clients.attacker_csrf},
            json=_recipe_payload(recipe_clients.attacker_custom_food_id),
        ).status_code
        == 404
    )
    assert (
        recipe_clients.attacker.delete(
            f"/api/v1/recipes/{recipe_id}",
            headers={"X-CSRF-Token": recipe_clients.attacker_csrf},
        ).status_code
        == 404
    )
    attacker_log = recipe_clients.attacker.post(
        "/api/v1/logs",
        headers={"X-CSRF-Token": recipe_clients.attacker_csrf},
        json={
            "logged_at": "2026-08-20T12:00:00Z",
            "meal_slot": "lunch",
            "food": {"source": "recipe", "source_id": recipe_id},
            "quantity": {"amount": "10", "unit": "g"},
        },
    )
    assert attacker_log.status_code == 404

    cross_tenant_ingredient = recipe_clients.owner.post(
        "/api/v1/recipes",
        headers={"X-CSRF-Token": recipe_clients.owner_csrf},
        json=_recipe_payload(recipe_clients.attacker_custom_food_id),
    )
    assert cross_tenant_ingredient.status_code == 404
    assert cross_tenant_ingredient.headers["cache-control"] == "no-store"


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_recipe_authentication_validation_and_pagination_are_private(
    recipe_clients: RecipeClients,
) -> None:
    missing_csrf = recipe_clients.owner.post(
        "/api/v1/recipes", json=_recipe_payload(recipe_clients.owner_custom_food_id)
    )
    assert missing_csrf.status_code == 403
    assert missing_csrf.headers["cache-control"] == "no-store"

    invalid = recipe_clients.owner.post(
        "/api/v1/recipes",
        headers={"X-CSRF-Token": recipe_clients.owner_csrf},
        json={**_recipe_payload(recipe_clients.owner_custom_food_id), "is_public": True},
    )
    assert invalid.status_code == 422
    assert invalid.headers["cache-control"] == "no-store"
    assert recipe_clients.owner.get("/api/v1/recipes", params={"limit": 101}).status_code == 422
    assert (
        recipe_clients.owner.get("/api/v1/recipes", params={"offset": 10001}).status_code
        == 422
    )

    first = _create_recipe(recipe_clients)
    second = recipe_clients.owner.post(
        "/api/v1/recipes",
        headers={"X-CSRF-Token": recipe_clients.owner_csrf},
        json={**_recipe_payload(recipe_clients.owner_custom_food_id), "name": "Ziti"},
    )
    assert first.status_code == 201
    assert second.status_code == 201
    first_page = recipe_clients.owner.get(
        "/api/v1/recipes", params={"limit": 1, "offset": 0}
    ).json()
    second_page = recipe_clients.owner.get(
        "/api/v1/recipes", params={"limit": 1, "offset": 1}
    ).json()
    assert first_page["has_more"] is True
    assert len(first_page["items"]) == 1
    assert second_page["has_more"] is False
    assert len(second_page["items"]) == 1
    assert first_page["items"][0]["id"] != second_page["items"][0]["id"]

    assert INTEGRATION_DATABASE_URL is not None
    settings = Settings(
        database_url=INTEGRATION_DATABASE_URL,
        app_environment="test",
        _env_file=None,
    )
    with TestClient(create_app(settings)) as unauthenticated:
        response = unauthenticated.get("/api/v1/recipes")
        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store"
