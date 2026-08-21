from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest
from alembic import command
from fastapi.testclient import TestClient
from opennosh_api.main import create_app
from opennosh_api.settings import Settings
from opennosh_api.targets import service as target_service
from opennosh_api.targets.schemas import TargetScheduleWrite
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.tests.test_migrations import migration_config

INTEGRATION_DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL")


@dataclass(frozen=True)
class TargetClients:
    owner: TestClient
    attacker: TestClient
    owner_csrf: str
    attacker_csrf: str
    owner_user_id: str


async def _reset_database(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("TRUNCATE auth_rate_limits, auth_sessions, targets, users CASCADE")
            )
    finally:
        await engine.dispose()


@pytest.fixture
def target_clients() -> Iterator[TargetClients]:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_reset_database(INTEGRATION_DATABASE_URL))
    settings = Settings(
        database_url=INTEGRATION_DATABASE_URL,
        app_environment="test",
        auth_rate_limit_attempts=50,
        target_kcal_floor="1200",
        _env_file=None,
    )
    with (
        TestClient(create_app(settings)) as owner,
        TestClient(create_app(settings)) as attacker,
    ):
        owner_registration = owner.post(
            "/api/v1/auth/register",
            json={"email": "target-owner@example.test", "password": "owner password 123"},
        )
        attacker_registration = attacker.post(
            "/api/v1/auth/register",
            json={
                "email": "target-attacker@example.test",
                "password": "attacker password 123",
            },
        )
        assert owner_registration.status_code == 201
        assert attacker_registration.status_code == 201
        yield TargetClients(
            owner=owner,
            attacker=attacker,
            owner_csrf=owner_registration.json()["csrf_token"],
            attacker_csrf=attacker_registration.json()["csrf_token"],
            owner_user_id=owner_registration.json()["user"]["id"],
        )


def _schedule(*, rest_kcal: str = "2100") -> dict[str, object]:
    return {
        "items": [
            {
                "day_type": "training",
                "kcal": "2500",
                "protein_g": "180",
                "carb_g": "300",
                "fat_g": "65",
                "active_from": "2026-08-01",
                "active_until": "2026-08-31",
            },
            {
                "day_type": "training",
                "kcal": "2600",
                "protein_g": "185",
                "carb_g": "315",
                "fat_g": "65",
                "active_from": "2026-09-01",
                "active_until": None,
            },
            {
                "day_type": "rest",
                "kcal": rest_kcal,
                "protein_g": "180",
                "carb_g": "200",
                "fat_g": "65",
                "active_from": "2026-08-01",
                "active_until": None,
            },
        ]
    }


def _put(client: TestClient, csrf: str, payload: dict[str, object]):
    return client.put(
        "/api/v1/targets",
        headers={"X-CSRF-Token": csrf},
        json=payload,
    )


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_target_schedule_replacement_and_day_type_resolution(
    target_clients: TargetClients,
) -> None:
    saved = _put(target_clients.owner, target_clients.owner_csrf, _schedule())

    assert saved.status_code == 200
    assert saved.headers["cache-control"] == "no-store"
    assert saved.json()["target_kcal_floor"] == "1200.00"
    assert "does not prescribe" in saved.json()["safety_copy"]
    assert [item["day_type"] for item in saved.json()["items"]] == [
        "rest",
        "training",
        "training",
    ]

    august_training = target_clients.owner.get(
        "/api/v1/targets/resolve",
        params={"day": "2026-08-31", "day_type": "training"},
    )
    september_training = target_clients.owner.get(
        "/api/v1/targets/resolve",
        params={"day": "2026-09-01", "day_type": "training"},
    )
    rest = target_clients.owner.get(
        "/api/v1/targets/resolve",
        params={"day": "2026-09-01", "day_type": "rest"},
    )
    missing = target_clients.owner.get(
        "/api/v1/targets/resolve",
        params={"day": "2026-07-31", "day_type": "training"},
    )

    assert august_training.json()["kcal"] == "2500.00"
    assert september_training.json()["kcal"] == "2600.00"
    assert rest.json()["kcal"] == "2100.00"
    assert missing.status_code == 404
    assert missing.headers["cache-control"] == "no-store"

    replaced = _put(target_clients.owner, target_clients.owner_csrf, _schedule(rest_kcal="2200"))
    assert replaced.status_code == 200
    assert len(target_clients.owner.get("/api/v1/targets").json()["items"]) == 3
    assert target_clients.owner.get(
        "/api/v1/targets/resolve",
        params={"day": "2026-09-01", "day_type": "rest"},
    ).json()["kcal"] == "2200.00"


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_below_floor_target_requires_and_records_explicit_confirmation(
    target_clients: TargetClients,
) -> None:
    payload = _schedule(rest_kcal="1199.99")

    rejected = _put(target_clients.owner, target_clients.owner_csrf, payload)
    assert rejected.status_code == 422
    assert rejected.json()["detail"] == (
        "This value is below the configured safety floor of 1200.00 kcal. "
        "Confirm this specific target in settings to save the value you entered."
    )
    assert target_clients.owner.get("/api/v1/targets").json()["items"] == []

    items = payload["items"]
    assert isinstance(items, list)
    rest = items[2]
    assert isinstance(rest, dict)
    rest["confirm_below_floor"] = True
    confirmed = _put(target_clients.owner, target_clients.owner_csrf, payload)

    assert confirmed.status_code == 200
    confirmed_rest = confirmed.json()["items"][0]
    assert confirmed_rest["kcal"] == "1199.99"
    assert confirmed_rest["below_floor_confirmed"] is True
    assert confirmed_rest["safety_review_required"] is False
    assert confirmed_rest["safety_floor_kcal"] == "1200.00"


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_raised_floor_requires_review_only_for_targets_without_confirmation(
    target_clients: TargetClients,
) -> None:
    assert _put(
        target_clients.owner,
        target_clients.owner_csrf,
        _schedule(rest_kcal="1300"),
    ).status_code == 200
    assert INTEGRATION_DATABASE_URL is not None
    raised_settings = Settings(
        database_url=INTEGRATION_DATABASE_URL,
        app_environment="test",
        auth_rate_limit_attempts=50,
        target_kcal_floor="1500",
        _env_file=None,
    )
    with TestClient(create_app(raised_settings)) as raised_floor:
        raised_floor.cookies.update(target_clients.owner.cookies)
        rest = next(
            item
            for item in raised_floor.get("/api/v1/targets").json()["items"]
            if item["day_type"] == "rest"
        )
        assert rest["safety_review_required"] is True
        assert raised_floor.get(
            "/api/v1/targets/resolve",
            params={"day": "2026-09-01", "day_type": "rest"},
        ).status_code == 404

    confirmed_payload = _schedule(rest_kcal="1100")
    items = confirmed_payload["items"]
    assert isinstance(items, list)
    confirmed_rest = items[2]
    assert isinstance(confirmed_rest, dict)
    confirmed_rest["confirm_below_floor"] = True
    assert _put(
        target_clients.owner,
        target_clients.owner_csrf,
        confirmed_payload,
    ).status_code == 200
    with TestClient(create_app(raised_settings)) as raised_floor:
        raised_floor.cookies.update(target_clients.owner.cookies)
        resolved = raised_floor.get(
            "/api/v1/targets/resolve",
            params={"day": "2026-09-01", "day_type": "rest"},
        )
        assert resolved.status_code == 200
        assert resolved.json()["kcal"] == "1100.00"


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_all_target_operations_are_authenticated_csrf_protected_and_owner_scoped(
    target_clients: TargetClients,
) -> None:
    owner_saved = _put(target_clients.owner, target_clients.owner_csrf, _schedule())
    assert owner_saved.status_code == 200

    assert target_clients.attacker.get("/api/v1/targets").json()["items"] == []
    assert target_clients.attacker.get(
        "/api/v1/targets/resolve",
        params={"day": "2026-08-20", "day_type": "training"},
    ).status_code == 404

    attacker_saved = _put(
        target_clients.attacker,
        target_clients.attacker_csrf,
        {"items": [_schedule()["items"][2]]},
    )
    assert attacker_saved.status_code == 200
    assert len(target_clients.owner.get("/api/v1/targets").json()["items"]) == 3
    assert len(target_clients.attacker.get("/api/v1/targets").json()["items"]) == 1

    assert target_clients.owner.put("/api/v1/targets", json={"items": []}).status_code == 403
    target_clients.owner.cookies.clear()
    assert target_clients.owner.get("/api/v1/targets").status_code == 401

    injected = _put(
        target_clients.attacker,
        target_clients.attacker_csrf,
        {
            "items": [
                {
                    **_schedule()["items"][0],
                    "user_id": "8eb1a7ea-c696-4d70-a922-02b1604f1d70",
                }
            ]
        },
    )
    assert injected.status_code == 422


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_invalid_replacement_is_atomic_and_private_errors_are_not_cached(
    target_clients: TargetClients,
) -> None:
    assert _put(target_clients.owner, target_clients.owner_csrf, _schedule()).status_code == 200

    invalid = _schedule()
    items = invalid["items"]
    assert isinstance(items, list)
    second = items[1]
    assert isinstance(second, dict)
    second["active_from"] = "2026-08-15"
    rejected = _put(target_clients.owner, target_clients.owner_csrf, invalid)

    assert rejected.status_code == 422
    assert rejected.headers["cache-control"] == "no-store"
    persisted = target_clients.owner.get("/api/v1/targets")
    assert [item["kcal"] for item in persisted.json()["items"]] == [
        "2100.00",
        "2500.00",
        "2600.00",
    ]


async def _assert_concurrent_replacements_serialize(
    database_url: str, user_id: str
) -> None:
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    first_has_lock = asyncio.Event()
    second_attempted_lock = asyncio.Event()
    second_has_lock = asyncio.Event()
    release_first = asyncio.Event()
    calls = 0
    original_lock = target_service._lock_owner

    async def gated_lock(database, owner_id):
        nonlocal calls
        calls += 1
        call_number = calls
        if call_number == 2:
            second_attempted_lock.set()
        await original_lock(database, owner_id)
        if call_number == 1:
            first_has_lock.set()
            await release_first.wait()
        else:
            second_has_lock.set()

    target_service._lock_owner = gated_lock
    current = SimpleNamespace(user_id=UUID(user_id))
    first_payload = TargetScheduleWrite.model_validate(_schedule(rest_kcal="2100"))
    second_payload = TargetScheduleWrite.model_validate(_schedule(rest_kcal="2200"))
    try:
        async with sessions() as first_database, sessions() as second_database:
            first = asyncio.create_task(
                target_service.replace_targets(
                    first_database,
                    first_payload,
                    current=current,
                    target_kcal_floor=Decimal("1200"),
                )
            )
            await asyncio.wait_for(first_has_lock.wait(), timeout=2)
            second = asyncio.create_task(
                target_service.replace_targets(
                    second_database,
                    second_payload,
                    current=current,
                    target_kcal_floor=Decimal("1200"),
                )
            )
            await asyncio.wait_for(second_attempted_lock.wait(), timeout=2)
            assert not second_has_lock.is_set()
            release_first.set()
            await asyncio.gather(first, second)
            assert second_has_lock.is_set()
    finally:
        target_service._lock_owner = original_lock
        await engine.dispose()


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_concurrent_schedule_replacements_serialize(
    target_clients: TargetClients,
) -> None:
    assert INTEGRATION_DATABASE_URL is not None
    asyncio.run(
        _assert_concurrent_replacements_serialize(
            INTEGRATION_DATABASE_URL,
            target_clients.owner_user_id,
        )
    )
    resolved = target_clients.owner.get(
        "/api/v1/targets/resolve",
        params={"day": "2026-09-01", "day_type": "rest"},
    )
    assert resolved.status_code == 200
    assert resolved.json()["kcal"] == "2200.00"
