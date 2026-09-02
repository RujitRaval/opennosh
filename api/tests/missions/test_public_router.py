from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from opennosh_api.auth.dependencies import get_app_settings
from opennosh_api.database import get_database_session
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


class _BrokenDatabase:
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
