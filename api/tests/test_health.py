from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient
from opennosh_api.database import DatabaseHealthProbe, get_database_probe
from opennosh_api.main import create_app
from opennosh_api.settings import Settings


class HealthyProbe:
    async def check(self) -> None:
        return None


class UnhealthyProbe:
    async def check(self) -> None:
        raise RuntimeError("database credentials must never reach the response")


@contextmanager
def client_for(probe: DatabaseHealthProbe) -> Iterator[TestClient]:
    app = create_app(Settings(database_url="postgresql+asyncpg://unused:unused@localhost/unused"))

    async def override_probe() -> AsyncIterator[DatabaseHealthProbe]:
        yield probe

    app.dependency_overrides[get_database_probe] = override_probe
    with TestClient(app) as client:
        yield client


def test_healthcheck_reports_a_healthy_database() -> None:
    with client_for(HealthyProbe()) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "database": "connected",
        "seed": "not_started",
    }


def test_healthcheck_degrades_without_leaking_database_details() -> None:
    with client_for(UnhealthyProbe()) as client:
        response = client.get("/healthz")

    assert response.status_code == 503
    assert response.json() == {
        "status": "degraded",
        "database": "unavailable",
        "seed": "not_started",
    }
    assert "credentials" not in response.text


def test_openapi_documents_the_degraded_response() -> None:
    app = create_app(Settings(database_url="postgresql+asyncpg://unused:unused@localhost/unused"))

    responses = app.openapi()["paths"]["/healthz"]["get"]["responses"]

    assert responses["503"]["description"] == (
        "The API is running but its database is unavailable."
    )


def test_app_version_comes_from_the_repository_version() -> None:
    app = create_app(Settings(database_url="postgresql+asyncpg://unused:unused@localhost/unused"))

    assert app.version == (Path(__file__).resolve().parents[2] / "VERSION").read_text().strip()
