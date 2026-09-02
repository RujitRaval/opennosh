from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from opennosh_api.auth.dependencies import get_app_settings
from opennosh_api.database import get_database_session
from opennosh_api.missions.activity_service import (
    MissionActivityRegionLevel,
    MissionActivityState,
    PublicMissionActivityCountry,
    PublicMissionActivityMap,
)
from opennosh_api.missions.router import router
from sqlalchemy.exc import SQLAlchemyError


async def _database() -> AsyncIterator[Any]:
    yield SimpleNamespace()


def test_disabled_public_missions_are_safe_and_not_cached() -> None:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_app_settings] = lambda: SimpleNamespace(
        mission_public_enabled=False
    )
    app.dependency_overrides[get_database_session] = _database

    response = TestClient(app).get("/api/v1/public/missions")

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": "1.0",
        "state": "unavailable",
        "reason": "disabled",
        "missions": [],
    }
    assert response.headers["cache-control"] == "no-store"


def test_public_mission_limit_is_bounded() -> None:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_app_settings] = lambda: SimpleNamespace(
        mission_public_enabled=False
    )
    app.dependency_overrides[get_database_session] = _database

    assert TestClient(app).get("/api/v1/public/missions?limit=101").status_code == 422


def test_disabled_public_mission_activity_is_safe_and_not_cached() -> None:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_app_settings] = lambda: SimpleNamespace(
        mission_activity_map_enabled=False
    )
    app.dependency_overrides[get_database_session] = _database

    response = TestClient(app).get("/api/v1/public/missions/activity")

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": "1.0",
        "state": "unavailable",
        "reason": "disabled",
        "minimum_cohort": 10,
        "regions": [],
    }
    assert response.headers["cache-control"] == "no-store"


def test_public_mission_activity_openapi_has_no_differencing_or_identity_inputs() -> None:
    app = FastAPI()
    app.include_router(router)

    operation = app.openapi()["paths"]["/api/v1/public/missions/activity"]["get"]
    assert operation.get("parameters", []) == []
    schema_text = str(operation)
    for forbidden in (
        "contributor",
        "actor",
        "user_id",
        "mission_id",
        "pack_id",
        "from",
        "until",
    ):
        assert forbidden not in schema_text


class _BrokenDatabase:
    async def execute(self, _statement: object) -> object:
        raise SQLAlchemyError("mission storage unavailable")

    async def scalar(self, _statement: object) -> object:
        raise SQLAlchemyError("mission storage unavailable")

    async def scalars(self, _statement: object) -> object:
        raise SQLAlchemyError("mission storage unavailable")


async def _broken_database() -> AsyncIterator[Any]:
    yield _BrokenDatabase()


def test_enabled_public_missions_fail_closed_on_storage_error() -> None:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_app_settings] = lambda: SimpleNamespace(
        mission_public_enabled=True
    )
    app.dependency_overrides[get_database_session] = _broken_database

    response = TestClient(app).get("/api/v1/public/missions")

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": "1.0",
        "state": "unavailable",
        "reason": "proof_unavailable",
        "missions": [],
    }
    assert response.headers["cache-control"] == "no-store"


def test_enabled_public_mission_activity_fails_closed_on_storage_error() -> None:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_app_settings] = lambda: SimpleNamespace(
        mission_activity_map_enabled=True
    )
    app.dependency_overrides[get_database_session] = _broken_database

    response = TestClient(app).get("/api/v1/public/missions/activity")

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": "1.0",
        "state": "unavailable",
        "reason": "proof_unavailable",
        "minimum_cohort": 10,
        "regions": [],
    }
    assert response.headers["cache-control"] == "no-store"


def test_enabled_public_mission_activity_caches_live_and_honest_zero(
    monkeypatch: Any,
) -> None:
    async def live(*_args: object, **_kwargs: object) -> PublicMissionActivityMap:
        return PublicMissionActivityMap(
            state=MissionActivityState.LIVE,
            regions=(
                PublicMissionActivityCountry(
                    region_code="US",
                    level=MissionActivityRegionLevel.COUNTRY,
                    accepted_count=10,
                ),
            ),
        )

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_app_settings] = lambda: SimpleNamespace(
        mission_activity_map_enabled=True
    )
    app.dependency_overrides[get_database_session] = _database
    monkeypatch.setattr("opennosh_api.missions.router.public_mission_activity_map", live)

    response = TestClient(app).get("/api/v1/public/missions/activity")

    assert response.status_code == 200
    assert response.json()["state"] == "live"
    assert response.json()["regions"] == [
        {"region_code": "US", "level": "country", "accepted_count": 10}
    ]
    assert response.headers["cache-control"] == (
        "public, max-age=0, s-maxage=60, stale-if-error=300"
    )

    async def zero(*_args: object, **_kwargs: object) -> PublicMissionActivityMap:
        return PublicMissionActivityMap(state=MissionActivityState.ZERO)

    monkeypatch.setattr("opennosh_api.missions.router.public_mission_activity_map", zero)
    response = TestClient(app).get("/api/v1/public/missions/activity")
    assert response.status_code == 200
    assert response.json()["state"] == "zero"
    assert response.json()["regions"] == []
    assert response.headers["cache-control"] == (
        "public, max-age=0, s-maxage=60, stale-if-error=300"
    )
