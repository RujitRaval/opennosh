from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from opennosh_api.auth.dependencies import get_current_session, require_csrf
from opennosh_api.database import get_database_session
from opennosh_api.main import create_app
from opennosh_api.reuse import router as reuse_router_module
from opennosh_api.reuse.contracts import ReuseEventType
from opennosh_api.reuse.models import ReuseDeclaration, ReuseDeclarationEvent, ReuseDependency
from opennosh_api.reuse.router import get_reuse_artifact_service, require_reuse_review_csrf
from opennosh_api.reuse.service import ReuseRegistryError
from opennosh_api.settings import Settings

OWNER = UUID("10000000-0000-4000-8000-000000000001")
STEWARD = UUID("10000000-0000-4000-8000-000000000002")
DECLARATION_ID = UUID("20000000-0000-4000-8000-000000000001")
IDEMPOTENCY_KEY = "30000000-0000-4000-8000-000000000001"
NOW = datetime(2026, 9, 4, 2, tzinfo=UTC)


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


def _settings(**overrides: object) -> Settings:
    return Settings.model_validate({"app_environment": "test", **overrides})


def _declaration(*, state: str = "verification_pending", revision: int = 2) -> ReuseDeclaration:
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
        state=state,
        revision=revision,
        created_at=NOW - timedelta(days=2),
        updated_at=NOW,
        withdrawn_at=None,
    )


def _event(declaration: ReuseDeclaration) -> ReuseDeclarationEvent:
    return ReuseDeclarationEvent(
        id=UUID("40000000-0000-4000-8000-000000000001"),
        declaration_id=declaration.id,
        actor_id=STEWARD,
        event_type="verified",
        declaration_revision=declaration.revision,
        idempotency_key_hash="a" * 64,
        request_hash="b" * 64,
        evidence_json={
            "source_url": "https://evidence.example.test/adoption",
            "observed_at": "2026-09-03T22:00:00Z",
            "content_sha256": "c" * 64,
            "status": "accessible",
        },
        reason="Evidence reviewed.",
        occurred_at=NOW,
        created_at=NOW,
    )


def test_review_and_public_routes_fail_closed_before_validation_or_database_io() -> None:
    app = create_app(_settings(), app_version="test")
    app.dependency_overrides[get_current_session] = lambda: SimpleNamespace(user_id=STEWARD)
    app.dependency_overrides[require_csrf] = lambda: SimpleNamespace(user_id=STEWARD)
    app.dependency_overrides[get_database_session] = lambda: ForbiddenDatabase()
    client = TestClient(app)
    responses = [
        client.get("/api/v1/governance/reuse/reviews"),
        client.post("/api/v1/governance/reuse/reviews/not-a-uuid/verify", json={}),
        client.post("/api/v1/governance/reuse/reviews/not-a-uuid/request-changes", json={}),
        client.post("/api/v1/governance/reuse/reviews/not-a-uuid/reject", json={}),
        client.get("/api/v1/public/reuse"),
        client.get("/api/v1/public/reuse/dependencies"),
        client.get("/api/v1/public/reuse/not-a-uuid"),
    ]
    assert [response.status_code for response in responses] == [404] * len(responses)


def test_review_and_public_openapi_contracts_are_bounded_and_identity_safe() -> None:
    schema = create_app(app_version="test").openapi()
    paths = schema["paths"]
    for path in (
        "/api/v1/governance/reuse/reviews",
        "/api/v1/governance/reuse/reviews/{declaration_id}/verify",
        "/api/v1/governance/reuse/reviews/{declaration_id}/request-changes",
        "/api/v1/governance/reuse/reviews/{declaration_id}/reject",
        "/api/v1/public/reuse",
        "/api/v1/public/reuse/dependencies",
        "/api/v1/public/reuse/{declaration_id}",
    ):
        assert path in paths
    public_list = paths["/api/v1/public/reuse"]["get"]
    assert public_list.get("parameters", []) == []
    serialized = json.dumps(public_list).lower()
    for forbidden in ("owner_actor_id", "actor_id", "organization_key", "request_hash"):
        assert forbidden not in serialized
    dependencies = paths["/api/v1/public/reuse/dependencies"]["get"]
    assert dependencies.get("parameters", []) == []
    dependency_contract = json.dumps(dependencies).lower()
    for forbidden in ("owner_actor_id", "actor_id", "organization_key", "source_url"):
        assert forbidden not in dependency_contract


def test_review_mutations_require_fresh_auth() -> None:
    app = create_app(
        _settings(
            reuse_registry_mutations_enabled=True,
            reuse_verification_enabled=True,
            governance_fresh_auth_seconds=60,
        ),
        app_version="test",
    )
    stale = SimpleNamespace(
        user_id=STEWARD,
        session=SimpleNamespace(created_at=datetime.now(UTC) - timedelta(minutes=2)),
    )
    app.dependency_overrides[require_csrf] = lambda: stale
    app.dependency_overrides[get_database_session] = lambda: ForbiddenDatabase()
    response = TestClient(app).post(
        f"/api/v1/governance/reuse/reviews/{DECLARATION_ID}/verify",
        headers={"Idempotency-Key": IDEMPOTENCY_KEY, "If-Match": "2"},
        json={
            "reason": "Evidence reviewed.",
            "evidence": {
                "source_url": "https://evidence.example.test/adoption",
                "observed_at": "2026-09-03T22:00:00Z",
                "content_sha256": "a" * 64,
                "status": "accessible",
            },
        },
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "fresh_auth_required"


def test_enabled_review_queue_and_transitions_commit_exact_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    declaration = _declaration()
    database = RecordingDatabase()
    actions: list[ReuseEventType] = []

    async def fake_list(*_args: object, **_kwargs: object) -> tuple[ReuseDeclaration, ...]:
        return (declaration,)

    async def fake_review(*_args: object, **kwargs: object) -> ReuseDeclaration:
        action = kwargs["action"]
        assert isinstance(action, ReuseEventType)
        actions.append(action)
        declaration.revision += 1
        declaration.state = (
            "verified" if action is ReuseEventType.VERIFIED else "community_declared"
        )
        return declaration

    monkeypatch.setattr(reuse_router_module, "list_reviewable_declarations", fake_list)
    monkeypatch.setattr(reuse_router_module, "review_declaration", fake_review)
    app = create_app(
        _settings(reuse_registry_mutations_enabled=True, reuse_verification_enabled=True),
        app_version="test",
    )
    current = SimpleNamespace(user_id=STEWARD)
    app.dependency_overrides[get_current_session] = lambda: current
    app.dependency_overrides[require_reuse_review_csrf] = lambda: current
    app.dependency_overrides[get_database_session] = lambda: database
    client = TestClient(app)

    queue = client.get("/api/v1/governance/reuse/reviews")
    assert queue.status_code == 200
    assert queue.headers["cache-control"] == "no-store"
    assert queue.json()["declarations"][0]["id"] == str(DECLARATION_ID)

    headers = {"Idempotency-Key": IDEMPOTENCY_KEY, "If-Match": "2"}
    verify = client.post(
        f"/api/v1/governance/reuse/reviews/{DECLARATION_ID}/verify",
        headers=headers,
        json={
            "reason": "Evidence reviewed.",
            "evidence": {
                "source_url": "https://evidence.example.test/adoption",
                "observed_at": "2026-09-03T22:00:00Z",
                "content_sha256": "a" * 64,
                "status": "accessible",
            },
        },
    )
    assert verify.status_code == 200
    assert verify.headers["etag"] == "3"

    for suffix, expected in (
        ("request-changes", ReuseEventType.CHANGES_REQUESTED),
        ("reject", ReuseEventType.REJECTED),
    ):
        response = client.post(
            f"/api/v1/governance/reuse/reviews/{DECLARATION_ID}/{suffix}",
            headers=headers,
            json={"reason": "Declaration needs correction."},
        )
        assert response.status_code == 200
        assert actions[-1] is expected
    assert actions == [
        ReuseEventType.VERIFIED,
        ReuseEventType.CHANGES_REQUESTED,
        ReuseEventType.REJECTED,
    ]
    assert database.commits == 3


def test_public_registry_maps_exact_labels_evidence_and_cache_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    community = _declaration(state="community_declared", revision=1)
    pending = _declaration(state="verification_pending", revision=2)
    verified = _declaration(state="verified", revision=3)
    verified.id = UUID("20000000-0000-4000-8000-000000000003")
    public_rows = ((community, None), (pending, None), (verified, _event(verified)))

    async def fake_list(*_args: object, **_kwargs: object) -> tuple[object, ...]:
        return public_rows

    async def fake_read(*_args: object, **_kwargs: object) -> tuple[object, object]:
        return verified, _event(verified)

    monkeypatch.setattr(reuse_router_module, "list_public_declarations", fake_list)
    monkeypatch.setattr(reuse_router_module, "read_public_declaration", fake_read)
    app = create_app(
        _settings(
            reuse_registry_mutations_enabled=True,
            reuse_verification_enabled=True,
            reuse_public_enabled=True,
        ),
        app_version="test",
    )
    app.dependency_overrides[get_database_session] = lambda: RecordingDatabase()
    client = TestClient(app)
    response = client.get("/api/v1/public/reuse")
    assert response.status_code == 200
    assert response.headers["cache-control"] == ("public, max-age=60, stale-while-revalidate=300")
    declarations = response.json()["declarations"]
    assert [item["verification_label"] for item in declarations] == [
        "community_declared",
        "unverified",
        "verified",
    ]
    assert declarations[0]["evidence"] is None
    assert declarations[2]["evidence"]["content_sha256"] == "c" * 64

    detail = client.get(f"/api/v1/public/reuse/{verified.id}")
    assert detail.status_code == 200
    assert detail.headers["etag"] == "3"


def test_public_dependencies_are_verified_revision_bound_and_stably_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    declaration = _declaration(state="verified", revision=3)
    event = _event(declaration)
    dependency = ReuseDependency(
        id=UUID("60000000-0000-4000-8000-000000000001"),
        declaration_id=declaration.id,
        source_pack_id="global-core",
        source_release_id="0.91.0.0",
        source_artifact_digest="d" * 64,
        dependency_kind="data",
        evidence_event_id=event.id,
        created_at=NOW,
    )

    async def fake_dependencies(*_args: object, **_kwargs: object) -> tuple[object, ...]:
        return ((dependency, declaration, event),)

    class ArtifactService:
        async def resolve_release(self, *, release_version: str) -> object:
            assert release_version == "0.91.0.0"
            return SimpleNamespace(
                manifest=SimpleNamespace(
                    packs=(
                        SimpleNamespace(
                            pack_id="global-core",
                            download=SimpleNamespace(digest="d" * 64),
                        ),
                    )
                )
            )

    monkeypatch.setattr(reuse_router_module, "list_public_dependencies", fake_dependencies)
    app = create_app(
        _settings(
            reuse_registry_mutations_enabled=True,
            reuse_verification_enabled=True,
            reuse_public_enabled=True,
        ),
        app_version="test",
    )
    app.dependency_overrides[get_database_session] = lambda: RecordingDatabase()
    app.dependency_overrides[get_reuse_artifact_service] = lambda: ArtifactService()
    client = TestClient(app)
    first = client.get("/api/v1/public/reuse/dependencies")
    second = client.get("/api/v1/public/reuse/dependencies")
    assert first.status_code == 200
    assert first.json() == {
        "schema_version": "1.0",
        "dependencies": [
            {
                "declaration_id": str(DECLARATION_ID),
                "project_label": "Meal Commons",
                "source_pack_id": "global-core",
                "source_release_id": "0.91.0.0",
                "source_artifact_digest": "d" * 64,
                "dependency_kind": "data",
                "verification_label": "verified",
                "evidence_observed_on": "2026-09-03",
            }
        ],
    }
    assert first.headers["cache-control"] == "public, max-age=60, stale-while-revalidate=300"
    assert first.headers["etag"] == second.headers["etag"]
    assert first.headers["etag"].startswith('"sha256-')


def test_public_dependencies_fail_closed_when_signed_artifact_no_longer_resolves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    declaration = _declaration(state="verified", revision=3)
    event = _event(declaration)
    dependency = ReuseDependency(
        id=UUID("60000000-0000-4000-8000-000000000002"),
        declaration_id=declaration.id,
        source_pack_id="global-core",
        source_release_id="0.91.0.0",
        source_artifact_digest="d" * 64,
        dependency_kind="data",
        evidence_event_id=event.id,
        created_at=NOW,
    )

    async def fake_dependencies(*_args: object, **_kwargs: object) -> tuple[object, ...]:
        return ((dependency, declaration, event),)

    class ArtifactService:
        async def resolve_release(self, *, release_version: str) -> object:
            del release_version
            return SimpleNamespace(manifest=SimpleNamespace(packs=()))

    monkeypatch.setattr(reuse_router_module, "list_public_dependencies", fake_dependencies)
    app = create_app(
        _settings(
            reuse_registry_mutations_enabled=True,
            reuse_verification_enabled=True,
            reuse_public_enabled=True,
        ),
        app_version="test",
    )
    app.dependency_overrides[get_database_session] = lambda: RecordingDatabase()
    app.dependency_overrides[get_reuse_artifact_service] = lambda: ArtifactService()
    response = TestClient(app).get("/api/v1/public/reuse/dependencies")
    assert response.status_code == 503
    assert response.headers["retry-after"] == "60"
    assert response.json()["detail"] == "reuse_dependency_proof_unavailable"


def test_review_authorization_errors_and_public_missing_records_are_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = RecordingDatabase()

    async def forbidden(*_args: object, **_kwargs: object) -> ReuseDeclaration:
        raise ReuseRegistryError("reuse_steward_role_not_active")

    async def missing(*_args: object, **_kwargs: object) -> tuple[object, object]:
        raise ReuseRegistryError("reuse_declaration_not_found")

    monkeypatch.setattr(reuse_router_module, "review_declaration", forbidden)
    monkeypatch.setattr(reuse_router_module, "read_public_declaration", missing)
    app = create_app(
        _settings(
            reuse_registry_mutations_enabled=True,
            reuse_verification_enabled=True,
            reuse_public_enabled=True,
        ),
        app_version="test",
    )
    current = SimpleNamespace(user_id=STEWARD)
    app.dependency_overrides[require_reuse_review_csrf] = lambda: current
    app.dependency_overrides[get_database_session] = lambda: database
    client = TestClient(app)
    denied = client.post(
        f"/api/v1/governance/reuse/reviews/{DECLARATION_ID}/reject",
        headers={"Idempotency-Key": IDEMPOTENCY_KEY, "If-Match": "2"},
        json={"reason": "Cannot review."},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"] == "reuse_steward_role_not_active"
    assert database.rollbacks == 1

    hidden = client.get(f"/api/v1/public/reuse/{DECLARATION_ID}")
    assert hidden.status_code == 404
    assert hidden.json()["detail"] == "The requested resource was not found."
