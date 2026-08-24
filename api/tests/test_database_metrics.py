from fastapi import FastAPI
from fastapi.testclient import TestClient
from opennosh_api.auth.client_address import PROXY_TOKEN_HEADER
from opennosh_api.database import DatabaseIdentity, DatabasePoolMetrics
from opennosh_api.database_metrics import router
from opennosh_api.problems import RequestIdMiddleware, install_problem_handlers
from opennosh_api.public_commons.manifests import PublicCommonsSnapshotMetrics
from opennosh_api.settings import Settings


def metrics_app() -> FastAPI:
    application = FastAPI()
    application.state.settings = Settings(
        trusted_web_proxy_token="test-proxy-token-with-at-least-32-characters",
        _env_file=None,
    )
    application.state.database_pool_metrics = DatabasePoolMetrics(
        DatabaseIdentity(deployment_id="metrics-test", role="web"), 4
    )
    application.state.public_commons_snapshot_service = type(
        "SnapshotService",
        (),
        {
            "metrics": PublicCommonsSnapshotMetrics(
                projection_reads=7,
                projection_read_bytes=8_192,
                projection_writes=2,
                projection_write_bytes=4_096,
                source_artifact_reads=4,
                rebuilds=2,
                stale_fallbacks=1,
                unavailable_responses=0,
                last_response_bytes=1_024,
            )
        },
    )()
    application.add_middleware(RequestIdMiddleware)
    install_problem_handlers(application)
    application.include_router(router)
    return application


def test_database_metrics_are_hidden_without_operations_token() -> None:
    with TestClient(metrics_app()) as client:
        response = client.get("/internal/metrics/database")

    assert response.status_code == 404


def test_database_metrics_export_role_and_deployment_attribution() -> None:
    with TestClient(metrics_app()) as client:
        response = client.get(
            "/internal/metrics/database",
            headers={
                PROXY_TOKEN_HEADER: "test-proxy-token-with-at-least-32-characters"
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "deployment_id": "metrics-test",
        "role": "web",
        "pool_size": 4,
        "active": 0,
        "idle": 0,
        "waiting": 0,
        "timed_out_total": 0,
        "acquisition_count": 0,
        "acquisition_latency_ms_average": 0.0,
        "acquisition_latency_ms_max": 0.0,
    }


def test_public_commons_metrics_use_the_same_operations_boundary() -> None:
    with TestClient(metrics_app()) as client:
        hidden = client.get("/internal/metrics/public-commons")
        visible = client.get(
            "/internal/metrics/public-commons",
            headers={
                PROXY_TOKEN_HEADER: "test-proxy-token-with-at-least-32-characters"
            },
        )

    assert hidden.status_code == 404
    assert visible.status_code == 200
    assert visible.json() == {
        "projection_reads": 7,
        "projection_read_bytes": 8_192,
        "projection_writes": 2,
        "projection_write_bytes": 4_096,
        "source_artifact_reads": 4,
        "rebuilds": 2,
        "stale_fallbacks": 1,
        "unavailable_responses": 0,
        "last_response_bytes": 1_024,
    }
