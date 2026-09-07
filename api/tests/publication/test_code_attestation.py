from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from opennosh_api.publication.code_attestation import (
    ATTESTATION_CHECK,
    CodeAttestationAvailabilityAlert,
    CodeAttestationReport,
    GitHubCodeAttestationService,
    WebhookCodeAttestationAlertDestination,
    run_code_attestation_loop,
)
from opennosh_api.publication.forge.contracts import (
    ForgeRetryableError,
    ForgeTerminalError,
)

HEAD = "a" * 40
NEW_HEAD = "b" * 40


def _pull(*, branch: str = "codex/release-control", head: str = HEAD) -> dict[str, Any]:
    return {
        "number": 17,
        "state": "open",
        "head": {"sha": head, "ref": branch},
        "base": {"ref": "main"},
    }


def _token(value: str) -> Callable[[], Awaitable[str]]:
    async def provide() -> str:
        return value

    return provide


def _service(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[GitHubCodeAttestationService, httpx.AsyncClient]:
    client = httpx.AsyncClient(
        base_url="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )
    return (
        GitHubCodeAttestationService(
            _token("read-token"),
            _token("write-token"),
            attester_app_id=654,
            propagate_main_merges=False,
            client=client,
        ),
        client,
    )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"repository": "missing-separator"}, "repository"),
        ({"attester_app_id": 0}, "App ID"),
        ({"base_branch": "bad branch"}, "base branch"),
        ({"managed_path_prefix": "/packs/"}, "path prefix"),
    ],
)
def test_constructor_rejects_invalid_security_boundaries(
    overrides: dict[str, object], message: str
) -> None:
    arguments: dict[str, object] = {
        "attester_app_id": 654,
        "repository": "RujitRaval/opennosh",
        "base_branch": "main",
        "managed_path_prefix": "packs/",
    }
    arguments.update(overrides)

    with pytest.raises(ValueError, match=message):
        GitHubCodeAttestationService(  # type: ignore[arg-type]
            _token("read-token"),
            _token("write-token"),
            **arguments,
        )


@pytest.mark.parametrize(
    "candidate",
    [
        None,
        {**_pull(), "state": "closed"},
        {**_pull(), "number": 0},
        {**_pull(), "head": {"sha": "bad", "ref": "branch"}},
        {**_pull(), "head": {"sha": HEAD, "ref": ""}},
        {**_pull(), "base": {"ref": "develop"}},
    ],
)
def test_malformed_pull_request_candidates_fail_closed(candidate: object) -> None:
    service, _client = _service(lambda _request: httpx.Response(500))
    with pytest.raises(ForgeTerminalError):
        service._candidate(candidate)


@pytest.mark.parametrize("value", [None, {"filename": "../packs/rice.json"}])
def test_malformed_changed_paths_fail_closed(value: object) -> None:
    with pytest.raises(ForgeTerminalError, match="file_invalid"):
        GitHubCodeAttestationService._changed_paths(value)


@pytest.mark.asyncio
async def test_http_failures_and_invalid_tokens_fail_closed() -> None:
    def unavailable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    service, client = _service(unavailable)
    try:
        with pytest.raises(ForgeRetryableError):
            await service._read("/unavailable")
        with pytest.raises(ForgeRetryableError):
            await service._write("/unavailable", {})
        with pytest.raises(ForgeTerminalError, match="token_invalid"):
            await service._token(_token("invalid token"))
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (429, ForgeRetryableError),
        (403, ForgeTerminalError),
        (418, ForgeTerminalError),
    ],
)
def test_response_statuses_are_classified(status: int, expected: type[Exception]) -> None:
    with pytest.raises(expected):
        GitHubCodeAttestationService._decode(httpx.Response(status), expected=200)


def test_non_json_success_response_fails_closed() -> None:
    with pytest.raises(ForgeTerminalError, match="response_invalid"):
        GitHubCodeAttestationService._decode(httpx.Response(200, content=b"not-json"), expected=200)


@pytest.mark.asyncio
async def test_reconciler_loop_retries_then_reports_recovery_and_stops(
    caplog: pytest.LogCaptureFixture,
) -> None:
    shutdown = asyncio.Event()

    class Service:
        calls = 0

        async def reconcile_once(self) -> CodeAttestationReport:
            self.calls += 1
            if self.calls == 1:
                return CodeAttestationReport(0, 0, 0, 0, 0, 0, 0, 0, 0)
            if self.calls == 2:
                raise ForgeRetryableError("temporary")
            shutdown.set()
            return CodeAttestationReport(1, 0, 0, 1, 0, 1, 0, 1, 0)

    service = Service()
    with caplog.at_level(logging.WARNING):
        await run_code_attestation_loop(  # type: ignore[arg-type]
            service, shutdown, interval_seconds=0.001
        )

    assert service.calls == 3
    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "state=retrying" in message and "consecutive_failures=1" in message for message in messages
    )
    assert any(
        "state=recovered" in message
        and "failed_attempts=1" in message
        and "outage_alerted=false" in message
        for message in messages
    )


@pytest.mark.asyncio
async def test_reconciler_loop_escalates_sustained_outage_at_bounded_intervals(
    caplog: pytest.LogCaptureFixture,
) -> None:
    shutdown = asyncio.Event()

    class Service:
        calls = 0

        async def reconcile_once(self) -> CodeAttestationReport:
            self.calls += 1
            if self.calls <= 6:
                raise ForgeRetryableError("temporary")
            shutdown.set()
            return CodeAttestationReport(0, 0, 0, 0, 0, 0, 0, 0, 0)

    service = Service()
    with caplog.at_level(logging.WARNING):
        await run_code_attestation_loop(  # type: ignore[arg-type]
            service,
            shutdown,
            interval_seconds=0.001,
            outage_failure_threshold=3,
        )

    assert service.calls == 7
    outage_records = [
        record
        for record in caplog.records
        if record.levelno == logging.ERROR and "state=outage" in record.getMessage()
    ]
    assert [record.getMessage() for record in outage_records] == [
        "Governance code attestation availability state=outage error=temporary "
        "consecutive_failures=3 alert_every_failures=3",
        "Governance code attestation availability state=outage error=temporary "
        "consecutive_failures=6 alert_every_failures=3",
    ]
    assert any(
        "state=recovered" in record.getMessage()
        and "failed_attempts=6" in record.getMessage()
        and "outage_alerted=true" in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_reconciler_loop_delivers_bounded_outage_and_recovery_alerts() -> None:
    shutdown = asyncio.Event()
    alerts: list[CodeAttestationAvailabilityAlert] = []

    class Service:
        calls = 0

        async def reconcile_once(self) -> CodeAttestationReport:
            self.calls += 1
            if self.calls <= 6:
                raise ForgeRetryableError("temporary")
            shutdown.set()
            return CodeAttestationReport(0, 0, 0, 0, 0, 0, 0, 0, 0)

    class Destination:
        async def send(self, alert: CodeAttestationAvailabilityAlert) -> None:
            alerts.append(alert)

    await run_code_attestation_loop(  # type: ignore[arg-type]
        Service(),
        shutdown,
        interval_seconds=0.001,
        outage_failure_threshold=3,
        alert_destination=Destination(),
    )

    assert [(alert.state, alert.error_code, alert.failed_attempts) for alert in alerts] == [
        ("outage", "temporary", 3),
        ("outage", "temporary", 6),
        ("recovered", "temporary", 6),
    ]


@pytest.mark.asyncio
async def test_webhook_alert_destination_sends_only_redacted_contract() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    destination = WebhookCodeAttestationAlertDestination(
        "https://alerts.example.test/opennosh",
        bearer_token="secret-token",
        client=client,
    )
    occurred_at = datetime(2026, 9, 5, 12, 30, tzinfo=UTC)
    try:
        await destination.send(
            CodeAttestationAvailabilityAlert(
                state="outage",
                error_code="github_code_attestation_unavailable",
                failed_attempts=3,
                occurred_at=occurred_at,
            )
        )
    finally:
        await client.aclose()

    assert len(requests) == 1
    assert requests[0].headers["Authorization"] == "Bearer secret-token"
    assert json.loads(requests[0].content) == {
        "text": (
            "OpenNosh governance code attestation is unavailable after 3 consecutive "
            "attempts (error: `github_code_attestation_unavailable`)."
        ),
        "schema": "opennosh.governance-code-attestation.availability.v1",
        "component": "governance-code-attestation",
        "state": "outage",
        "error_code": "github_code_attestation_unavailable",
        "failed_attempts": 3,
        "occurred_at": "2026-09-05T12:30:00+00:00",
    }


@pytest.mark.asyncio
async def test_webhook_alert_destination_formats_recovery_for_slack() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text="ok")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        destination = WebhookCodeAttestationAlertDestination(
            "https://hooks.slack.com/services/example/test/value",
            client=client,
        )
        await destination.send(
            CodeAttestationAvailabilityAlert(
                state="recovered",
                error_code="github_code_attestation_unavailable",
                failed_attempts=6,
                occurred_at=datetime(2026, 9, 5, 12, 45, tzinfo=UTC),
            )
        )

    assert len(requests) == 1
    assert "Authorization" not in requests[0].headers
    assert json.loads(requests[0].content)["text"] == (
        "OpenNosh governance code attestation recovered after 6 failed attempts "
        "(previous error: `github_code_attestation_unavailable`)."
    )


@pytest.mark.asyncio
async def test_external_alert_delivery_failure_does_not_stop_reconciliation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    shutdown = asyncio.Event()

    class Service:
        calls = 0

        async def reconcile_once(self) -> CodeAttestationReport:
            self.calls += 1
            if self.calls == 1:
                raise ForgeRetryableError("temporary")
            shutdown.set()
            return CodeAttestationReport(0, 0, 0, 0, 0, 0, 0, 0, 0)

    class FailedDestination:
        async def send(self, _alert: CodeAttestationAvailabilityAlert) -> None:
            raise RuntimeError("destination secret detail")

    with caplog.at_level(logging.ERROR):
        await run_code_attestation_loop(  # type: ignore[arg-type]
            Service(),
            shutdown,
            interval_seconds=0.001,
            outage_failure_threshold=1,
            alert_destination=FailedDestination(),
        )

    assert "error_type=RuntimeError" in caplog.text
    assert "destination secret detail" not in caplog.text


@pytest.mark.asyncio
async def test_reconciler_loop_rejects_nonpositive_interval() -> None:
    with pytest.raises(ValueError, match="interval must be positive"):
        await run_code_attestation_loop(  # type: ignore[arg-type]
            object(), asyncio.Event(), interval_seconds=0
        )

    with pytest.raises(ValueError, match="outage threshold must be positive"):
        await run_code_attestation_loop(  # type: ignore[arg-type]
            object(),
            asyncio.Event(),
            interval_seconds=1,
            outage_failure_threshold=0,
        )


@pytest.mark.asyncio
async def test_ordinary_code_pull_request_receives_source_pinned_success() -> None:
    writes: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            assert request.headers["Authorization"] == "Bearer read-token"
        if request.url.path.endswith("/pulls"):
            return httpx.Response(200, json=[_pull()])
        if request.url.path.endswith("/pulls/17/files"):
            return httpx.Response(200, json=[{"filename": "api/opennosh_api/app.py"}])
        if request.url.path.endswith("/pulls/17"):
            return httpx.Response(200, json=_pull())
        if request.url.path.endswith(f"/commits/{HEAD}/check-runs"):
            return httpx.Response(200, json={"check_runs": []})
        if request.method == "POST" and request.url.path.endswith("/check-runs"):
            assert request.headers["Authorization"] == "Bearer write-token"
            writes.append(json.loads(request.read()))
            return httpx.Response(201, json={"id": 1})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    service, client = _service(handler)
    try:
        report = await service.reconcile_once()
    finally:
        await client.aclose()

    assert report.passed == 1
    assert report.blocked == 0
    assert len(writes) == 1
    assert writes[0] == {
        "name": ATTESTATION_CHECK,
        "head_sha": HEAD,
        "status": "completed",
        "conclusion": "success",
        "output": {
            "title": "Non-governed code change verified",
            "summary": (
                "The exact pull-request head changes no managed packs/ data; "
                "database-backed contribution authorization is not applicable."
            ),
        },
    }


@pytest.mark.asyncio
async def test_ordinary_pull_request_touching_governed_data_is_blocked() -> None:
    conclusions: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/pulls"):
            return httpx.Response(200, json=[_pull()])
        if request.url.path.endswith("/pulls/17/files"):
            return httpx.Response(200, json=[{"filename": "packs/foods/rice.json"}])
        if request.url.path.endswith("/pulls/17"):
            return httpx.Response(200, json=_pull())
        if request.url.path.endswith(f"/commits/{HEAD}/check-runs"):
            return httpx.Response(200, json={"check_runs": []})
        if request.method == "POST":
            conclusions.append(json.loads(request.read())["conclusion"])
            return httpx.Response(201, json={"id": 2})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    service, client = _service(handler)
    try:
        report = await service.reconcile_once()
    finally:
        await client.aclose()

    assert report.blocked == 1
    assert report.passed == 0
    assert conclusions == ["action_required"]


@pytest.mark.asyncio
async def test_rename_out_of_governed_data_is_blocked_by_previous_path() -> None:
    conclusions: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/pulls"):
            return httpx.Response(200, json=[_pull()])
        if request.url.path.endswith("/pulls/17/files"):
            return httpx.Response(
                200,
                json=[
                    {
                        "filename": "archive/rice.json",
                        "previous_filename": "packs/foods/rice.json",
                        "status": "renamed",
                    }
                ],
            )
        if request.url.path.endswith("/pulls/17"):
            return httpx.Response(200, json=_pull())
        if request.url.path.endswith(f"/commits/{HEAD}/check-runs"):
            return httpx.Response(200, json={"check_runs": []})
        if request.method == "POST":
            conclusions.append(json.loads(request.read())["conclusion"])
            return httpx.Response(201, json={"id": 4})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    service, client = _service(handler)
    try:
        report = await service.reconcile_once()
    finally:
        await client.aclose()

    assert report.blocked == 1
    assert conclusions == ["action_required"]


@pytest.mark.asyncio
async def test_database_governed_branch_remains_exclusive_to_publication_attester() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/pulls"):
            return httpx.Response(
                200,
                json=[_pull(branch="opennosh/contribution/0123456789abcdef01234567")],
            )
        raise AssertionError("Governed branch must not be inspected or attested")

    service, client = _service(handler)
    try:
        report = await service.reconcile_once()
    finally:
        await client.aclose()

    assert report.governed_skipped == 1
    assert calls == ["/repos/RujitRaval/opennosh/pulls"]


@pytest.mark.asyncio
async def test_spoofed_check_does_not_satisfy_attestation() -> None:
    posts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal posts
        if request.url.path.endswith("/pulls"):
            return httpx.Response(200, json=[_pull()])
        if request.url.path.endswith("/pulls/17/files"):
            return httpx.Response(200, json=[{"filename": "docs/release.md"}])
        if request.url.path.endswith("/pulls/17"):
            return httpx.Response(200, json=_pull())
        if request.url.path.endswith(f"/commits/{HEAD}/check-runs"):
            return httpx.Response(
                200,
                json={
                    "check_runs": [
                        {
                            "name": ATTESTATION_CHECK,
                            "head_sha": HEAD,
                            "status": "completed",
                            "conclusion": "success",
                            "app": {"id": 999},
                        }
                    ]
                },
            )
        if request.method == "POST":
            posts += 1
            return httpx.Response(201, json={"id": 3})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    service, client = _service(handler)
    try:
        report = await service.reconcile_once()
    finally:
        await client.aclose()

    assert report.passed == 1
    assert posts == 1


@pytest.mark.asyncio
async def test_exact_existing_attester_check_is_idempotent() -> None:
    posts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal posts
        if request.url.path.endswith("/pulls"):
            return httpx.Response(200, json=[_pull()])
        if request.url.path.endswith("/pulls/17/files"):
            return httpx.Response(200, json=[{"filename": "README.md"}])
        if request.url.path.endswith("/pulls/17"):
            return httpx.Response(200, json=_pull())
        if request.url.path.endswith(f"/commits/{HEAD}/check-runs"):
            return httpx.Response(
                200,
                json={
                    "check_runs": [
                        {
                            "name": ATTESTATION_CHECK,
                            "head_sha": HEAD,
                            "status": "completed",
                            "conclusion": "success",
                            "app": {"id": 654},
                        }
                    ]
                },
            )
        if request.method == "POST":
            posts += 1
            return httpx.Response(201, json={"id": 7})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    service, client = _service(handler)
    try:
        report = await service.reconcile_once()
    finally:
        await client.aclose()

    assert report.unchanged == 1
    assert posts == 0


@pytest.mark.asyncio
async def test_changed_head_is_not_attested() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/pulls"):
            return httpx.Response(200, json=[_pull()])
        if request.url.path.endswith("/pulls/17/files"):
            return httpx.Response(200, json=[{"filename": "README.md"}])
        if request.url.path.endswith("/pulls/17"):
            return httpx.Response(200, json=_pull(head=NEW_HEAD))
        raise AssertionError("A changed head must not reach check inspection or write")

    service, client = _service(handler)
    try:
        report = await service.reconcile_once()
    finally:
        await client.aclose()

    assert report.passed == 0
    assert report.blocked == 0


@pytest.mark.asyncio
async def test_invalid_previous_path_fails_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/pulls"):
            return httpx.Response(200, json=[_pull()])
        if request.url.path.endswith("/pulls/17/files"):
            return httpx.Response(
                200,
                json=[
                    {
                        "filename": "archive/rice.json",
                        "previous_filename": "packs/../rice.json",
                    }
                ],
            )
        raise AssertionError("Invalid paths must fail before head or check inspection")

    service, client = _service(handler)
    try:
        with pytest.raises(ForgeTerminalError, match="file_invalid"):
            await service.reconcile_once()
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_unbounded_open_pull_request_page_fails_closed() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_pull()] * 100)

    service, client = _service(handler)
    try:
        with pytest.raises(ForgeTerminalError, match="unbounded"):
            await service.reconcile_once()
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_attested_pull_request_is_propagated_to_exact_main_merge() -> None:
    merge_commit = "c" * 40
    writes: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/commits"):
            return httpx.Response(200, json=[{"sha": merge_commit}])
        if path.endswith(f"/commits/{merge_commit}/check-runs"):
            return httpx.Response(200, json={"check_runs": []})
        if path.endswith(f"/commits/{merge_commit}/pulls"):
            return httpx.Response(
                200,
                json=[
                    {
                        "number": 17,
                        "state": "closed",
                        "merged_at": "2026-09-05T12:00:00Z",
                        "merge_commit_sha": merge_commit,
                        "head": {"sha": HEAD},
                        "base": {"ref": "main"},
                    }
                ],
            )
        if path.endswith("/pulls"):
            return httpx.Response(200, json=[])
        if path.endswith(f"/commits/{HEAD}/check-runs"):
            return httpx.Response(
                200,
                json={
                    "check_runs": [
                        {
                            "name": ATTESTATION_CHECK,
                            "head_sha": HEAD,
                            "status": "completed",
                            "conclusion": "success",
                            "app": {"id": 654},
                        }
                    ]
                },
            )
        if request.method == "POST" and path.endswith("/check-runs"):
            writes.append(json.loads(request.read()))
            return httpx.Response(201, json={"id": 5})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = httpx.AsyncClient(
        base_url="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )
    service = GitHubCodeAttestationService(
        _token("read-token"),
        _token("write-token"),
        attester_app_id=654,
        client=client,
    )
    try:
        report = await service.reconcile_once()
    finally:
        await client.aclose()

    assert report.merge_propagated == 1
    assert writes[0]["head_sha"] == merge_commit
    assert writes[0]["conclusion"] == "success"


@pytest.mark.asyncio
async def test_existing_main_attestation_is_idempotent() -> None:
    merge_commit = "f" * 40

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/pulls"):
            return httpx.Response(200, json=[])
        if path.endswith("/commits"):
            return httpx.Response(200, json=[{"sha": merge_commit}])
        if path.endswith(f"/commits/{merge_commit}/check-runs"):
            return httpx.Response(
                200,
                json={
                    "check_runs": [
                        {
                            "name": ATTESTATION_CHECK,
                            "head_sha": merge_commit,
                            "status": "completed",
                            "conclusion": "success",
                            "app": {"id": 654},
                        }
                    ]
                },
            )
        raise AssertionError("Existing main attestation must skip association and write")

    client = httpx.AsyncClient(
        base_url="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )
    service = GitHubCodeAttestationService(
        _token("read-token"),
        _token("write-token"),
        attester_app_id=654,
        client=client,
    )
    try:
        report = await service.reconcile_once()
    finally:
        await client.aclose()

    assert report.merge_unchanged == 1
    assert report.merge_propagated == 0


@pytest.mark.asyncio
async def test_direct_or_bypassed_main_commit_is_not_attested() -> None:
    merge_commit = "d" * 40
    bypassed_merge = "e" * 40
    posts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal posts
        path = request.url.path
        if path.endswith("/commits"):
            return httpx.Response(
                200,
                json=[{"sha": merge_commit}, {"sha": bypassed_merge}],
            )
        if path.endswith(f"/commits/{merge_commit}/check-runs") or path.endswith(
            f"/commits/{bypassed_merge}/check-runs"
        ):
            return httpx.Response(200, json={"check_runs": []})
        if path.endswith(f"/commits/{merge_commit}/pulls"):
            return httpx.Response(200, json=[])
        if path.endswith(f"/commits/{bypassed_merge}/pulls"):
            return httpx.Response(
                200,
                json=[
                    {
                        "number": 18,
                        "state": "closed",
                        "merged_at": "2026-09-05T12:00:00Z",
                        "merge_commit_sha": bypassed_merge,
                        "head": {"sha": HEAD},
                        "base": {"ref": "main"},
                    }
                ],
            )
        if path.endswith(f"/commits/{HEAD}/check-runs"):
            return httpx.Response(200, json={"check_runs": []})
        if path.endswith("/pulls"):
            return httpx.Response(200, json=[])
        if request.method == "POST":
            posts += 1
            return httpx.Response(201, json={"id": 6})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = httpx.AsyncClient(
        base_url="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )
    service = GitHubCodeAttestationService(
        _token("read-token"),
        _token("write-token"),
        attester_app_id=654,
        client=client,
    )
    try:
        report = await service.reconcile_once()
    finally:
        await client.aclose()

    assert report.merge_untrusted == 2
    assert posts == 0
