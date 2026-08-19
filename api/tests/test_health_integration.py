import os

import pytest
from fastapi.testclient import TestClient
from opennosh_api.main import create_app
from opennosh_api.settings import Settings

INTEGRATION_DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL")


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_healthcheck_reaches_postgresql() -> None:
    app = create_app(Settings(database_url=INTEGRATION_DATABASE_URL))  # type: ignore[arg-type]

    with TestClient(app) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["database"] == "connected"


def test_healthcheck_handles_an_unreachable_database() -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://opennosh:opennosh@127.0.0.1:1/opennosh",
        database_healthcheck_timeout_seconds=0.1,
    )
    app = create_app(settings)

    with TestClient(app) as client:
        response = client.get("/healthz")

    assert response.status_code == 503
    assert response.json()["database"] == "unavailable"
