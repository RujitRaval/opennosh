from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import UUID

from fastapi import HTTPException
from fastapi.testclient import TestClient
from opennosh_api.auth.dependencies import get_current_session, require_csrf
from opennosh_api.contributions.router import get_evidence_upload_broker
from opennosh_api.database import get_database_session
from opennosh_api.main import create_app
from opennosh_api.problems.schemas import ProblemDetails
from opennosh_api.settings import Settings

USER_ID = UUID("00000000-0000-4000-8000-000000000001")
DRAFT_ID = "00000000-0000-4000-8000-000000000002"
UPLOAD_ID = "00000000-0000-4000-8000-000000000003"


class ForbiddenBroker:
    def __getattribute__(self, name: str) -> object:
        if name.startswith("__"):
            return super().__getattribute__(name)
        raise AssertionError("disabled upload route touched object storage")


def _disabled_client() -> TestClient:
    app = create_app(Settings(app_environment="test", _env_file=None), app_version="test")
    current = SimpleNamespace(user_id=USER_ID)
    app.dependency_overrides[require_csrf] = lambda: current
    app.dependency_overrides[get_current_session] = lambda: current
    app.dependency_overrides[get_database_session] = lambda: object()
    app.dependency_overrides[get_evidence_upload_broker] = lambda: ForbiddenBroker()
    return TestClient(app)


def test_disabled_upload_routes_are_indistinguishable_and_perform_zero_storage_io() -> None:
    client = _disabled_client()
    responses = [
        client.post(
            f"/api/v1/contribution-drafts/{DRAFT_ID}/evidence-uploads",
            headers={"Idempotency-Key": "attempt-1"},
            json={
                "source_draft_version": 1,
                "media_type": "image/png",
                "byte_length": 10,
            },
        ),
        client.post(
            f"/api/v1/contribution-drafts/{DRAFT_ID}/evidence-uploads/{UPLOAD_ID}/complete",
            json={"completion_capability": "a" * 43},
        ),
        client.get(f"/api/v1/contribution-drafts/{DRAFT_ID}/evidence-uploads/{UPLOAD_ID}"),
    ]

    assert [response.status_code for response in responses] == [404, 404, 404]
    assert {response.json()["detail"] for response in responses} == {
        "The requested resource was not found."
    }
    assert all(response.headers["cache-control"] == "no-store" for response in responses)


def test_disabled_upload_routes_hide_before_path_header_and_body_validation() -> None:
    client = _disabled_client()
    responses = [
        client.post(
            "/api/v1/contribution-drafts/not-a-uuid/evidence-uploads",
            json={"media_type": "application/pdf", "byte_length": 0},
        ),
        client.post(
            f"/api/v1/contribution-drafts/{DRAFT_ID}/evidence-uploads/not-a-uuid/complete",
            json={"completion_capability": "short"},
        ),
        client.get(f"/api/v1/contribution-drafts/{DRAFT_ID}/evidence-uploads/not-a-uuid"),
    ]

    assert [response.status_code for response in responses] == [404, 404, 404]
    assert {response.json()["code"] for response in responses} == {"resource_not_found"}
    assert all(response.headers["cache-control"] == "no-store" for response in responses)
    for response in responses:
        ProblemDetails.model_validate(response.json())


def test_disabled_upload_routes_preserve_authentication_and_csrf_precedence() -> None:
    for denied_status in (401, 403):
        app = create_app(Settings(app_environment="test", _env_file=None), app_version="test")

        def deny(status_code: int = denied_status) -> None:
            raise HTTPException(status_code=status_code)

        app.dependency_overrides[require_csrf] = deny
        app.dependency_overrides[get_current_session] = deny
        app.dependency_overrides[get_database_session] = lambda: object()
        with TestClient(app) as client:
            create_response = client.post(
                "/api/v1/contribution-drafts/not-a-uuid/evidence-uploads",
                json={},
            )
            read_response = client.get(
                f"/api/v1/contribution-drafts/{DRAFT_ID}/evidence-uploads/not-a-uuid"
            )
        assert create_response.status_code == denied_status
        assert read_response.status_code == denied_status


def test_configured_upload_bound_rejects_before_database_or_broker_io() -> None:
    settings = Settings(
        app_environment="test",
        evidence_uploads_enabled=True,
        evidence_upload_max_bytes=4,
        evidence_quarantine_endpoint="https://account.r2.cloudflarestorage.com",
        evidence_quarantine_region="auto",
        evidence_quarantine_bucket="opennosh-evidence-quarantine",
        evidence_quarantine_access_key_id="test-access",
        evidence_quarantine_secret_access_key="test-secret",
        _env_file=None,
    )
    app = create_app(settings, app_version="test")
    current = SimpleNamespace(user_id=USER_ID)
    app.dependency_overrides[require_csrf] = lambda: current
    app.dependency_overrides[get_database_session] = lambda: object()
    app.dependency_overrides[get_evidence_upload_broker] = lambda: ForbiddenBroker()
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/contribution-drafts/{DRAFT_ID}/evidence-uploads",
            headers={"Idempotency-Key": "attempt-over-limit"},
            json={
                "source_draft_version": 1,
                "media_type": "image/png",
                "byte_length": 5,
            },
        )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_failed"


def test_openapi_upload_contract_is_safe_and_contains_no_provider_credentials() -> None:
    schema = create_app(app_version="test").openapi()
    paths = schema["paths"]
    assert "/api/v1/contribution-drafts/{draft_id}/evidence-uploads" in paths
    assert "/api/v1/contribution-drafts/{draft_id}/evidence-uploads/{upload_id}/complete" in paths
    assert "/api/v1/contribution-drafts/{draft_id}/evidence-uploads/{upload_id}" in paths
    serialized = json.dumps(schema).lower()
    for secret_name in (
        "evidence_quarantine_access_key_id",
        "evidence_quarantine_secret_access_key",
        "evidence_sanitized_secret_access_key",
        "evidence_immutable_secret_access_key",
    ):
        assert secret_name not in serialized


def test_safe_session_schema_never_contains_upload_url_or_completion_capability() -> None:
    schema = create_app(app_version="test").openapi()
    properties = schema["components"]["schemas"]["EvidenceUploadSessionResponse"]["properties"]
    assert "upload" not in properties
    assert "url" not in properties
    assert "completion_capability" not in properties
