from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from opennosh_api.auth.dependencies import get_app_settings
from opennosh_api.database import get_database_session
from opennosh_api.impact.contracts import signed_impact_snapshot
from opennosh_api.impact.router import router
from sqlalchemy.exc import SQLAlchemyError


class ForbiddenDatabase:
    def __getattribute__(self, name: str) -> object:
        if name.startswith("__"):
            return super().__getattribute__(name)
        raise AssertionError("disabled impact route touched storage")


async def _database() -> AsyncIterator[Any]:
    yield ForbiddenDatabase()


def _client(*, enabled: bool) -> tuple[FastAPI, TestClient]:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_app_settings] = lambda: SimpleNamespace(
        impact_public_enabled=enabled
    )
    app.dependency_overrides[get_database_session] = _database
    return app, TestClient(app)


def test_disabled_public_impact_is_stable_empty_and_never_reads_storage() -> None:
    _app, client = _client(enabled=False)
    response = client.get("/api/v1/public/impact")

    assert response.status_code == 200
    assert response.json()["state"] == "unavailable"
    assert response.json()["reason"] == "disabled"
    assert response.json()["source_checkpoint_id"] is None
    assert response.json()["global"] == {
        "verified_adopters": 0,
        "community_declarations": 0,
        "accepted_contributions": 0,
        "pack_installs": 0,
        "api_reads": 0,
        "artifact_downloads": 0,
    }
    assert response.json()["regions"] == []
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["etag"] == f'"{response.json()["digest"]}"'


def test_public_impact_contract_has_no_query_or_differencing_surface() -> None:
    app, client = _client(enabled=False)
    operation = app.openapi()["paths"]["/api/v1/public/impact"]["get"]
    assert operation.get("parameters", []) == []
    assert client.get("/api/v1/public/impact?from=2020&region=US").status_code == 200
    serialized = json.dumps(operation).lower()
    for forbidden in (
        "actor_id",
        "user_id",
        "organization_key",
        "contributor",
        "suppressed_count",
        "ip_address",
    ):
        assert forbidden not in serialized


def test_enabled_public_impact_fails_closed_on_storage_error(monkeypatch: Any) -> None:
    async def broken(_database: object) -> object:
        raise SQLAlchemyError("storage unavailable")

    monkeypatch.setattr("opennosh_api.impact.router.latest_impact_snapshot", broken)
    _app, client = _client(enabled=True)
    response = client.get("/api/v1/public/impact")

    assert response.status_code == 200
    assert response.json()["state"] == "unavailable"
    assert response.json()["reason"] == "proof_unavailable"
    assert response.headers["cache-control"] == "no-store"


def test_enabled_public_impact_serves_only_released_snapshot(monkeypatch: Any) -> None:
    released = signed_impact_snapshot(
        state="live",
        reason=None,
        observed_at=datetime(2026, 9, 4, 4, tzinfo=UTC),
        source_checkpoint_id="checkpoint-1",
        global_={"api_reads": 10},
        regions=(),
    )

    async def latest(_database: object) -> object:
        return released

    monkeypatch.setattr("opennosh_api.impact.router.latest_impact_snapshot", latest)
    _app, client = _client(enabled=True)
    response = client.get("/api/v1/public/impact")

    assert response.status_code == 200
    assert response.json()["state"] == "live"
    assert response.json()["source_checkpoint_id"] == "checkpoint-1"
    assert response.headers["cache-control"] == (
        "public, max-age=0, s-maxage=300, stale-if-error=900"
    )
    assert response.headers["etag"] == f'"{released.digest}"'
