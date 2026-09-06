from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
from opennosh_api.publication.code_attestation import (
    ATTESTATION_CHECK,
    GitHubCodeAttestationService,
)
from opennosh_api.publication.forge.contracts import ForgeTerminalError

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
