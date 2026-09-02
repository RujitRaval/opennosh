from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from opennosh_api.auth.dependencies import (
    CurrentSession,
    get_app_settings,
    get_current_session,
    require_csrf,
)
from opennosh_api.database import get_database_session
from opennosh_api.main import create_app
from opennosh_api.missions import router as mission_router_module
from opennosh_api.missions.policy import MissionLifecycleError
from opennosh_api.missions.router import (
    organizer_router,
    require_mission_steward_csrf,
)
from opennosh_api.settings import Settings

MISSION_ID = UUID("10000000-0000-4000-8000-000000000001")
DEFINITION_ID = UUID("20000000-0000-4000-8000-000000000001")
EVENT_ID = UUID("30000000-0000-4000-8000-000000000001")
PRIOR_EVENT_ID = UUID("40000000-0000-4000-8000-000000000001")
ACTOR_ID = UUID("50000000-0000-4000-8000-000000000001")
STEWARD_ID = UUID("60000000-0000-4000-8000-000000000001")
NOW = datetime(2026, 9, 2, 20, tzinfo=UTC)


class ForbiddenDatabase:
    def __getattribute__(self, name: str) -> object:
        if name.startswith("__"):
            return super().__getattribute__(name)
        raise AssertionError("disabled mission route touched the database")


class RecordingDatabase:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "mission_public_enabled": True,
        "mission_mutations_enabled": True,
        "mission_projection_enabled": False,
        "mission_pack_release_enabled": False,
        "governance_fresh_auth_seconds": 900,
        "session_cookie_name": "opennosh_session",
        "csrf_cookie_name": "opennosh_csrf",
    }
    values.update(overrides)
    return cast(Settings, SimpleNamespace(**values))


def _current(*, age_seconds: int = 0) -> CurrentSession:
    return cast(
        CurrentSession,
        SimpleNamespace(
            user_id=ACTOR_ID,
            session=SimpleNamespace(
                created_at=datetime.now(UTC) - timedelta(seconds=age_seconds),
                csrf_token_hash="unused",
            ),
        ),
    )


def _proposal_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "mission_id": str(MISSION_ID),
        "definition_id": str(DEFINITION_ID),
        "event_id": str(EVENT_ID),
        "responsible_steward_actor_id": str(STEWARD_ID),
        "definition": {
            "gap_kind": "missing_field",
            "title": "Complete sodium values",
            "summary": "Fill verified sodium gaps without ranking contributors.",
            "target_pack_id": "opennosh-starter",
            "target_dataset": "foods",
            "acceptance_target": 10,
            "acceptance_criteria": "Count verified records with a sodium value.",
        },
        "public_reason": "Open a measurable public gap.",
    }
    payload.update(overrides)
    return payload


def _transition_payload(
    action: str = "approve", **overrides: Any
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "definition_id": str(DEFINITION_ID),
        "event_id": str(EVENT_ID),
        "expected_prior_event_id": str(PRIOR_EVENT_ID),
        "action": action,
        "public_reason": f"Apply {action} with a public explanation.",
    }
    payload.update(overrides)
    return payload


def _event(action: str, *, sequence: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=EVENT_ID,
        mission_id=MISSION_ID,
        definition_id=DEFINITION_ID,
        sequence=sequence,
        action=action,
        public_reason=f"Apply {action} with a public explanation.",
        next_review_at=None,
        release_receipt_digest=None,
        occurred_at=NOW,
    )


def _client(
    *,
    settings: Settings | None = None,
    database: object | None = None,
) -> TestClient:
    app = FastAPI()
    app.include_router(organizer_router)
    app.dependency_overrides[get_app_settings] = lambda: settings or _settings()
    app.dependency_overrides[require_csrf] = lambda: _current()
    app.dependency_overrides[get_database_session] = lambda: database or RecordingDatabase()
    return TestClient(app)


def test_mission_mutations_are_disabled_before_validation_or_database_io() -> None:
    app = FastAPI()
    app.include_router(organizer_router)
    app.dependency_overrides[get_app_settings] = lambda: _settings(
        mission_public_enabled=False,
        mission_mutations_enabled=False,
    )
    app.dependency_overrides[get_database_session] = lambda: ForbiddenDatabase()
    client = TestClient(app)

    proposal = client.post("/api/v1/missions", json={})
    transition = client.post("/api/v1/missions/not-a-uuid/transitions", json={})

    assert [proposal.status_code, transition.status_code] == [404, 404]
    assert proposal.json() == transition.json() == {
        "detail": "The requested resource was not found."
    }


def test_enabled_mission_mutations_require_authentication_and_matching_csrf() -> None:
    app = FastAPI()
    app.include_router(organizer_router)
    app.dependency_overrides[get_app_settings] = lambda: _settings()
    app.dependency_overrides[get_database_session] = lambda: RecordingDatabase()
    client = TestClient(app)

    unauthenticated = client.post("/api/v1/missions", json=_proposal_payload())
    assert unauthenticated.status_code == 401

    app.dependency_overrides[get_current_session] = _current
    missing_csrf = client.post("/api/v1/missions", json=_proposal_payload())
    assert missing_csrf.status_code == 403

    client.cookies.set("opennosh_csrf", "cookie-token")
    mismatched_csrf = client.post(
        "/api/v1/missions",
        headers={"X-CSRF-Token": "different-token"},
        json=_proposal_payload(),
    )
    assert mismatched_csrf.status_code == 403


def test_organizer_lifecycle_contract_is_registered_without_actor_disclosure() -> None:
    schema = create_app(app_version="test").openapi()

    assert "/api/v1/missions" in schema["paths"]
    assert "/api/v1/missions/{mission_id}/transitions" in schema["paths"]
    response_schema = schema["components"]["schemas"]["MissionLifecycleResponse"]
    assert "actor_id" not in response_schema["properties"]

    transition_schema = schema["paths"][
        "/api/v1/missions/{mission_id}/transitions"
    ]["post"]["requestBody"]["content"]["application/json"]["schema"]
    discriminator = transition_schema["discriminator"]
    assert discriminator["propertyName"] == "action"
    assert set(discriminator["mapping"]) == {
        "approve",
        "pause",
        "resume",
        "complete",
        "release",
        "close",
    }
    assert "propose" not in discriminator["mapping"]

    component_schemas = schema["components"]["schemas"]
    assert "next_review_at" in component_schemas["MissionPauseTransitionRequest"][
        "required"
    ]
    assert "release_receipt_digest" in component_schemas[
        "MissionReleaseTransitionRequest"
    ]["required"]


def test_mission_mutations_require_a_fresh_human_session() -> None:
    settings = _settings()
    fresh = _current(age_seconds=899)
    stale = _current(age_seconds=901)

    assert require_mission_steward_csrf(fresh, settings) is fresh
    with pytest.raises(HTTPException) as caught:
        require_mission_steward_csrf(stale, settings)
    assert caught.value.status_code == 401
    assert caught.value.detail == "fresh_auth_required"


def test_proposal_commits_actor_bound_event_and_returns_no_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = RecordingDatabase()
    captured: dict[str, object] = {}

    async def fake_propose(_store: object, command: object, *, now: datetime) -> Any:
        captured["command"] = command
        captured["now"] = now
        return SimpleNamespace(), _event("propose", sequence=1)

    monkeypatch.setattr(mission_router_module, "propose_mission", fake_propose)
    response = _client(database=database).post(
        "/api/v1/missions",
        json=_proposal_payload(public_reason="  Open a measurable public gap.  "),
    )

    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["state"] == "proposed"
    command = captured["command"]
    assert command.actor_id == ACTOR_ID  # type: ignore[attr-defined]
    assert command.public_reason == "Open a measurable public gap."  # type: ignore[attr-defined]
    assert database.commits == 1
    assert database.rollbacks == 0


def test_transition_commits_optimistic_event_and_maps_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = RecordingDatabase()
    calls = 0

    async def fake_transition(_store: object, command: object, *, now: datetime) -> Any:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise MissionLifecycleError("mission_revision_conflict")
        assert command.actor_id == ACTOR_ID  # type: ignore[attr-defined]
        assert command.expected_prior_event_id == PRIOR_EVENT_ID  # type: ignore[attr-defined]
        return _event("approve", sequence=2)

    monkeypatch.setattr(mission_router_module, "transition_mission", fake_transition)
    client = _client(database=database)

    accepted = client.post(
        f"/api/v1/missions/{MISSION_ID}/transitions",
        json=_transition_payload(),
    )
    conflict = client.post(
        f"/api/v1/missions/{MISSION_ID}/transitions",
        json=_transition_payload(event_id="30000000-0000-4000-8000-000000000002"),
    )

    assert accepted.status_code == 200
    assert accepted.headers["cache-control"] == "no-store"
    assert accepted.json()["state"] == "active"
    assert conflict.status_code == 409
    assert conflict.json() == {"detail": "mission_revision_conflict"}
    assert database.commits == 1
    assert database.rollbacks == 1


@pytest.mark.parametrize(
    ("action", "extra"),
    [
        ("complete", {}),
        ("release", {"release_receipt_digest": "a" * 64}),
    ],
)
def test_proof_dependent_transitions_require_their_specific_flags(
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    extra: dict[str, object],
) -> None:
    async def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("disabled transition reached the mission service")

    monkeypatch.setattr(mission_router_module, "transition_mission", forbidden)
    response = _client().post(
        f"/api/v1/missions/{MISSION_ID}/transitions",
        json=_transition_payload(action, **extra),
    )

    assert response.status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        _proposal_payload(public_reason="   "),
        _transition_payload("propose"),
        _transition_payload("pause"),
        _transition_payload("approve", next_review_at="2026-09-03T10:00:00Z"),
        _transition_payload("pause", next_review_at="2026-09-03T10:00:00"),
    ],
)
def test_invalid_lifecycle_request_shapes_are_rejected(payload: dict[str, Any]) -> None:
    client = _client()
    path = (
        "/api/v1/missions"
        if "mission_id" in payload
        else f"/api/v1/missions/{MISSION_ID}/transitions"
    )

    assert client.post(path, json=payload).status_code == 422


def test_proposal_rejects_whitespace_only_public_definition_text() -> None:
    payload = _proposal_payload()
    payload["definition"] = {
        **payload["definition"],
        "title": "   ",
    }

    assert _client().post("/api/v1/missions", json=payload).status_code == 422


def test_steward_authorization_failures_roll_back_without_leaking_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = RecordingDatabase()

    async def forbidden_actor(*_args: object, **_kwargs: object) -> object:
        raise MissionLifecycleError("mission_actor_not_active_steward")

    monkeypatch.setattr(mission_router_module, "propose_mission", forbidden_actor)
    response = _client(database=database).post(
        "/api/v1/missions", json=_proposal_payload()
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "The requested resource was not found."}
    assert database.commits == 0
    assert database.rollbacks == 1


def test_release_action_can_run_only_when_all_release_flags_are_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = RecordingDatabase()

    async def fake_transition(_store: object, command: object, *, now: datetime) -> Any:
        event = _event("release", sequence=6)
        event.release_receipt_digest = command.release_receipt_digest  # type: ignore[attr-defined]
        return event

    monkeypatch.setattr(mission_router_module, "transition_mission", fake_transition)
    settings = _settings(
        mission_projection_enabled=True,
        mission_pack_release_enabled=True,
    )
    response = _client(settings=settings, database=database).post(
        f"/api/v1/missions/{MISSION_ID}/transitions",
        json=_transition_payload("release", release_receipt_digest="a" * 64),
    )

    assert response.status_code == 200
    assert response.json()["state"] == "released"
    assert response.json()["release_receipt_digest"] == "a" * 64
    assert database.commits == 1
