from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest
from opennosh_api.public_commons.canary import (
    CommonsCanaryAlert,
    WebhookCommonsCanaryAlertDestination,
    probe_post_deploy_commons,
    run_commons_canary_monitor,
    run_post_deploy_commons_canary,
)

COMMIT = "a" * 40


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_delivery", [0, 1, 2])
@pytest.mark.parametrize("startup_state", ["quiet", "unavailable"])
async def test_periodic_monitor_deduplicates_outages_retries_delivery_and_reports_recovery(
    fail_delivery: int,
    startup_state: str,
) -> None:
    shutdown = asyncio.Event()
    states = iter([startup_state, "stale", "stale", "stale", "stale", "quiet", "quiet"])
    alerts: list[CommonsCanaryAlert] = []
    attempts = 0

    class Destination:
        async def send(self, alert: CommonsCanaryAlert) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == fail_delivery:
                raise RuntimeError("private webhook details")
            alerts.append(alert)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("build-version"):
            return response({"schema_version": "1", "version": "1.2.3.4", "commit": COMMIT})
        state = next(states, "stop")
        if state == "stop":
            shutdown.set()
            state = "quiet"
        return commons_response(state)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await run_commons_canary_monitor(
            shutdown,
            base_url="https://opennosh.example",
            expected_commit=COMMIT,
            timeout_seconds=1,
            poll_seconds=0.001,
            interval_seconds=0.001,
            alert_destination=Destination(),
            client=client,
        )
    assert [(a.phase, a.state) for a in alerts] == [
        (
            "post-deploy" if startup_state == "unavailable" and fail_delivery != 1 else "periodic",
            "unavailable",
        ),
        ("periodic", "recovered"),
    ]
    assert attempts == (3 if fail_delivery else 2)


@pytest.mark.asyncio
async def test_periodic_monitor_shutdown_does_not_probe_or_alert() -> None:
    shutdown = asyncio.Event()
    shutdown.set()

    class Destination:
        async def send(self, alert: CommonsCanaryAlert) -> None:
            raise AssertionError("unexpected alert")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("unexpected request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await run_commons_canary_monitor(
            shutdown,
            base_url="https://opennosh.example",
            expected_commit=COMMIT,
            timeout_seconds=1,
            poll_seconds=1,
            alert_destination=Destination(),
            client=client,
        )


@pytest.mark.asyncio
async def test_periodic_monitor_never_accumulates_nonconsecutive_failures() -> None:
    shutdown = asyncio.Event()
    states = iter(["quiet", "stale", "stale", "quiet", "stale", "stale", "quiet"])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("build-version"):
            return response({"schema_version": "1", "version": "1.2.3.4", "commit": COMMIT})
        state = next(states, None)
        if state is None:
            shutdown.set()
            state = "quiet"
        return commons_response(state)

    alerts: list[CommonsCanaryAlert] = []

    class RecordingDestination:
        async def send(self, alert: CommonsCanaryAlert) -> None:
            alerts.append(alert)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await run_commons_canary_monitor(
            shutdown,
            base_url="https://opennosh.example",
            expected_commit=COMMIT,
            timeout_seconds=1,
            poll_seconds=0.001,
            interval_seconds=0.001,
            alert_destination=RecordingDestination(),
            client=client,
        )
    assert alerts == []


def response(payload: object, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


def commons_response(state: str, *, commit: str = COMMIT) -> httpx.Response:
    return httpx.Response(
        200,
        json={"state": state},
        headers={"X-OpenNosh-Build-Commit": commit},
    )


@pytest.mark.asyncio
async def test_probe_waits_for_the_exact_deployed_commit() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response({"schema_version": "1", "version": "1.2.3.4", "commit": "b" * 40})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        observation = await probe_post_deploy_commons(
            client,
            base_url="https://opennosh.example",
            expected_commit=COMMIT,
        )

    assert observation.status == "pending"
    assert observation.error_code == "build_commit_mismatch"
    assert [request.url.path for request in requests] == ["/api/v1/public/build-version"]


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["quiet", "live"])
async def test_probe_accepts_truthful_commons_states(state: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("build-version"):
            return response({"schema_version": "1", "version": "1.2.3.4", "commit": COMMIT})
        return commons_response(state)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        observation = await probe_post_deploy_commons(
            client,
            base_url="https://opennosh.example",
            expected_commit=COMMIT,
        )

    assert observation.status == "healthy"
    assert observation.commons_state == state


@pytest.mark.asyncio
async def test_probe_never_accepts_commons_from_another_rolling_build() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("build-version"):
            return response({"schema_version": "1", "version": "1.2.3.4", "commit": COMMIT})
        return commons_response("quiet", commit="b" * 40)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        observation = await probe_post_deploy_commons(
            client,
            base_url="https://opennosh.example",
            expected_commit=COMMIT,
        )

    assert observation.status == "pending"
    assert observation.error_code == "commons_build_commit_mismatch"
    assert observation.observed_commit == "b" * 40


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected_error"),
    [
        ("build_http", "build_version_unavailable"),
        ("build_json", "build_version_invalid"),
        ("commons_http", "commons_endpoint_unavailable"),
        ("commons_json", "commons_response_invalid"),
    ],
)
async def test_probe_reports_bounded_endpoint_failures(
    failure: str,
    expected_error: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("build-version"):
            if failure == "build_http":
                return httpx.Response(503)
            if failure == "build_json":
                return httpx.Response(200, content=b"{")
            return response({"schema_version": "1", "version": "1.2.3.4", "commit": COMMIT})
        if failure == "commons_http":
            return httpx.Response(503)
        return commons_response("not-a-state")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        observation = await probe_post_deploy_commons(
            client,
            base_url="https://opennosh.example",
            expected_commit=COMMIT,
        )

    assert observation.status == "pending"
    assert observation.error_code == expected_error


@pytest.mark.asyncio
async def test_canary_polls_until_the_exact_build_is_healthy() -> None:
    build_requests = 0

    class Destination:
        async def send(self, _alert: CommonsCanaryAlert) -> None:
            raise AssertionError("healthy canary must not alert")

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal build_requests
        if request.url.path.endswith("build-version"):
            build_requests += 1
            commit = "b" * 40 if build_requests == 1 else COMMIT
            return response({"schema_version": "1", "version": "1.2.3.4", "commit": commit})
        return commons_response("quiet")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        observation = await run_post_deploy_commons_canary(
            asyncio.Event(),
            base_url="https://opennosh.example",
            expected_commit=COMMIT,
            timeout_seconds=1,
            poll_seconds=0.001,
            alert_destination=Destination(),
            client=client,
        )

    assert observation is not None
    assert observation.status == "healthy"
    assert observation.commons_state == "quiet"
    assert build_requests == 2


@pytest.mark.asyncio
async def test_canary_alerts_once_when_commons_returns_unavailable() -> None:
    alerts: list[CommonsCanaryAlert] = []

    class Destination:
        async def send(self, alert: CommonsCanaryAlert) -> None:
            alerts.append(alert)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("build-version"):
            return response({"schema_version": "1", "version": "1.2.3.4", "commit": COMMIT})
        return commons_response("unavailable")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        observation = await run_post_deploy_commons_canary(
            asyncio.Event(),
            base_url="https://opennosh.example",
            expected_commit=COMMIT,
            timeout_seconds=30,
            poll_seconds=1,
            alert_destination=Destination(),
            client=client,
        )

    assert observation is not None
    assert observation.status == "unavailable"
    assert len(alerts) == 1
    assert alerts[0].error_code == "commons_state_unavailable"
    assert alerts[0].expected_commit == COMMIT


@pytest.mark.asyncio
async def test_canary_timeout_alerts_with_the_last_bounded_error() -> None:
    alerts: list[CommonsCanaryAlert] = []

    class Destination:
        async def send(self, alert: CommonsCanaryAlert) -> None:
            alerts.append(alert)

    def handler(_request: httpx.Request) -> httpx.Response:
        return response({"schema_version": "1", "version": "1.2.3.4", "commit": "b" * 40})

    ticks = iter((0.0, 2.0))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        observation = await run_post_deploy_commons_canary(
            asyncio.Event(),
            base_url="https://opennosh.example",
            expected_commit=COMMIT,
            timeout_seconds=1,
            poll_seconds=1,
            alert_destination=Destination(),
            client=client,
            monotonic=lambda: next(ticks),
        )

    assert observation is not None
    assert observation.status == "unavailable"
    assert alerts[0].error_code == "build_commit_mismatch"
    assert alerts[0].observed_commit == "b" * 40


@pytest.mark.asyncio
async def test_canary_logs_alert_delivery_failure_without_exposing_destination(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Destination:
        async def send(self, _alert: CommonsCanaryAlert) -> None:
            raise RuntimeError("sensitive destination detail")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("build-version"):
            return response({"schema_version": "1", "version": "1.2.3.4", "commit": COMMIT})
        return commons_response("unavailable")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        observation = await run_post_deploy_commons_canary(
            asyncio.Event(),
            base_url="https://opennosh.example",
            expected_commit=COMMIT,
            timeout_seconds=1,
            poll_seconds=0.001,
            alert_destination=Destination(),
            client=client,
        )

    assert observation is not None
    assert observation.status == "unavailable"
    assert "error_type=RuntimeError" in caplog.text
    assert "sensitive destination detail" not in caplog.text


@pytest.mark.asyncio
async def test_webhook_destination_formats_a_redacted_slack_payload() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text="ok")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        destination = WebhookCommonsCanaryAlertDestination(
            "https://hooks.slack.com/services/example/test/value",
            client=client,
        )
        await destination.send(
            CommonsCanaryAlert(
                error_code="commons_state_unavailable",
                expected_commit=COMMIT,
                observed_commit=COMMIT,
                build_version="1.2.3.4",
                observed_at=datetime(2026, 9, 8, 12, 30, tzinfo=UTC),
            )
        )

    payload = json.loads(requests[0].content)
    assert payload == {
        "text": (
            "OpenNosh post-deploy Commons canary is unavailable for build "
            "`aaaaaaaaaaaa` (error: `commons_state_unavailable`)."
        ),
        "schema": "opennosh.public-commons.post-deploy-canary.v1",
        "component": "public-commons",
        "state": "unavailable",
        "error_code": "commons_state_unavailable",
        "expected_commit": COMMIT,
        "observed_commit": COMMIT,
        "build_version": "1.2.3.4",
        "occurred_at": "2026-09-08T12:30:00+00:00",
    }
