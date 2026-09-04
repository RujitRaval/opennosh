from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from opennosh_api.database import get_database_session
from opennosh_api.main import create_app
from opennosh_api.public_operations import router as router_module
from opennosh_api.public_operations.contracts import (
    PublicIncident,
    PublicIncidentListResponse,
)
from opennosh_api.public_operations.manifest import load_public_status_manifest
from opennosh_api.public_operations.router import get_public_status_manifest
from opennosh_api.public_operations.service import project_public_status
from opennosh_api.settings import Settings
from sqlalchemy.exc import SQLAlchemyError

NOW = datetime(2026, 9, 4, 6, tzinfo=UTC)
MANIFEST = load_public_status_manifest()


class RecordingDatabase:
    used = False

    async def execute(self, *_args: object, **_kwargs: object) -> None:
        self.used = True
        raise AssertionError("disabled public operations touched the database")


def _settings(**changes: object) -> Settings:
    return Settings(app_environment="test", **changes)


def test_public_operations_fail_closed_before_database_io_when_disabled() -> None:
    database = RecordingDatabase()
    app = create_app(_settings(), app_version="test")
    app.dependency_overrides[get_database_session] = lambda: database
    client = TestClient(app)
    assert client.get("/api/v1/public/status").status_code == 404
    assert client.get("/api/v1/public/incidents").status_code == 404
    assert not database.used


def test_public_operations_openapi_has_no_filters_or_private_infrastructure_fields() -> None:
    schema = create_app(_settings(), app_version="test").openapi()
    paths = schema["paths"]
    for path in ("/api/v1/public/status", "/api/v1/public/incidents"):
        operation = paths[path]["get"]
        assert operation.get("parameters", []) == []
        serialized = json.dumps(operation).lower()
        for forbidden in (
            "credential",
            "provider_resource_id",
            "hostname",
            "ip_address",
            "log_excerpt",
            "private_topology",
        ):
            assert forbidden not in serialized


def test_public_status_and_incidents_are_bounded_and_stably_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status_result = project_public_status(manifest=MANIFEST, observations=(), now=NOW)
    incident_result = PublicIncidentListResponse(
        incidents=(
            PublicIncident(
                incident_id=UUID("80000000-0000-4000-8000-000000000001"),
                title="Food search interruption",
                public_summary="Some search requests are unavailable.",
                affected_component_ids=("api", "search"),
                affected_versions=("0.92.0.0",),
                guidance="Use existing signed downloads while recovery is verified.",
                state="investigating",
                opened_at=NOW,
                updated_at=NOW,
            ),
        )
    )

    async def fake_status(*_args: object, **_kwargs: object) -> object:
        return status_result

    async def fake_incidents(*_args: object, **_kwargs: object) -> object:
        return incident_result

    monkeypatch.setattr(router_module, "current_public_status", fake_status)
    monkeypatch.setattr(router_module, "list_public_incidents", fake_incidents)
    app = create_app(_settings(public_status_enabled=True), app_version="test")
    app.dependency_overrides[get_database_session] = lambda: RecordingDatabase()
    app.dependency_overrides[get_public_status_manifest] = lambda: MANIFEST
    client = TestClient(app)

    first_status = client.get("/api/v1/public/status")
    second_status = client.get("/api/v1/public/status")
    incidents = client.get("/api/v1/public/incidents")
    assert first_status.status_code == 200
    assert len(first_status.json()["components"]) == 8
    assert first_status.headers["etag"] == second_status.headers["etag"]
    assert first_status.headers["cache-control"] == (
        "public, max-age=30, stale-while-revalidate=120"
    )
    assert incidents.status_code == 200
    assert incidents.json()["incidents"][0]["state"] == "investigating"
    assert incidents.headers["etag"].startswith('"sha256-')


def test_public_operations_database_failure_is_retryable_and_never_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unavailable(*_args: object, **_kwargs: object) -> object:
        raise SQLAlchemyError("unavailable")

    monkeypatch.setattr(router_module, "current_public_status", unavailable)
    app = create_app(_settings(public_status_enabled=True), app_version="test")
    app.dependency_overrides[get_database_session] = lambda: RecordingDatabase()
    response = TestClient(app).get("/api/v1/public/status")
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["retry-after"] == "60"
    assert response.json()["detail"] == "public_operations_evidence_unavailable"
