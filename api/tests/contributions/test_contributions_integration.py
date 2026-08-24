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
    assert created.json()["draft_id"] == retried_create.json()["draft_id"]
    draft_id = created.json()["draft_id"]

    hidden = contribution_clients.attacker.get(f"{route}/{draft_id}")
    absent = contribution_clients.attacker.get(f"{route}/{uuid4()}")
    assert hidden.status_code == absent.status_code == 404

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


def test_openapi_registers_typed_contribution_operations() -> None:
    paths = create_app(Settings(app_environment="test", _env_file=None)).openapi()["paths"]
    assert set(paths["/api/v1/contribution-drafts"]) == {"post"}
    assert set(paths["/api/v1/contribution-drafts/{draft_id}"]) == {"get", "patch"}
    assert set(paths["/api/v1/contribution-drafts/{draft_id}/submit"]) == {"post"}
