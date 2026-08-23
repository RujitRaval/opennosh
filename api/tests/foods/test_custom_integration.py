from __future__ import annotations

import asyncio
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


@dataclass(frozen=True)
class CustomFoodClients:
    owner: TestClient
    attacker: TestClient
    owner_csrf: str
    attacker_csrf: str


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


async def _custom_rows(database_url: str) -> list[tuple[str, str]]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            rows = await connection.execute(
                text(
                    """
                    SELECT users.email, foods_custom.name
                    FROM foods_custom
                    JOIN users ON users.id = foods_custom.user_id
                    ORDER BY users.email
                    """
                )
            )
            return [(str(row[0]), str(row[1])) for row in rows]
    finally:
        await engine.dispose()


@pytest.fixture
def custom_food_clients() -> Iterator[CustomFoodClients]:
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
            json={"email": "custom-owner@example.test", "password": "owner password 123"},
        )
        attacker_registration = attacker.post(
            "/api/v1/auth/register",
            json={
                "email": "custom-attacker@example.test",
                "password": "attacker password 123",
            },
        )
        assert owner_registration.status_code == 201
        assert attacker_registration.status_code == 201
        yield CustomFoodClients(
            owner=owner,
            attacker=attacker,
            owner_csrf=owner_registration.json()["csrf_token"],
            attacker_csrf=attacker_registration.json()["csrf_token"],
        )


def _payload() -> dict[str, object]:
    return {
        "name": "  Weeknight tofu  ",
        "nutrients": {
            "basis": "per_100g",
            "nutrients": {
                "energy_kcal": "165",
                "protein_g": "10",
                "carbohydrate_g": "20",
                "fat_g": "5",
            },
        },
        "portions": [{"name": "slice", "grams": "25"}],
    }


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_custom_food_is_private_and_can_be_logged_by_its_owner(
    custom_food_clients: CustomFoodClients,
) -> None:
    created = custom_food_clients.owner.post(
        "/api/v1/foods/custom",
        headers={"X-CSRF-Token": custom_food_clients.owner_csrf},
        json=_payload(),
    )

    assert created.status_code == 201
    assert created.headers["cache-control"] == "no-store"
    body = created.json()
    assert body["source"] == "custom"
    assert body["source_id"] == body["id"]
    assert body["name"] == "Weeknight tofu"
    assert body["private"] is True
    assert body["portions"] == [{"name": "slice", "grams": "25"}]
    assert asyncio.run(_custom_rows(INTEGRATION_DATABASE_URL)) == [
        ("custom-owner@example.test", "Weeknight tofu")
    ]

    owner_log = custom_food_clients.owner.post(
        "/api/v1/logs",
        headers={"X-CSRF-Token": custom_food_clients.owner_csrf},
        json={
            "logged_at": "2026-08-20T12:00:00Z",
            "meal_slot": "Lunch",
            "food": {"source": "custom", "source_id": body["id"]},
            "quantity": {"amount": "2", "unit": "portion", "portion_name": "slice"},
        },
    )
    assert owner_log.status_code == 201
    assert owner_log.json()["snapshot"]["grams"] == "50.00"

    attacker_log = custom_food_clients.attacker.post(
        "/api/v1/logs",
        headers={"X-CSRF-Token": custom_food_clients.attacker_csrf},
        json={
            "logged_at": "2026-08-20T12:00:00Z",
            "meal_slot": "Lunch",
            "food": {"source": "custom", "source_id": body["id"]},
            "quantity": {"amount": "50", "unit": "g", "portion_name": None},
        },
    )
    assert attacker_log.status_code == 404

    public_search = custom_food_clients.attacker.get(
        "/api/v1/foods/search", params={"q": "Weeknight tofu"}
    )
    assert public_search.status_code == 200
    assert public_search.json()["items"] == []


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_custom_food_requires_authentication_and_csrf(
    custom_food_clients: CustomFoodClients,
) -> None:
    settings = Settings(
        database_url=INTEGRATION_DATABASE_URL,
        app_environment="test",
        _env_file=None,
    )
    with TestClient(create_app(settings)) as anonymous:
        assert anonymous.post("/api/v1/foods/custom", json=_payload()).status_code == 401
    assert (
        custom_food_clients.owner.post("/api/v1/foods/custom", json=_payload()).status_code == 403
    )


def test_food_capabilities_follow_open_food_facts_configuration() -> None:
    for enabled in (False, True):
        settings = Settings(
            app_environment="test",
            open_food_facts_enabled=enabled,
            **({"open_food_facts_user_agent_contact": "ops@example.test"} if enabled else {}),
            _env_file=None,
        )
        with TestClient(create_app(settings)) as client:
            response = client.get("/api/v1/foods/capabilities")
        assert response.status_code == 200
        assert response.json() == {
            "schema_version": "1.0",
            "barcode_lookup_enabled": enabled,
        }
