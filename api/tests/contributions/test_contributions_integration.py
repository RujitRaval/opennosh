from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from dataclasses import dataclass
from math import ceil
from time import perf_counter
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
class ContributionClients:
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
                text("TRUNCATE auth_rate_limits, auth_sessions, contribution_drafts, users CASCADE")
            )
    finally:
        await engine.dispose()


async def _backdate_operation(database_url: str, operation_id: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE contribution_draft_operations "
                    "SET created_at = now() - INTERVAL '9 days' "
                    "WHERE operation_id = :operation_id"
                ),
                {"operation_id": operation_id},
            )
    finally:
        await engine.dispose()


@pytest.fixture
def contribution_clients() -> Iterator[ContributionClients]:
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
            json={"email": "contributor@example.test", "password": "owner password 123"},
        )
        attacker_registration = attacker.post(
            "/api/v1/auth/register",
            json={"email": "attacker@example.test", "password": "attacker password 123"},
        )
        assert owner_registration.status_code == attacker_registration.status_code == 201
        yield ContributionClients(
            owner=owner,
            attacker=attacker,
            anonymous=anonymous,
            owner_csrf=owner_registration.json()["csrf_token"],
            attacker_csrf=attacker_registration.json()["csrf_token"],
        )


def _complete_patches() -> list[dict[str, object]]:
    return [
        {"field": "evidence_type", "value": "public_document"},
        {"field": "source_uri", "value": "https://example.test/food-source"},
        {"field": "rights_acknowledged", "value": True},
        {"field": "name", "value": "Test dal"},
        {"field": "locale", "value": "en-IN"},
        {"field": "category", "value": "meal"},
        {"field": "portion_description", "value": "1 bowl"},
        {"field": "portion_amount", "value": "1"},
        {"field": "portion_unit", "value": "serving"},
        {"field": "portion_grams", "value": "240"},
        {"field": "energy_kcal", "value": "280"},
        {"field": "protein_g", "value": "18"},
        {"field": "fat_g", "value": "6"},
        {"field": "carbohydrate_g", "value": "40"},
        {"field": "pack_id", "value": "global-core"},
        {"field": "source_date", "value": "2026-08-20"},
        {"field": "attribution", "value": "Integration Contributor"},
        {"field": "source_license", "value": "contributor-original"},
        {"field": "review_acknowledged", "value": True},
    ]


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_contribution_lifecycle_is_isolated_versioned_and_idempotent(
    contribution_clients: ContributionClients,
) -> None:
    route = "/api/v1/contribution-drafts"
    assert contribution_clients.anonymous.post(route, json={}).status_code == 401
    assert contribution_clients.owner.post(route, json={}).status_code == 403

    create_payload = {"client_draft_id": "device-draft-1"}
    created = contribution_clients.owner.post(
        route,
        headers={"X-CSRF-Token": contribution_clients.owner_csrf},
        json=create_payload,
    )
    retried_create = contribution_clients.owner.post(
        route,
        headers={"X-CSRF-Token": contribution_clients.owner_csrf},
        json=create_payload,
    )
    assert created.status_code == retried_create.status_code == 201
    assert created.headers["cache-control"] == "no-store"
    assert created.json()["draft_id"] == retried_create.json()["draft_id"]
    draft_id = created.json()["draft_id"]

    hidden = contribution_clients.attacker.get(f"{route}/{draft_id}")
    absent = contribution_clients.attacker.get(f"{route}/{uuid4()}")
    assert hidden.status_code == absent.status_code == 404
    read = contribution_clients.owner.get(f"{route}/{draft_id}")
    assert read.headers["cache-control"] == "no-store"

    operation_id = str(uuid4())
    patched = contribution_clients.owner.patch(
        f"{route}/{draft_id}",
        headers={"X-CSRF-Token": contribution_clients.owner_csrf},
        json={
            "expected_draft_version": 1,
            "operation_id": operation_id,
            "requested_stage": "review",
            "patches": _complete_patches(),
        },
    )
    assert patched.status_code == 200
    assert patched.headers["cache-control"] == "no-store"
    assert patched.json()["draft_version"] == 2
    assert patched.json()["resolved_stage"] == "review"
    assert patched.json()["completed_stages"] == [
        "evidence",
        "details",
        "duplicates",
        "provenance",
    ]

    retried_patch = contribution_clients.owner.patch(
        f"{route}/{draft_id}",
        headers={"X-CSRF-Token": contribution_clients.owner_csrf},
        json={
            "expected_draft_version": 1,
            "operation_id": operation_id,
            "requested_stage": "review",
            "patches": _complete_patches(),
        },
    )
    assert retried_patch.status_code == 200
    assert retried_patch.json()["draft_version"] == 2

    stale = contribution_clients.owner.patch(
        f"{route}/{draft_id}",
        headers={"X-CSRF-Token": contribution_clients.owner_csrf},
        json={
            "expected_draft_version": 1,
            "operation_id": str(uuid4()),
            "patches": [{"field": "category", "value": "prepared meal"}],
        },
    )
    assert stale.status_code == 409

    submission_key = str(uuid4())
    submitted = contribution_clients.owner.post(
        f"{route}/{draft_id}/submit",
        headers={"X-CSRF-Token": contribution_clients.owner_csrf},
        json={"expected_draft_version": 2, "idempotency_key": submission_key},
    )
    assert submitted.status_code == 200
    assert submitted.headers["cache-control"] == "no-store"
    assert submitted.json()["review_state"] == "in_review"
    assert submitted.json()["receipt"]["status"] == "received_for_review"
    assert submitted.json()["receipt"]["status_href"] == f"/en/contribute/{draft_id}/status"

    retried_submit = contribution_clients.owner.post(
        f"{route}/{draft_id}/submit",
        headers={"X-CSRF-Token": contribution_clients.owner_csrf},
        json={"expected_draft_version": 2, "idempotency_key": submission_key},
    )
    assert retried_submit.status_code == 200
    assert retried_submit.json()["receipt"] == submitted.json()["receipt"]

    locked = contribution_clients.owner.patch(
        f"{route}/{draft_id}",
        headers={"X-CSRF-Token": contribution_clients.owner_csrf},
        json={
            "expected_draft_version": 3,
            "operation_id": str(uuid4()),
            "patches": [{"field": "category", "value": "changed"}],
        },
    )
    assert locked.status_code == 422


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_contribution_autosave_merges_only_unchanged_base_fields(
    contribution_clients: ContributionClients,
) -> None:
    route = "/api/v1/contribution-drafts"
    created = contribution_clients.owner.post(
        route,
        headers={"X-CSRF-Token": contribution_clients.owner_csrf},
        json={"client_draft_id": "autosave-conflict-draft"},
    )
    assert created.status_code == 201
    draft_id = created.json()["draft_id"]
    headers = {"X-CSRF-Token": contribution_clients.owner_csrf}

    first = contribution_clients.owner.patch(
        f"{route}/{draft_id}",
        headers=headers,
        json={
            "expected_draft_version": 1,
            "operation_id": str(uuid4()),
            "patches": [{
                "field": "name",
                "value": "Dal",
                "base_value": None,
                "base_version": 1,
            }],
        },
    )
    assert first.status_code == 200
    assert first.json()["draft_version"] == 2

    disjoint = contribution_clients.owner.patch(
        f"{route}/{draft_id}",
        headers=headers,
        json={
            "expected_draft_version": 1,
            "operation_id": str(uuid4()),
            "patches": [{
                "field": "category",
                "value": "meal",
                "base_value": None,
                "base_version": 1,
            }],
        },
    )
    assert disjoint.status_code == 200
    assert disjoint.json()["draft_version"] == 3
    assert disjoint.json()["fields"]["name"] == "Dal"
    assert disjoint.json()["fields"]["category"] == "meal"

    same_field = contribution_clients.owner.patch(
        f"{route}/{draft_id}",
        headers=headers,
        json={
            "expected_draft_version": 1,
            "operation_id": str(uuid4()),
            "patches": [{
                "field": "name",
                "value": "Lentil dal",
                "base_value": None,
                "base_version": 1,
            }],
        },
    )
    assert same_field.status_code == 409
    current = contribution_clients.owner.get(f"{route}/{draft_id}").json()
    assert current["draft_version"] == 3
    assert current["fields"]["name"] == "Dal"

    partial_match = contribution_clients.owner.patch(
        f"{route}/{draft_id}",
        headers=headers,
        json={
            "expected_draft_version": 1,
            "operation_id": str(uuid4()),
            "patches": [
                {
                    "field": "attribution",
                    "value": "Must not apply",
                    "base_value": None,
                    "base_version": 1,
                },
                {
                    "field": "name",
                    "value": "Must not apply either",
                    "base_value": None,
                    "base_version": 1,
                },
            ],
        },
    )
    assert partial_match.status_code == 409

    future_base = contribution_clients.owner.patch(
        f"{route}/{draft_id}",
        headers=headers,
        json={
            "expected_draft_version": 1,
            "operation_id": str(uuid4()),
            "patches": [{
                "field": "category",
                "value": "prepared meal",
                "base_value": "meal",
                "base_version": 2,
            }],
        },
    )
    assert future_base.status_code == 409
    unchanged = contribution_clients.owner.get(f"{route}/{draft_id}").json()
    assert unchanged["draft_version"] == 3
    assert unchanged["fields"]["attribution"] is None
    assert unchanged["fields"]["name"] == "Dal"


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_contribution_operation_replay_is_bounded_by_retention(
    contribution_clients: ContributionClients,
) -> None:
    route = "/api/v1/contribution-drafts"
    headers = {"X-CSRF-Token": contribution_clients.owner_csrf}
    created = contribution_clients.owner.post(
        route, headers=headers, json={"client_draft_id": "retention-draft"}
    )
    draft_id = created.json()["draft_id"]
    operation_id = str(uuid4())
    payload = {
        "expected_draft_version": 1,
        "operation_id": operation_id,
        "patches": [{
            "field": "name", "value": "Dal", "base_value": None, "base_version": 1,
        }],
    }

    first = contribution_clients.owner.patch(f"{route}/{draft_id}", headers=headers, json=payload)
    replay = contribution_clients.owner.patch(f"{route}/{draft_id}", headers=headers, json=payload)
    assert first.status_code == replay.status_code == 200
    assert replay.json()["draft_version"] == 2

    asyncio.run(_backdate_operation(INTEGRATION_DATABASE_URL, operation_id))
    outside_retention = contribution_clients.owner.patch(
        f"{route}/{draft_id}", headers=headers, json=payload
    )
    assert outside_retention.status_code == 409


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_contribution_patch_is_rate_limited_per_user_and_draft() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_reset_database(INTEGRATION_DATABASE_URL))
    settings = Settings(
        database_url=INTEGRATION_DATABASE_URL,
        app_environment="test",
        auth_rate_limit_attempts=50,
        contribution_patch_rate_limit_attempts=2,
        contribution_patch_account_rate_limit_attempts=3,
        _env_file=None,
    )
    with TestClient(create_app(settings)) as client:
        registered = client.post(
            "/api/v1/auth/register",
            json={"email": "limited@example.test", "password": "owner password 123"},
        )
        headers = {"X-CSRF-Token": registered.json()["csrf_token"]}
        created = client.post("/api/v1/contribution-drafts", headers=headers, json={})
        route = f"/api/v1/contribution-drafts/{created.json()['draft_id']}"
        responses = []
        base_value = None
        for version, value in enumerate(("one", "two", "three"), start=1):
            responses.append(client.patch(route, headers=headers, json={
                "expected_draft_version": version,
                "operation_id": str(uuid4()),
                "patches": [{
                    "field": "category",
                    "value": value,
                    "base_value": base_value,
                    "base_version": version,
                }],
            }))
            base_value = value
        assert [response.status_code for response in responses] == [200, 200, 429]
        assert responses[-1].headers["retry-after"]
        second = client.post("/api/v1/contribution-drafts", headers=headers, json={})
        account_limited = client.patch(
            f"/api/v1/contribution-drafts/{second.json()['draft_id']}",
            headers=headers,
            json={
                "expected_draft_version": 1,
                "operation_id": str(uuid4()),
                "patches": [{
                    "field": "category",
                    "value": "other draft",
                    "base_value": None,
                    "base_version": 1,
                }],
            },
        )
        assert account_limited.status_code == 429


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_contribution_autosave_acknowledgement_p95_is_under_500_ms(
    contribution_clients: ContributionClients,
) -> None:
    route = "/api/v1/contribution-drafts"
    headers = {"X-CSRF-Token": contribution_clients.owner_csrf}
    created = contribution_clients.owner.post(
        route, headers=headers, json={"client_draft_id": "autosave-latency-gate"}
    )
    draft_id = created.json()["draft_id"]
    version = 1
    base_value = None
    latencies: list[float] = []
    for index in range(30):
        value = f"category-{index}"
        started = perf_counter()
        response = contribution_clients.owner.patch(
            f"{route}/{draft_id}",
            headers=headers,
            json={
                "expected_draft_version": version,
                "operation_id": str(uuid4()),
                "patches": [{
                    "field": "category",
                    "value": value,
                    "base_value": base_value,
                    "base_version": version,
                }],
            },
        )
        latencies.append(perf_counter() - started)
        assert response.status_code == 200
        version = response.json()["draft_version"]
        base_value = value
    p95_seconds = sorted(latencies)[ceil(len(latencies) * 0.95) - 1]
    assert p95_seconds < 0.5, f"autosave acknowledgement p95 was {p95_seconds * 1000:.1f} ms"


def test_openapi_registers_typed_contribution_operations() -> None:
    paths = create_app(Settings(app_environment="test", _env_file=None)).openapi()["paths"]
    assert set(paths["/api/v1/contribution-drafts"]) == {"post"}
    assert set(paths["/api/v1/contribution-drafts/{draft_id}"]) == {"get", "patch"}
    assert set(paths["/api/v1/contribution-drafts/{draft_id}/submit"]) == {"post"}
