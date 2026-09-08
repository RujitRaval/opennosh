from fastapi.testclient import TestClient
from opennosh_api.main import create_app
from opennosh_api.settings import Settings


def test_public_build_version_reports_release_and_render_commit_without_cache() -> None:
    commit = "a" * 40
    app = create_app(
        Settings(render_git_commit=commit, _env_file=None),
        app_version="1.2.3.4",
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/public/build-version")

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": "1",
        "version": "1.2.3.4",
        "commit": commit,
    }
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-opennosh-build-commit"] == commit


def test_public_build_version_allows_local_build_without_commit() -> None:
    app = create_app(Settings(_env_file=None), app_version="1.2.3.4")

    with TestClient(app) as client:
        response = client.get("/api/v1/public/build-version")

    assert response.status_code == 200
    assert response.json()["commit"] is None
    assert "x-opennosh-build-commit" not in response.headers
