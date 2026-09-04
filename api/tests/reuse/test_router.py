from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from opennosh_api.auth.dependencies import get_current_session, require_csrf
from opennosh_api.database import get_database_session
from opennosh_api.main import create_app
from opennosh_api.reuse import router as reuse_router_module
from opennosh_api.reuse.models import ReuseDeclaration
from opennosh_api.settings import Settings

OWNER = UUID("10000000-0000-4000-8000-000000000001")
DECLARATION_ID = UUID("20000000-0000-4000-8000-000000000001")
IDEMPOTENCY_KEY = "30000000-0000-4000-8000-000000000001"
NOW = datetime(2026, 9, 3, 22, tzinfo=UTC)


class ForbiddenDatabase:
    def __getattribute__(self, name: str) -> object:
        if name.startswith("__"):
            return super().__getattribute__(name)
        raise AssertionError("disabled reuse route touched the database")


class RecordingDatabase:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


def _declaration() -> ReuseDeclaration:
    return ReuseDeclaration(
        id=DECLARATION_ID,
        owner_actor_id=OWNER,
        organization_name="Community Kitchen",
        organization_key="community kitchen",
        project_name="Meal Commons",
        project_key="meal commons",
        project_url="https://example.test/reuse",
        use_case="Uses verified records in a menu.",
        region_level="country",
        region_code="US",
        state="community_declared",
        revision=1,
        created_at=NOW,
        updated_at=NOW,
        withdrawn_at=None,
    )


def _settings(**overrides: object) -> Settings:
    return Settings.model_validate({"app_environment": "test", **overrides})


def _disabled_client() -> TestClient:
    app = create_app(_settings(), app_version="test")
    current = SimpleNamespace(user_id=OWNER)
    app.dependency_overrides[require_csrf] = lambda: current
    app.dependency_overrides[get_current_session] = lambda: current
    app.dependency_overrides[get_database_session] = lambda: ForbiddenDatabase()
    return TestClient(app)


def test_all_registry_routes_fail_closed_before_validation_or_database_io() -> None:
    client = _disabled_client()
    responses = [
        client.post("/api/v1/reuse/declarations", json={}),
        client.get("/api/v1/reuse/declarations/mine?limit=1000"),
        client.get("/api/v1/reuse/declarations/not-a-uuid"),
        client.patch("/api/v1/reuse/declarations/not-a-uuid", json={}),
        client.post("/api/v1/reuse/declarations/not-a-uuid/submit", json={}),
        client.request("DELETE", "/api/v1/reuse/declarations/not-a-uuid", json={}),
        client.post("/api/v1/reuse/declarations/not-a-uuid/restore", json={}),
    ]
    assert [response.status_code for response in responses] == [404] * len(responses)
    assert {response.json()["detail"] for response in responses} == {
        "The requested resource was not found."
    }


def test_reuse_openapi_contract_exposes_only_public_request_and_response_fields() -> None:
    schema = create_app(app_version="test").openapi()
    paths = schema["paths"]
    assert "/api/v1/reuse/declarations" in paths
    assert "/api/v1/reuse/declarations/mine" in paths
    assert "/api/v1/reuse/declarations/{declaration_id}" in paths
    assert "/api/v1/reuse/declarations/{declaration_id}/submit" in paths
    assert "/api/v1/reuse/declarations/{declaration_id}/restore" in paths
    serialized = json.dumps(schema).lower()
    for forbidden in (
        "organization_key",
        "project_key",
        "idempotency_key_hash",
        "request_hash",
        "evidence_json",
        "owner_actor_id",
    ):
        assert forbidden not in serialized


def test_t34_8_flags_default_off_and_dependencies_fail_closed() -> None:
    settings = _settings()
    assert settings.reuse_registry_mutations_enabled is False
    assert settings.reuse_verification_enabled is False
    assert settings.reuse_public_enabled is False
    assert settings.impact_aggregation_enabled is False
    assert settings.impact_public_enabled is False
    assert settings.public_status_enabled is False

    with pytest.raises(ValueError, match="verification requires registry"):
        _settings(reuse_verification_enabled=True)
    with pytest.raises(ValueError, match="Public reuse requires verification"):
        _settings(reuse_public_enabled=True)
    with pytest.raises(ValueError, match="aggregation requires reuse verification"):
        _settings(impact_aggregation_enabled=True)
    with pytest.raises(ValueError, match="Public impact requires impact aggregation"):
        _settings(impact_public_enabled=True)


def test_create_route_commits_and_marks_idempotent_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    declaration = _declaration()
    database = RecordingDatabase()
    created = True

    async def fake_create(*_args: object, **_kwargs: object) -> tuple[ReuseDeclaration, bool]:
        return declaration, created

    monkeypatch.setattr(reuse_router_module, "create_declaration", fake_create)
    app = create_app(
        _settings(reuse_registry_mutations_enabled=True),
        app_version="test",
    )
    current = SimpleNamespace(user_id=OWNER)
    app.dependency_overrides[require_csrf] = lambda: current
    app.dependency_overrides[get_database_session] = lambda: database
    client = TestClient(app)
    payload = {
        "organization_name": "Community Kitchen",
        "project_name": "Meal Commons",
        "project_url": "https://example.test/reuse",
        "use_case": "Uses verified records in a menu.",
        "region_level": "country",
        "region_code": "US",
    }

    response = client.post(
        "/api/v1/reuse/declarations",
        json=payload,
        headers={"Idempotency-Key": IDEMPOTENCY_KEY},
    )
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["etag"] == "1"
    assert response.json()["id"] == str(DECLARATION_ID)
    assert database.commits == 1

    created = False
    replay = client.post(
        "/api/v1/reuse/declarations",
        json=payload,
        headers={"Idempotency-Key": IDEMPOTENCY_KEY},
    )
    assert replay.status_code == 200
    assert database.commits == 2


def test_enabled_mutations_require_csrf_idempotency_and_revision_headers() -> None:
    app = create_app(
        _settings(reuse_registry_mutations_enabled=True),
        app_version="test",
    )
    app.dependency_overrides[get_database_session] = lambda: ForbiddenDatabase()
    client = TestClient(app)
    create_response = client.post(
        "/api/v1/reuse/declarations",
        json={
            "organization_name": "Community Kitchen",
            "project_name": "Meal Commons",
            "use_case": "Public menu.",
        },
        headers={"Idempotency-Key": IDEMPOTENCY_KEY},
    )
    assert create_response.status_code == 401

    current = SimpleNamespace(user_id=OWNER)
    app.dependency_overrides[require_csrf] = lambda: current
    app.dependency_overrides[get_database_session] = lambda: ForbiddenDatabase()
    missing_headers = client.patch(
        f"/api/v1/reuse/declarations/{DECLARATION_ID}",
        json={"use_case": "Updated public menu."},
    )
    assert missing_headers.status_code == 422
