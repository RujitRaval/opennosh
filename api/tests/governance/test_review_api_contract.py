from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient
from opennosh_api.auth.dependencies import get_current_session, require_csrf
from opennosh_api.database import get_database_session
from opennosh_api.main import create_app
from opennosh_api.settings import Settings

USER_ID = UUID("00000000-0000-4000-8000-000000000001")
CASE_ID = "00000000-0000-4000-8000-000000000002"


class ForbiddenDatabase:
    def __getattribute__(self, name: str) -> object:
        if name.startswith("__"):
            return super().__getattribute__(name)
        raise AssertionError("disabled governance route touched the database")


def _disabled_client() -> TestClient:
    app = create_app(Settings(app_environment="test", _env_file=None), app_version="test")
    current = SimpleNamespace(user_id=USER_ID)
    app.dependency_overrides[require_csrf] = lambda: current
    app.dependency_overrides[get_current_session] = lambda: current
    app.dependency_overrides[get_database_session] = lambda: ForbiddenDatabase()
    return TestClient(app)


def test_governance_surfaces_are_disabled_before_validation_or_database_io() -> None:
    client = _disabled_client()
    responses = [
        client.get("/api/v1/governance/review-cases?pack_id=bad pack"),
        client.get("/api/v1/governance/review-cases/not-a-uuid"),
        client.post(
            f"/api/v1/governance/review-cases/{CASE_ID}/claim",
            headers={"Idempotency-Key": "not-a-uuid"},
            json={},
        ),
        client.post(
            f"/api/v1/governance/review-cases/{CASE_ID}/release",
            json={"expected_revision": 0, "reason": ""},
        ),
        client.post(
            f"/api/v1/governance/review-cases/{CASE_ID}/decision",
            json={"outcome": "approved", "reason": ""},
        ),
        client.post(
            f"/api/v1/governance/review-cases/{CASE_ID}/approve",
            json={},
        ),
        client.post(
            f"/api/v1/governance/review-cases/{CASE_ID}/disputes",
            json={},
        ),
        client.post("/api/v1/governance/disputes/not-a-uuid/resolve", json={}),
        client.post("/api/v1/governance/disputes/not-a-uuid/appeal", json={}),
        client.post("/api/v1/governance/appeals/not-a-uuid/resolve", json={}),
    ]
    assert [response.status_code for response in responses] == [404] * 10
    assert {response.json()["detail"] for response in responses} == {
        "The requested resource was not found."
    }


def test_governance_openapi_contract_omits_private_notes_and_provider_material() -> None:
    schema = create_app(app_version="test").openapi()
    paths = schema["paths"]
    assert "/api/v1/governance/review-cases" in paths
    assert "/api/v1/governance/review-cases/{review_case_id}" in paths
    assert "/api/v1/governance/review-cases/{review_case_id}/claim" in paths
    assert "/api/v1/governance/review-cases/{review_case_id}/release" in paths
    assert "/api/v1/governance/review-cases/{review_case_id}/decision" in paths
    assert "/api/v1/governance/review-cases/{review_case_id}/approve" in paths
    assert "/api/v1/governance/review-cases/{review_case_id}/disputes" in paths
    assert "/api/v1/governance/disputes/{dispute_id}/resolve" in paths
    assert "/api/v1/governance/disputes/{dispute_id}/appeal" in paths
    assert "/api/v1/governance/appeals/{appeal_id}/resolve" in paths
    serialized = json.dumps(schema).lower()
    for forbidden in (
        "private_note",
        "object_key",
        "presigned",
        "provider_revision",
        "access_key",
        "secret_access",
    ):
        assert forbidden not in serialized


def test_governance_flags_default_off_independently() -> None:
    settings = Settings(app_environment="test", _env_file=None)
    assert settings.governance_steward_ui_enabled is False
    assert settings.governance_mutations_enabled is False
    assert settings.governance_public_decisions_enabled is False
