from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from dataclasses import dataclass
from uuid import uuid4

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
class BodyMetricClients:
    owner: TestClient
    attacker: TestClient
    anonymous: TestClient
    owner_csrf: str
    attacker_csrf: str


async def _reset_database(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("TRUNCATE auth_rate_limits, auth_sessions, body_metrics, users CASCADE")
            )
    finally:
        await engine.dispose()


@pytest.fixture
def body_metric_clients() -> Iterator[BodyMetricClients]:
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
        TestClient(create_app(settings)) as anonymous,
    ):
        owner_registration = owner.post(
            "/api/v1/auth/register",
            json={"email": "metric-owner@example.test", "password": "owner password 123"},
        )
        attacker_registration = attacker.post(
            "/api/v1/auth/register",
            json={
                "email": "metric-attacker@example.test",
                "password": "attacker password 123",
            },
        )
        assert owner_registration.status_code == 201
        assert attacker_registration.status_code == 201
        yield BodyMetricClients(
            owner=owner,
            attacker=attacker,
            anonymous=anonymous,
            owner_csrf=owner_registration.json()["csrf_token"],
            attacker_csrf=attacker_registration.json()["csrf_token"],
        )


def _post(
    client: TestClient,
    csrf: str,
    *,
    recorded_at: str,
    metric_type: str = "body_weight",
    value: str = "80.125",
    unit: str = "kg",
):
    return client.post(
        "/api/v1/body-metrics",
        headers={"X-CSRF-Token": csrf},
        json={
            "recorded_at": recorded_at,
            "metric_type": metric_type,
            "value": value,
            "unit": unit,
        },
    )


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_create_list_paginate_and_delete_private_body_metrics(
    body_metric_clients: BodyMetricClients,
) -> None:
    first = _post(
        body_metric_clients.owner,
        body_metric_clients.owner_csrf,
        recorded_at="2026-08-20T23:30:00-04:00",
    )
    second = _post(
        body_metric_clients.owner,
        body_metric_clients.owner_csrf,
        recorded_at="2026-08-21T12:00:00Z",
        metric_type="waist_circumference",
        value="84.2",
        unit="cm",
    )
    outside = _post(
        body_metric_clients.owner,
        body_metric_clients.owner_csrf,
        recorded_at="2026-08-22T00:00:00Z",
        value="79.9",
    )

    assert first.status_code == second.status_code == outside.status_code == 201
    assert first.headers["cache-control"] == "no-store"
    assert first.json()["value"] == "80.1250"
    assert first.json()["recorded_at"] == "2026-08-21T03:30:00Z"
    assert set(first.json()) == {"id", "recorded_at", "metric_type", "value", "unit"}

    listed = body_metric_clients.owner.get(
        "/api/v1/body-metrics",
        params={"from": "2026-08-21", "to": "2026-08-21", "limit": 1},
    )
    assert listed.status_code == 200
    assert listed.headers["cache-control"] == "no-store"
    assert listed.json()["from_date"] == "2026-08-21"
    assert listed.json()["to_date"] == "2026-08-21"
    assert listed.json()["has_more"] is True
    assert listed.json()["items"][0]["metric_type"] == "waist_circumference"

    next_page = body_metric_clients.owner.get(
        "/api/v1/body-metrics",
        params={"from": "2026-08-21", "to": "2026-08-21", "limit": 1, "offset": 1},
    )
    assert next_page.json()["has_more"] is False
    assert next_page.json()["items"][0]["id"] == first.json()["id"]
    assert next_page.json()["items"][0] == first.json()

    deleted = body_metric_clients.owner.delete(
        f"/api/v1/body-metrics/{first.json()['id']}",
        headers={"X-CSRF-Token": body_metric_clients.owner_csrf},
    )
    assert deleted.status_code == 204
    assert deleted.headers["cache-control"] == "no-store"
    assert body_metric_clients.owner.delete(
        f"/api/v1/body-metrics/{first.json()['id']}",
        headers={"X-CSRF-Token": body_metric_clients.owner_csrf},
    ).status_code == 404


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_all_operations_are_authenticated_csrf_protected_and_tenant_isolated(
    body_metric_clients: BodyMetricClients,
) -> None:
    created = _post(
        body_metric_clients.owner,
        body_metric_clients.owner_csrf,
        recorded_at="2026-08-20T12:00:00Z",
    )
    metric_id = created.json()["id"]

    owner_override = body_metric_clients.owner.post(
        "/api/v1/body-metrics",
        headers={"X-CSRF-Token": body_metric_clients.owner_csrf},
        json={
            "user_id": str(uuid4()),
            "recorded_at": "2026-08-20T12:00:00Z",
            "metric_type": "body_weight",
            "value": "80",
            "unit": "kg",
        },
    )
    assert owner_override.status_code == 422
    assert owner_override.headers["cache-control"] == "no-store"

    attacker_list = body_metric_clients.attacker.get(
        "/api/v1/body-metrics", params={"from": "2026-08-20", "to": "2026-08-20"}
    )
    assert attacker_list.status_code == 200
    assert attacker_list.json()["items"] == []

    attacker_delete = body_metric_clients.attacker.delete(
        f"/api/v1/body-metrics/{metric_id}",
        headers={"X-CSRF-Token": body_metric_clients.attacker_csrf},
    )
    missing_delete = body_metric_clients.attacker.delete(
        f"/api/v1/body-metrics/{uuid4()}",
        headers={"X-CSRF-Token": body_metric_clients.attacker_csrf},
    )
    assert attacker_delete.status_code == missing_delete.status_code == 404
    assert attacker_delete.json() == missing_delete.json() == {"detail": "Body metric not found"}

    missing_csrf = body_metric_clients.owner.delete(f"/api/v1/body-metrics/{metric_id}")
    assert missing_csrf.status_code == 403
    assert missing_csrf.headers["cache-control"] == "no-store"
    assert body_metric_clients.owner.get(
        "/api/v1/body-metrics", params={"from": "2026-08-20", "to": "2026-08-20"}
    ).json()["items"][0]["id"] == metric_id

    for method, path, kwargs in (
        ("get", "/api/v1/body-metrics", {"params": {"from": "2026-08-20", "to": "2026-08-20"}}),
        (
            "post",
            "/api/v1/body-metrics",
            {
                "json": {
                    "recorded_at": "2026-08-20T12:00:00Z",
                    "metric_type": "body_weight",
                    "value": "80",
                    "unit": "kg",
                }
            },
        ),
        ("delete", f"/api/v1/body-metrics/{metric_id}", {}),
    ):
        response = getattr(body_metric_clients.anonymous, method)(path, **kwargs)
        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store"


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_invalid_units_and_date_ranges_return_private_validation_errors(
    body_metric_clients: BodyMetricClients,
) -> None:
    invalid_unit = _post(
        body_metric_clients.owner,
        body_metric_clients.owner_csrf,
        recorded_at="2026-08-20T12:00:00Z",
        unit="percent",
    )
    assert invalid_unit.status_code == 422
    assert invalid_unit.headers["cache-control"] == "no-store"

    reversed_range = body_metric_clients.owner.get(
        "/api/v1/body-metrics", params={"from": "2026-08-21", "to": "2026-08-20"}
    )
    minimum_sentinel = _post(
        body_metric_clients.owner,
        body_metric_clients.owner_csrf,
        recorded_at="0001-01-01T00:00:00Z",
    )
    minimum = _post(
        body_metric_clients.owner,
        body_metric_clients.owner_csrf,
        recorded_at="0001-01-01T00:00:00.000001Z",
    )
    minimum_range = body_metric_clients.owner.get(
        "/api/v1/body-metrics", params={"from": "0001-01-01", "to": "0001-01-01"}
    )
    maximum = _post(
        body_metric_clients.owner,
        body_metric_clients.owner_csrf,
        recorded_at="9999-12-31T23:59:59.999998Z",
    )
    maximum_range = body_metric_clients.owner.get(
        "/api/v1/body-metrics", params={"from": "9999-12-31", "to": "9999-12-31"}
    )
    assert reversed_range.status_code == 422
    assert reversed_range.json()["detail"] == "from must be on or before to"
    assert reversed_range.headers["cache-control"] == "no-store"
    assert minimum_sentinel.status_code == 422
    assert minimum.status_code == 201
    assert minimum_range.status_code == 200
    assert minimum_range.json()["items"] == [minimum.json()]
    assert maximum.status_code == 201
    assert maximum_range.status_code == 200
    assert maximum_range.json()["items"] == [maximum.json()]
