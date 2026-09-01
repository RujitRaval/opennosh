from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from opennosh_api.auth.dependencies import get_current_session, require_csrf
from opennosh_api.database import get_database_session
from opennosh_api.governance import router as governance_router_module
from opennosh_api.governance.contracts import GovernanceDecisionOutcome
from opennosh_api.governance.models import GovernanceDecision
from opennosh_api.governance.router import require_governance_csrf
from opennosh_api.main import create_app
from opennosh_api.settings import Settings

USER_ID = UUID("00000000-0000-4000-8000-000000000001")
CASE_ID = "00000000-0000-4000-8000-000000000002"
DRAFT_ID = UUID("00000000-0000-4000-8000-000000000003")
DECISION_ID = UUID("00000000-0000-4000-8000-000000000004")
DISPUTE_ID = UUID("00000000-0000-4000-8000-000000000005")
APPEAL_ID = UUID("00000000-0000-4000-8000-000000000006")


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
        client.get("/api/v1/governance/contributor/review-case?draft_id=not-a-uuid"),
        client.get("/api/v1/governance/public-decisions/not-a-uuid"),
        client.post(
            f"/api/v1/governance/review-cases/{CASE_ID}/claim",
            headers={"Idempotency-Key": "not-a-uuid"},
            json={},
        ),
        client.post(
            f"/api/v1/governance/review-cases/{CASE_ID}/release",
            json={"expected_revision": 0, "reason": ""},
        ),
        client.post(f"/api/v1/governance/review-cases/{CASE_ID}/pause", json={}),
        client.post(f"/api/v1/governance/review-cases/{CASE_ID}/resume", json={}),
        client.post(f"/api/v1/governance/review-cases/{CASE_ID}/recuse", json={}),
        client.post(
            f"/api/v1/governance/review-cases/{CASE_ID}/decision",
            json={"outcome": "approved", "reason": ""},
        ),
        client.post(
            f"/api/v1/governance/review-cases/{CASE_ID}/approve",
            json={},
        ),
        client.post(
            f"/api/v1/governance/review-cases/{CASE_ID}/response",
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
    assert [response.status_code for response in responses] == [404] * 16
    assert {response.json()["detail"] for response in responses} == {
        "The requested resource was not found."
    }


def test_governance_openapi_contract_omits_private_notes_and_provider_material() -> None:
    schema = create_app(app_version="test").openapi()
    paths = schema["paths"]
    assert "/api/v1/governance/review-cases" in paths
    assert "/api/v1/governance/review-cases/{review_case_id}" in paths
    assert "/api/v1/governance/contributor/review-case" in paths
    assert "/api/v1/governance/public-decisions/{decision_id}" in paths
    assert "/api/v1/governance/review-cases/{review_case_id}/claim" in paths
    assert "/api/v1/governance/review-cases/{review_case_id}/release" in paths
    assert "/api/v1/governance/review-cases/{review_case_id}/pause" in paths
    assert "/api/v1/governance/review-cases/{review_case_id}/resume" in paths
    assert "/api/v1/governance/review-cases/{review_case_id}/recuse" in paths
    assert "/api/v1/governance/review-cases/{review_case_id}/decision" in paths
    assert "/api/v1/governance/review-cases/{review_case_id}/approve" in paths
    assert "/api/v1/governance/review-cases/{review_case_id}/response" in paths
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
    assert settings.governance_fresh_auth_seconds == 900


def test_governance_mutations_require_a_fresh_human_session() -> None:
    settings = Settings(app_environment="test", governance_fresh_auth_seconds=900, _env_file=None)
    now = datetime.now(UTC)
    fresh = SimpleNamespace(session=SimpleNamespace(created_at=now - timedelta(seconds=899)))
    stale = SimpleNamespace(session=SimpleNamespace(created_at=now - timedelta(seconds=901)))

    assert require_governance_csrf(fresh, settings) is fresh  # type: ignore[arg-type]
    with pytest.raises(HTTPException) as caught:
        require_governance_csrf(stale, settings)  # type: ignore[arg-type]
    assert caught.value.status_code == 401
    assert caught.value.detail == "fresh_auth_required"


def test_governance_mutations_cannot_enable_without_the_read_surface() -> None:
    with pytest.raises(ValueError, match="mutations require the steward UI"):
        Settings(
            app_environment="test",
            governance_mutations_enabled=True,
            governance_steward_ui_enabled=False,
            _env_file=None,
        )


class RecordingDatabase:
    def __init__(self, decision: object | None = None) -> None:
        self.decision = decision
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def get(self, model: object, object_id: UUID) -> object | None:
        if model is GovernanceDecision and object_id == DECISION_ID:
            return self.decision
        return None

    async def scalar(self, _statement: object) -> str:
        return "planned"


def _review_case(*, contributor_actor_id: UUID = USER_ID) -> SimpleNamespace:
    now = datetime(2026, 9, 1, 20, tzinfo=UTC)
    return SimpleNamespace(
        id=UUID(CASE_ID),
        source_draft_id=DRAFT_ID,
        source_draft_version=1,
        pack_id="starter-us",
        contributor_actor_id=contributor_actor_id,
        submitted_fields_json={"name": "Soup"},
        state="in_review",
        revision=2,
        assigned_steward_actor_id=USER_ID,
        acknowledged_at=now,
        pause_reason=None,
        next_review_at=None,
        opened_at=now,
        updated_at=now,
        closed_at=None,
    )


def _enabled_client(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[TestClient, RecordingDatabase]:
    now = datetime(2026, 9, 1, 20, tzinfo=UTC)
    review_case = _review_case()
    event = SimpleNamespace(
        sequence=1,
        event_type="opened",
        actor_id=USER_ID,
        public_reason="Submitted for review.",
        occurred_at=now,
    )
    dispute = SimpleNamespace(
        id=DISPUTE_ID,
        decision_id=DECISION_ID,
        category="accuracy",
        public_reason="The value needs another review.",
        requested_remedy="Check the cited source.",
        state="resolved",
        revision=2,
        opened_at=now,
        resolution="Returned for review.",
        resolved_at=now,
    )
    appeal = SimpleNamespace(
        id=APPEAL_ID,
        dispute_id=DISPUTE_ID,
        public_reason="The resolution missed the source.",
        requested_remedy="Independent review.",
        state="resolved",
        revision=2,
        opened_at=now,
        resolution="Appeal upheld.",
        resolved_at=now,
    )
    decision = SimpleNamespace(
        id=DECISION_ID,
        pack_id="starter-us",
        source_draft_version=1,
        outcome="approved",
        reason="Verified against the submitted evidence.",
        decided_at=now,
    )
    database = RecordingDatabase(decision)
    current = SimpleNamespace(
        user_id=USER_ID,
        session=SimpleNamespace(created_at=datetime.now(UTC)),
    )

    async def one_case(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return review_case

    async def case_and_record(*_args: object, **_kwargs: object) -> tuple[object, object]:
        return review_case, decision

    async def case_and_intent(
        *_args: object, **_kwargs: object
    ) -> tuple[object, object, object]:
        return review_case, decision, SimpleNamespace(id=uuid4())

    async def response_result(
        *_args: object, **_kwargs: object
    ) -> tuple[object, object, object]:
        return review_case, review_case, SimpleNamespace(draft_version=2)

    async def records(*_args: object, **_kwargs: object) -> tuple[object, ...]:
        return (event,)

    async def disputes_and_appeals(
        *_args: object, **_kwargs: object
    ) -> tuple[tuple[object, ...], tuple[object, ...]]:
        return (dispute,), (appeal,)

    async def queue(*_args: object, **_kwargs: object) -> tuple[object, ...]:
        return (review_case,)

    for name in (
        "claim_review_case",
        "release_review_case",
        "pause_review_case",
        "resume_review_case",
        "recuse_review_case",
        "get_review_case_for_actor",
        "get_latest_review_case_for_contributor",
    ):
        monkeypatch.setattr(governance_router_module, name, one_case)
    monkeypatch.setattr(governance_router_module, "record_nonapproval_decision", case_and_record)
    monkeypatch.setattr(governance_router_module, "approve_review_case", case_and_intent)
    monkeypatch.setattr(governance_router_module, "respond_to_changes_request", response_result)
    for name in ("open_dispute", "resolve_dispute", "open_appeal", "resolve_appeal"):
        monkeypatch.setattr(governance_router_module, name, case_and_record)
    monkeypatch.setattr(governance_router_module, "list_review_events", records)
    monkeypatch.setattr(
        governance_router_module,
        "list_disputes_and_appeals",
        disputes_and_appeals,
    )
    monkeypatch.setattr(governance_router_module, "list_review_cases_for_steward", queue)

    app = create_app(
        Settings(
            app_environment="test",
            governance_steward_ui_enabled=True,
            governance_mutations_enabled=True,
            governance_public_decisions_enabled=True,
            _env_file=None,
        ),
        app_version="test",
    )
    app.dependency_overrides[require_csrf] = lambda: current
    app.dependency_overrides[get_current_session] = lambda: current
    app.dependency_overrides[get_database_session] = lambda: database
    return TestClient(app), database


def test_enabled_governance_routes_expose_the_complete_action_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, database = _enabled_client(monkeypatch)
    idempotency = {"Idempotency-Key": str(uuid4())}
    review_case = f"/api/v1/governance/review-cases/{CASE_ID}"
    responses = [
        client.get("/api/v1/governance/review-cases", params={"pack_id": "starter-us"}),
        client.get(review_case),
        client.get(
            "/api/v1/governance/contributor/review-case",
            params={"draft_id": str(DRAFT_ID)},
        ),
        client.get(f"/api/v1/governance/public-decisions/{DECISION_ID}"),
        client.post(f"{review_case}/claim", headers=idempotency, json={"expected_revision": 2}),
        client.post(
            f"{review_case}/release",
            headers=idempotency,
            json={"expected_revision": 2, "reason": "Return to queue."},
        ),
        client.post(
            f"{review_case}/pause",
            headers=idempotency,
            json={
                "expected_revision": 2,
                "reason": "Awaiting source confirmation.",
                "next_review_at": "2026-09-02T20:00:00Z",
            },
        ),
        client.post(
            f"{review_case}/resume",
            headers=idempotency,
            json={"expected_revision": 2, "reason": "Source confirmed."},
        ),
        client.post(
            f"{review_case}/recuse",
            headers=idempotency,
            json={"expected_revision": 2, "reason": "Prior involvement."},
        ),
        client.post(
            f"{review_case}/decision",
            headers=idempotency,
            json={
                "expected_revision": 2,
                "outcome": GovernanceDecisionOutcome.CHANGES_REQUESTED.value,
                "reason": "Clarify the source date.",
            },
        ),
        client.post(
            f"{review_case}/approve",
            headers=idempotency,
            json={
                "expected_revision": 2,
                "pack_id": "starter-us",
                "record_id": "soup",
                "expected_base_commit": "a" * 40,
                "files": [{"path": "packs/starter-us/soup.json", "content": "{}"}],
                "reason": "Evidence verified.",
            },
        ),
        client.post(
            f"{review_case}/response",
            headers=idempotency,
            json={
                "expected_revision": 2,
                "expected_draft_version": 1,
                "patches": [{"field": "name", "value": "Tomato soup"}],
                "public_reason": "Updated the requested field.",
            },
        ),
        client.post(
            f"{review_case}/disputes",
            headers=idempotency,
            json={
                "expected_revision": 2,
                "category": "accuracy",
                "public_reason": "The source supports the original value.",
                "requested_remedy": "Review the cited page.",
            },
        ),
        client.post(
            f"/api/v1/governance/disputes/{DISPUTE_ID}/resolve",
            headers=idempotency,
            json={
                "expected_case_revision": 2,
                "expected_dispute_revision": 1,
                "resolution": "The case returns for review.",
            },
        ),
        client.post(
            f"/api/v1/governance/disputes/{DISPUTE_ID}/appeal",
            headers=idempotency,
            json={
                "expected_case_revision": 2,
                "expected_dispute_revision": 1,
                "public_reason": "The resolution missed the cited page.",
                "requested_remedy": "Independent review.",
            },
        ),
        client.post(
            f"/api/v1/governance/appeals/{APPEAL_ID}/resolve",
            headers=idempotency,
            json={
                "expected_case_revision": 2,
                "expected_appeal_revision": 1,
                "resolution": "The appeal is upheld.",
            },
        ),
    ]

    assert [response.status_code for response in responses] == [200] * len(responses)
    assert responses[0].json()["cases"][0]["viewer_role"] == "steward"
    assert responses[1].json()["viewer_role"] == "contributor"
    assert responses[3].json()["publication_state"] == "planned"
    assert responses[10].json()["status"] == "publication_pending"
    assert responses[11].json()["next_draft_version"] == 2
    assert responses[1].json()["events"][0]["event_type"] == "opened"
    assert responses[1].json()["disputes"][0]["decision_id"] == str(DECISION_ID)
    assert responses[1].json()["appeals"][0]["appeal_id"] == str(APPEAL_ID)
    assert database.commits == 12
    assert database.rollbacks == 0


def test_enabled_governance_routes_map_service_failures_and_roll_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, database = _enabled_client(monkeypatch)
    idempotency = {"Idempotency-Key": str(uuid4())}
    review_case = f"/api/v1/governance/review-cases/{CASE_ID}"

    async def conflict(*_args: object, **_kwargs: object) -> object:
        raise governance_router_module.ReviewCaseError("review_case_revision_conflict")

    requests = [
        (
            "list_review_cases_for_steward",
            "get",
            "/api/v1/governance/review-cases?pack_id=starter-us",
            None,
        ),
        ("get_review_case_for_actor", "get", review_case, None),
        (
            "get_latest_review_case_for_contributor",
            "get",
            f"/api/v1/governance/contributor/review-case?draft_id={DRAFT_ID}",
            None,
        ),
        ("claim_review_case", "post", f"{review_case}/claim", {"expected_revision": 2}),
        (
            "release_review_case",
            "post",
            f"{review_case}/release",
            {"expected_revision": 2, "reason": "Return to queue."},
        ),
        (
            "pause_review_case",
            "post",
            f"{review_case}/pause",
            {
                "expected_revision": 2,
                "reason": "Awaiting source.",
                "next_review_at": "2026-09-02T20:00:00Z",
            },
        ),
        (
            "resume_review_case",
            "post",
            f"{review_case}/resume",
            {"expected_revision": 2, "reason": "Source arrived."},
        ),
        (
            "recuse_review_case",
            "post",
            f"{review_case}/recuse",
            {"expected_revision": 2, "reason": "Prior involvement."},
        ),
        (
            "record_nonapproval_decision",
            "post",
            f"{review_case}/decision",
            {"expected_revision": 2, "outcome": "rejected", "reason": "Unsupported."},
        ),
        (
            "approve_review_case",
            "post",
            f"{review_case}/approve",
            {
                "expected_revision": 2,
                "pack_id": "starter-us",
                "record_id": "soup",
                "expected_base_commit": "a" * 40,
                "files": [{"path": "packs/starter-us/soup.json", "content": "{}"}],
                "reason": "Verified.",
            },
        ),
        (
            "respond_to_changes_request",
            "post",
            f"{review_case}/response",
            {
                "expected_revision": 2,
                "expected_draft_version": 1,
                "patches": [{"field": "name", "value": "Tomato soup"}],
                "public_reason": "Updated.",
            },
        ),
        (
            "open_dispute",
            "post",
            f"{review_case}/disputes",
            {
                "expected_revision": 2,
                "category": "accuracy",
                "public_reason": "Incorrect decision.",
                "requested_remedy": "Review it.",
            },
        ),
        (
            "resolve_dispute",
            "post",
            f"/api/v1/governance/disputes/{DISPUTE_ID}/resolve",
            {
                "expected_case_revision": 2,
                "expected_dispute_revision": 1,
                "resolution": "Return for review.",
            },
        ),
        (
            "open_appeal",
            "post",
            f"/api/v1/governance/disputes/{DISPUTE_ID}/appeal",
            {
                "expected_case_revision": 2,
                "expected_dispute_revision": 1,
                "public_reason": "Missed evidence.",
                "requested_remedy": "Independent review.",
            },
        ),
        (
            "resolve_appeal",
            "post",
            f"/api/v1/governance/appeals/{APPEAL_ID}/resolve",
            {
                "expected_case_revision": 2,
                "expected_appeal_revision": 1,
                "resolution": "Appeal upheld.",
            },
        ),
    ]

    for service_name, method, path, payload in requests:
        monkeypatch.setattr(governance_router_module, service_name, conflict)
        response = client.request(method, path, headers=idempotency, json=payload)
        assert response.status_code == 409
        assert response.json()["detail"] == "review_case_revision_conflict"

    assert database.rollbacks == 12


@pytest.mark.parametrize(
    ("service_name", "path", "payload"),
    [
        (
            "pause_review_case",
            f"/api/v1/governance/review-cases/{CASE_ID}/pause",
            {
                "expected_revision": 2,
                "reason": "Awaiting source.",
                "next_review_at": "2026-09-02T20:00:00Z",
            },
        ),
        (
            "approve_review_case",
            f"/api/v1/governance/review-cases/{CASE_ID}/approve",
            {
                "expected_revision": 2,
                "pack_id": "starter-us",
                "record_id": "soup",
                "expected_base_commit": "a" * 40,
                "files": [{"path": "packs/starter-us/soup.json", "content": "{}"}],
                "reason": "Verified.",
            },
        ),
        (
            "respond_to_changes_request",
            f"/api/v1/governance/review-cases/{CASE_ID}/response",
            {
                "expected_revision": 2,
                "expected_draft_version": 1,
                "patches": [{"field": "name", "value": "Tomato soup"}],
                "public_reason": "Updated.",
            },
        ),
    ],
)
def test_governance_route_value_errors_are_unprocessable(
    monkeypatch: pytest.MonkeyPatch,
    service_name: str,
    path: str,
    payload: dict[str, object],
) -> None:
    client, database = _enabled_client(monkeypatch)

    async def invalid(*_args: object, **_kwargs: object) -> object:
        raise ValueError("invalid governance value")

    monkeypatch.setattr(governance_router_module, service_name, invalid)
    response = client.post(path, headers={"Idempotency-Key": str(uuid4())}, json=payload)
    assert response.status_code == 422
    assert response.json()["detail"] == "invalid governance value"
    assert database.rollbacks == 1


@pytest.mark.parametrize(
    ("code", "status_code"),
    [
        ("review_case_not_found", 404),
        ("evidence_manifest_not_ready", 409),
        ("steward_role_not_active", 403),
    ],
)
def test_governance_error_mapping_is_fail_closed(code: str, status_code: int) -> None:
    with pytest.raises(HTTPException) as caught:
        governance_router_module._raise_review_error(
            governance_router_module.ReviewCaseError(code)
        )
    assert caught.value.status_code == status_code


def test_missing_public_decision_is_not_disclosed(monkeypatch: pytest.MonkeyPatch) -> None:
    client, database = _enabled_client(monkeypatch)
    database.decision = None
    response = client.get(f"/api/v1/governance/public-decisions/{DECISION_ID}")
    assert response.status_code == 404
