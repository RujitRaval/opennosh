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

_USDA_NUTRIENTS = """{
  "basis": "per_100g",
  "nutrients": {
    "energy_kcal": "100",
    "protein_g": "10",
    "carbohydrate_g": "15",
    "fat_g": "0"
  }
}"""
_COMMUNITY_NUTRIENTS = """{
  "basis": "per_100g",
  "nutrients": {
    "energy_kcal": "190",
    "protein_g": "10",
    "carbohydrate_g": "15",
    "fat_g": "10"
  }
}"""
_AUTHORITATIVE_NUTRIENTS = """{
  "basis": "per_100g",
  "nutrients": {
    "energy_kcal": "100",
    "protein_g": "0",
    "carbohydrate_g": "0",
    "fat_g": "0"
  }
}"""


@dataclass(frozen=True)
class LogClients:
    owner: TestClient
    attacker: TestClient
    owner_csrf: str
    attacker_csrf: str
    attacker_custom_food_id: str
    owner_custom_food_id: str
    owner_user_id: str


async def _reset_database(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    TRUNCATE auth_rate_limits, auth_sessions, log_entries,
                             foods_reference, foods_community, foods_odbl,
                             foods_custom, users CASCADE
                    """
                )
            )
    finally:
        await engine.dispose()


async def _seed_foods(
    database_url: str, attacker_user_id: str, owner_user_id: str
) -> tuple[str, str]:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO foods_reference (
                        fdc_id, description, food_category, nutrients_json, portions_json
                    ) VALUES
                    (
                        '100', 'Stable oats', 'grain', CAST(:usda AS jsonb),
                        '[{"name":"1 cup","grams":"80"}]'::jsonb
                    ),
                    (
                        '101', 'Published energy-factor food', 'test',
                        CAST(:authoritative AS jsonb), '[]'::jsonb
                    )
                    """
                ),
                {"usda": _USDA_NUTRIENTS, "authoritative": _AUTHORITATIVE_NUTRIENTS},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO foods_community (
                        pack_id, pack_version, slug, name, locale, category, provenance,
                        source_license, nutrients_json, portions_json, contributed_by
                    ) VALUES (
                        'community-test', '1.0.0', 'dal-rice', 'Dal rice', 'en-IN',
                        'meal', 'own_measurement', 'contributor-original',
                        CAST(:community AS jsonb),
                        '[{"name":"1 bowl","grams":"200"}]'::jsonb,
                        'Test Contributor'
                    )
                    """
                ),
                {"community": _COMMUNITY_NUTRIENTS},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO foods_odbl (
                        barcode, product_name, nutrients_json, source_url,
                        attribution_text
                    ) VALUES (
                        '0012345678905', 'Packaged dal', CAST(:community AS jsonb),
                        'https://world.openfoodfacts.org/product/0012345678905',
                        'Open Food Facts contributors'
                    )
                    """
                ),
                {"community": _COMMUNITY_NUTRIENTS},
            )
            attacker_custom_id = await connection.scalar(
                text(
                    """
                    INSERT INTO foods_custom (
                        user_id, name, nutrients_json, portions_json
                    ) VALUES (
                        CAST(:user_id AS uuid), 'Private attacker food',
                        CAST(:community AS jsonb), '[]'::jsonb
                    ) RETURNING id
                    """
                ),
                {"user_id": attacker_user_id, "community": _COMMUNITY_NUTRIENTS},
            )
            owner_custom_id = await connection.scalar(
                text(
                    """
                    INSERT INTO foods_custom (
                        user_id, name, nutrients_json, portions_json
                    ) VALUES (
                        CAST(:user_id AS uuid), 'Private owner food',
                        CAST(:community AS jsonb), '[]'::jsonb
                    ) RETURNING id
                    """
                ),
                {"user_id": owner_user_id, "community": _COMMUNITY_NUTRIENTS},
            )
            return str(attacker_custom_id), str(owner_custom_id)
    finally:
        await engine.dispose()


async def _change_usda_food(database_url: str) -> None:
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
    finally:
        await engine.dispose()


async def _set_user_timezone(database_url: str, user_id: str, timezone: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE users
                    SET settings_json = jsonb_build_object('timezone', CAST(:timezone AS text))
                    WHERE id = CAST(:user_id AS uuid)
                    """
                ),
                {"timezone": timezone, "user_id": user_id},
            )
    finally:
        await engine.dispose()


@pytest.fixture
def log_clients() -> Iterator[LogClients]:
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
            json={"email": "owner@example.test", "password": "owner password 123"},
        )
        attacker_registration = attacker.post(
            "/api/v1/auth/register",
            json={"email": "attacker@example.test", "password": "attacker password 123"},
        )
        assert owner_registration.status_code == 201
        assert attacker_registration.status_code == 201
        attacker_custom_id, owner_custom_id = asyncio.run(
            _seed_foods(
                INTEGRATION_DATABASE_URL,
                attacker_registration.json()["user"]["id"],
                owner_registration.json()["user"]["id"],
            )
        )
        yield LogClients(
            owner=owner,
            attacker=attacker,
            owner_csrf=owner_registration.json()["csrf_token"],
            attacker_csrf=attacker_registration.json()["csrf_token"],
            attacker_custom_food_id=attacker_custom_id,
            owner_custom_food_id=owner_custom_id,
            owner_user_id=owner_registration.json()["user"]["id"],
        )


def _create(
    client: TestClient,
    csrf_token: str,
    *,
    logged_at: str = "2026-08-20T12:00:00Z",
    meal_slot: str = "lunch",
    food: dict[str, str] | None = None,
    quantity: dict[str, str] | None = None,
):
    return client.post(
        "/api/v1/logs",
        headers={"X-CSRF-Token": csrf_token},
        json={
            "logged_at": logged_at,
            "meal_slot": meal_slot,
            "food": food or {"source": "usda", "source_id": "100"},
            "quantity": quantity or {"amount": "50", "unit": "g"},
        },
    )


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_create_list_detail_totals_and_immutable_snapshot(log_clients: LogClients) -> None:
    first = _create(log_clients.owner, log_clients.owner_csrf)
    second = _create(
        log_clients.owner,
        log_clients.owner_csrf,
        logged_at="2026-08-20T18:00:00Z",
        meal_slot="dinner",
        food={"source": "community", "source_id": "dal-rice"},
        quantity={"amount": "1", "unit": "portion", "portion_name": "1 bowl"},
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.headers["cache-control"] == "no-store"
    assert first.json()["food"] == {
        "source": "usda",
        "source_id": "100",
        "name": "Stable oats",
    }
    assert first.json()["snapshot"]["grams"] == "50.00"
    assert first.json()["snapshot"]["nutrients"]["energy_kcal"] == "50.00"
    assert second.json()["snapshot"]["grams"] == "200.00"
    assert second.json()["snapshot"]["nutrients"]["energy_kcal"] == "380.00"

    page = log_clients.owner.get(
        "/api/v1/logs",
        params={"day": "2026-08-20", "timezone": "UTC", "limit": 1},
    )
    assert page.status_code == 200
    assert page.json()["has_more"] is True
    assert [item["meal_slot"] for item in page.json()["items"]] == ["lunch"]
    assert log_clients.owner.get(f"/api/v1/logs/{first.json()['id']}").status_code == 200

    totals = log_clients.owner.get(
        "/api/v1/logs/daily-totals",
        params={"day": "2026-08-20", "timezone": "UTC"},
    )
    assert totals.status_code == 200
    assert totals.json()["entry_count"] == 2
    assert totals.json()["grams"] == "250.00"
    assert totals.json()["nutrients"]["energy_kcal"] == "430.00"

    assert INTEGRATION_DATABASE_URL is not None
    asyncio.run(_change_usda_food(INTEGRATION_DATABASE_URL))
    unchanged = log_clients.owner.get(f"/api/v1/logs/{first.json()['id']}")
    assert unchanged.json()["food"]["name"] == "Stable oats"
    assert unchanged.json()["snapshot"]["nutrients"]["energy_kcal"] == "50.00"


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_create_supports_every_v1_food_store_and_authoritative_usda(
    log_clients: LogClients,
) -> None:
    cases = (
        (
            {"source": "usda", "source_id": "101"},
            "Published energy-factor food",
            "50.00",
        ),
        (
            {"source": "openfoodfacts", "source_id": "0012345678905"},
            "Packaged dal",
            "95.00",
        ),
        (
            {"source": "custom", "source_id": log_clients.owner_custom_food_id},
            "Private owner food",
            "95.00",
        ),
    )
    for food, expected_name, expected_energy in cases:
        created = _create(
            log_clients.owner,
            log_clients.owner_csrf,
            food=food,
        )
        assert created.status_code == 201
        assert created.json()["food"] == {**food, "name": expected_name}
        assert created.json()["snapshot"]["nutrients"]["energy_kcal"] == expected_energy


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_daily_queries_use_local_day_boundaries_across_dst(log_clients: LogClients) -> None:
    timestamps = (
        "2026-03-08T04:59:59Z",
        "2026-03-08T05:00:00Z",
        "2026-03-09T03:59:59Z",
        "2026-03-09T04:00:00Z",
    )
    for index, logged_at in enumerate(timestamps):
        response = _create(
            log_clients.owner,
            log_clients.owner_csrf,
            logged_at=logged_at,
            meal_slot=f"slot-{index}",
        )
        assert response.status_code == 201

    assert INTEGRATION_DATABASE_URL is not None
    asyncio.run(
        _set_user_timezone(
            INTEGRATION_DATABASE_URL,
            log_clients.owner_user_id,
            "America/New_York",
        )
    )
    new_york = log_clients.owner.get(
        "/api/v1/logs",
        params={"day": "2026-03-08"},
    )
    assert new_york.json()["timezone"] == "America/New_York"
    assert [item["meal_slot"] for item in new_york.json()["items"]] == ["slot-1", "slot-2"]
    new_york_totals = log_clients.owner.get(
        "/api/v1/logs/daily-totals",
        params={"day": "2026-03-08"},
    )
    assert new_york_totals.json()["timezone"] == "America/New_York"
    assert new_york_totals.json()["entry_count"] == 2
    assert new_york_totals.json()["grams"] == "100.00"
    utc = log_clients.owner.get(
        "/api/v1/logs",
        params={"day": "2026-03-08", "timezone": "UTC"},
    )
    assert [item["meal_slot"] for item in utc.json()["items"]] == ["slot-0", "slot-1"]

    fall_timestamps = (
        "2026-11-01T03:59:59Z",
        "2026-11-01T04:00:00Z",
        "2026-11-02T04:59:59Z",
        "2026-11-02T05:00:00Z",
    )
    for index, logged_at in enumerate(fall_timestamps):
        response = _create(
            log_clients.owner,
            log_clients.owner_csrf,
            logged_at=logged_at,
            meal_slot=f"fall-{index}",
        )
        assert response.status_code == 201
    fall_day = log_clients.owner.get(
        "/api/v1/logs",
        params={"day": "2026-11-01"},
    )
    assert [item["meal_slot"] for item in fall_day.json()["items"]] == [
        "fall-1",
        "fall-2",
    ]
    fall_totals = log_clients.owner.get(
        "/api/v1/logs/daily-totals",
        params={"day": "2026-11-01"},
    )
    assert fall_totals.json()["entry_count"] == 2
    assert fall_totals.json()["grams"] == "100.00"


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_every_log_endpoint_is_tenant_isolated(log_clients: LogClients) -> None:
    created = _create(log_clients.owner, log_clients.owner_csrf)
    entry_id = created.json()["id"]

    attacker_detail = log_clients.attacker.get(f"/api/v1/logs/{entry_id}")
    assert attacker_detail.status_code == 404
    assert attacker_detail.headers["cache-control"] == "no-store"
    attacker_list = log_clients.attacker.get(
        "/api/v1/logs", params={"day": "2026-08-20", "timezone": "UTC"}
    )
    assert attacker_list.json()["items"] == []
    attacker_totals = log_clients.attacker.get(
        "/api/v1/logs/daily-totals",
        params={"day": "2026-08-20", "timezone": "UTC"},
    )
    assert attacker_totals.json()["entry_count"] == 0
    assert attacker_totals.json()["grams"] == "0.00"
    assert attacker_totals.json()["nutrients"] == {}
    assert (
        log_clients.attacker.delete(
            f"/api/v1/logs/{entry_id}",
            headers={"X-CSRF-Token": log_clients.attacker_csrf},
        ).status_code
        == 404
    )
    private_food = _create(
        log_clients.owner,
        log_clients.owner_csrf,
        food={"source": "custom", "source_id": log_clients.attacker_custom_food_id},
    )
    assert private_food.status_code == 404

    deleted = log_clients.owner.delete(
        f"/api/v1/logs/{entry_id}",
        headers={"X-CSRF-Token": log_clients.owner_csrf},
    )
    assert deleted.status_code == 204
    assert log_clients.owner.get(f"/api/v1/logs/{entry_id}").status_code == 404


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_log_validation_and_authentication_errors_are_stable(log_clients: LogClients) -> None:
    missing_csrf = log_clients.owner.post("/api/v1/logs", json={})
    assert missing_csrf.status_code == 403
    assert missing_csrf.headers["cache-control"] == "no-store"
    assert INTEGRATION_DATABASE_URL is not None
    unauthenticated_settings = Settings(
        database_url=INTEGRATION_DATABASE_URL,
        app_environment="test",
        _env_file=None,
    )
    with TestClient(create_app(unauthenticated_settings)) as unauthenticated:
        unauthenticated_list = unauthenticated.get(
            "/api/v1/logs", params={"day": "2026-08-20", "timezone": "UTC"}
        )
        assert unauthenticated_list.status_code == 401
        assert unauthenticated_list.headers["cache-control"] == "no-store"

    invalid_payloads = (
        {"quantity": {"amount": "0", "unit": "g"}},
        {"logged_at": "2026-08-20T12:00:00"},
        {"meal_slot": "bad\nslot"},
    )
    for changes in invalid_payloads:
        payload = {
            "logged_at": "2026-08-20T12:00:00Z",
            "meal_slot": "lunch",
            "food": {"source": "usda", "source_id": "100"},
            "quantity": {"amount": "50", "unit": "g"},
            **changes,
        }
        response = log_clients.owner.post(
            "/api/v1/logs",
            headers={"X-CSRF-Token": log_clients.owner_csrf},
            json=payload,
        )
        assert response.status_code == 422
        assert response.headers["cache-control"] == "no-store"

    millilitres = _create(
        log_clients.owner,
        log_clients.owner_csrf,
        quantity={"amount": "100", "unit": "ml"},
    )
    assert millilitres.status_code == 422
    assert "density_g_per_ml" in millilitres.json()["detail"]
    missing_portion = _create(
        log_clients.owner,
        log_clients.owner_csrf,
        quantity={"amount": "1", "unit": "portion", "portion_name": "missing"},
    )
    assert missing_portion.status_code == 422
    assert "Unknown household portion" in missing_portion.json()["detail"]

    for params in (
        {"day": "2026-08-20", "timezone": "Unknown/Nowhere"},
        {"day": "2026-08-20", "timezone": "UTC", "limit": 101},
        {"day": "2026-08-20", "timezone": "UTC", "offset": 10001},
    ):
        assert log_clients.owner.get("/api/v1/logs", params=params).status_code == 422

    for path, params in (
        ("/api/v1/logs", {"day": "9999-12-31", "timezone": "UTC"}),
        (
            "/api/v1/logs/daily-totals",
            {"day": "0001-01-01", "timezone": "Asia/Kolkata"},
        ),
    ):
        response = log_clients.owner.get(path, params=params)
        assert response.status_code == 422
        assert response.json()["detail"] == "day is outside the supported timezone range"
