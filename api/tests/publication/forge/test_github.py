from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from opennosh_api.governance.contracts import (
    PROTECTED_STATUS_CHECKS,
    ApprovedChangeSet,
    ApprovedFileChange,
)
from opennosh_api.governance.policy import GovernanceBinding
from opennosh_api.publication.forge.contracts import (
    ForgeCheckState,
    ForgeConflictError,
    ForgeMutation,
    ForgePullRequestState,
    ForgeRetryableError,
)
from opennosh_api.publication.forge.github import (
    GitHubAppInstallationTokenProvider,
    GitHubForgeClient,
    GitHubGovernanceAttester,
)

NOW = datetime(2026, 8, 26, 12, tzinfo=UTC)
PATH = "packs/global-core/foods/lentils.json"
CONTENT = '{"name":"Lentils"}\n'
LICENSE = b"CC0"
TREE_SHA = "e" * 40


async def installation_token() -> str:
    return "installation-token"


def mutation_binding() -> GovernanceBinding:
    return GovernanceBinding(
        publication_id=UUID("11111111-1111-4111-8111-111111111111"),
        decision_id=UUID("22222222-2222-4222-8222-222222222222"),
        pack_id="global-core",
        contributor_actor_id=UUID("33333333-3333-4333-8333-333333333333"),
        approving_actor_id=UUID("44444444-4444-4444-8444-444444444444"),
        approved_at=NOW - timedelta(hours=1),
        approved_changes=ApprovedChangeSet.build(
            pack_id="global-core",
            files=(ApprovedFileChange(path=PATH, content=CONTENT),),
        ),
        expected_base_commit="a" * 40,
        required_checks=PROTECTED_STATUS_CHECKS,
        forge_target="github:RujitRaval/opennosh",
        role_granted_at=NOW - timedelta(days=1),
    )


def response(status: int, value: object) -> httpx.Response:
    return httpx.Response(
        status,
        headers={"content-type": "application/json"},
        content=json.dumps(value).encode(),
    )


def handler(  # type: ignore[no-untyped-def]
    *,
    extra_file: bool = False,
    extra_tree_file: bool = False,
    duplicate_failed_check: bool = False,
):
    def route(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/pulls"):
            return response(200, [{"number": 42}])
        if path.endswith("/pulls/42"):
            return response(
                200,
                {
                    "number": 42,
                    "state": "closed",
                    "merged": True,
                    "merged_at": "2026-08-26T12:00:00Z",
                    "merge_commit_sha": "c" * 40,
                    "html_url": "https://github.test/pull/42",
                    "head": {"sha": "d" * 40},
                    "node_id": "PR_test",
                },
            )
        if path.endswith("/check-runs"):
            runs = [{"name": "schema", "status": "completed", "conclusion": "success"}]
            if duplicate_failed_check:
                runs.insert(
                    0,
                    {"name": "schema", "status": "completed", "conclusion": "failure"},
                )
            return response(
                200,
                {"check_runs": runs},
            )
        if path == "/graphql":
            return response(
                200,
                {"data": {"node": {"autoMergeRequest": None}}},
            )
        if path.endswith("/pulls/42/files"):
            files = [{"filename": PATH}]
            if extra_file:
                files.append({"filename": "README.md"})
            return response(200, files)
        if path.endswith(f"/git/commits/{'c' * 40}"):
            return response(200, {"tree": {"sha": TREE_SHA}})
        if path.endswith(f"/git/trees/{TREE_SHA}"):
            entries = [
                {
                    "path": PATH,
                    "type": "blob",
                    "sha": "1" * 40,
                    "size": len(CONTENT.encode()),
                },
                {
                    "path": "packs/CC0-1.0.txt",
                    "type": "blob",
                    "sha": "2" * 40,
                    "size": len(LICENSE),
                },
            ]
            if extra_tree_file:
                entries.insert(
                    1,
                    {
                        "path": "packs/global-core/foods/unapproved.json",
                        "type": "blob",
                        "sha": "3" * 40,
                        "size": 2,
                    },
                )
            return response(
                200,
                {
                    "truncated": False,
                    "tree": entries,
                },
            )
        if path.endswith(f"/git/blobs/{'1' * 40}"):
            encoded = base64.b64encode(CONTENT.encode()).decode()
            return response(200, {"encoding": "base64", "content": encoded + "\n"})
        if path.endswith(f"/git/blobs/{'2' * 40}"):
            return response(
                200,
                {
                    "encoding": "base64",
                    "content": base64.b64encode(LICENSE).decode(),
                },
            )
        if "/contents/" in path:
            return response(
                200,
                {
                    "encoding": "base64",
                    "content": base64.b64encode(CONTENT.encode()).decode(),
                },
            )
        return response(404, {"message": "unexpected test route"})

    return route


@pytest.mark.asyncio
async def test_github_observation_recomputes_exact_merged_payload() -> None:
    http = httpx.AsyncClient(
        base_url="https://api.github.test", transport=httpx.MockTransport(handler())
    )
    client = GitHubForgeClient(installation_token, client=http)
    binding = mutation_binding()
    observed = await client.observe(ForgeMutation(binding=binding, idempotency_key="b" * 64))

    assert observed.state is ForgePullRequestState.MERGED
    assert observed.merged_payload_digest == binding.approved_changes.digest
    assert observed.merged_tree_digest == hashlib.sha256(
        json.dumps(
            {"commit": "c" * 40, "tree": TREE_SHA},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    ).hexdigest()
    await http.aclose()


@pytest.mark.asyncio
async def test_github_reads_only_bounded_pack_material_from_verified_merge() -> None:
    http = httpx.AsyncClient(
        base_url="https://api.github.test", transport=httpx.MockTransport(handler())
    )
    client = GitHubForgeClient(installation_token, client=http)
    mutation = ForgeMutation(binding=mutation_binding(), idempotency_key="b" * 64)
    observed = await client.observe(mutation)
    assert observed.merged_commit is not None
    assert observed.merged_tree_digest is not None

    material = await client.read_merged_pack(
        mutation,
        expected_commit=observed.merged_commit,
        expected_tree_digest=observed.merged_tree_digest,
    )

    assert material.files == {
        "CC0-1.0.txt": LICENSE,
        "global-core/foods/lentils.json": CONTENT.encode(),
    }
    await http.aclose()


@pytest.mark.asyncio
async def test_github_rejects_unapproved_file_already_present_in_pack_tree() -> None:
    http = httpx.AsyncClient(
        base_url="https://api.github.test",
        transport=httpx.MockTransport(handler(extra_tree_file=True)),
    )
    client = GitHubForgeClient(installation_token, client=http)
    mutation = ForgeMutation(binding=mutation_binding(), idempotency_key="b" * 64)
    observed = await client.observe(mutation)
    assert observed.merged_commit is not None
    assert observed.merged_tree_digest is not None

    with pytest.raises(ForgeConflictError, match="merged_pack_inventory_mismatch"):
        await client.read_merged_pack(
            mutation,
            expected_commit=observed.merged_commit,
            expected_tree_digest=observed.merged_tree_digest,
        )

    await http.aclose()


@pytest.mark.asyncio
async def test_github_observation_rejects_unapproved_changed_path() -> None:
    http = httpx.AsyncClient(
        base_url="https://api.github.test",
        transport=httpx.MockTransport(handler(extra_file=True)),
    )
    client = GitHubForgeClient(installation_token, client=http)
    observed = await client.observe(
        ForgeMutation(binding=mutation_binding(), idempotency_key="b" * 64)
    )

    assert observed.merged_payload_digest is None
    await http.aclose()


@pytest.mark.asyncio
async def test_github_observation_never_masks_duplicate_failed_check() -> None:
    http = httpx.AsyncClient(
        base_url="https://api.github.test",
        transport=httpx.MockTransport(handler(duplicate_failed_check=True)),
    )
    client = GitHubForgeClient(installation_token, client=http)

    observed = await client.observe(
        ForgeMutation(binding=mutation_binding(), idempotency_key="b" * 64)
    )

    assert observed.checks == (("schema", ForgeCheckState.FAILED),)
    await http.aclose()


@pytest.mark.asyncio
async def test_github_auto_merge_rechecks_base_head_and_uses_squash() -> None:
    requests: list[httpx.Request] = []

    def route(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/pulls"):
            return response(200, [{"number": 42}])
        if request.url.path.endswith("/pulls/42"):
            return response(
                200,
                {
                    "number": 42,
                    "state": "open",
                    "merged": False,
                    "head": {"sha": "d" * 40},
                    "node_id": "PR_test",
                },
            )
        if request.url.path.endswith("/git/ref/heads/main"):
            return response(200, {"object": {"sha": "a" * 40}})
        if request.url.path == "/graphql":
            payload = json.loads(request.content)
            if "enablePullRequestAutoMerge" not in payload["query"]:
                return response(200, {"data": {"node": {"autoMergeRequest": None}}})
            return response(
                200,
                {
                    "data": {
                        "enablePullRequestAutoMerge": {
                            "pullRequest": {
                                "autoMergeRequest": {"mergeMethod": "SQUASH"}
                            }
                        }
                    }
                },
            )
        return response(404, {"message": "unexpected test route"})

    http = httpx.AsyncClient(
        base_url="https://api.github.test", transport=httpx.MockTransport(route)
    )
    client = GitHubForgeClient(installation_token, client=http)
    await client.enable_protected_auto_merge(
        ForgeMutation(binding=mutation_binding(), idempotency_key="b" * 64),
        expected_head_commit="d" * 40,
    )

    merge_request = next(
        request
        for request in requests
        if request.url.path == "/graphql"
        and "enablePullRequestAutoMerge" in json.loads(request.content)["query"]
    )
    merge_payload = json.loads(merge_request.content)
    assert "mergeMethod:SQUASH" in merge_payload["query"]
    assert merge_payload["variables"] == {"id": "PR_test"}
    assert merge_request.headers["authorization"] == "Bearer installation-token"
    await http.aclose()


@pytest.mark.asyncio
async def test_github_auto_merge_retry_recovers_after_lost_success_response() -> None:
    auto_merge_enabled = False
    enable_requests = 0

    def route(request: httpx.Request) -> httpx.Response:
        nonlocal auto_merge_enabled, enable_requests
        if request.url.path.endswith("/pulls"):
            return response(200, [{"number": 42}])
        if request.url.path.endswith("/pulls/42"):
            return response(
                200,
                {
                    "number": 42,
                    "state": "open",
                    "merged": False,
                    "head": {"sha": "d" * 40},
                    "node_id": "PR_test",
                },
            )
        if request.url.path.endswith("/git/ref/heads/main"):
            return response(200, {"object": {"sha": "a" * 40}})
        if request.url.path == "/graphql":
            payload = json.loads(request.content)
            if "enablePullRequestAutoMerge" not in payload["query"]:
                request_value = (
                    {"mergeMethod": "SQUASH"} if auto_merge_enabled else None
                )
                return response(
                    200,
                    {"data": {"node": {"autoMergeRequest": request_value}}},
                )
            enable_requests += 1
            auto_merge_enabled = True
            raise httpx.ReadTimeout("success response lost", request=request)
        return response(404, {"message": "unexpected test route"})

    http = httpx.AsyncClient(
        base_url="https://api.github.test", transport=httpx.MockTransport(route)
    )
    client = GitHubForgeClient(installation_token, client=http)
    mutation = ForgeMutation(binding=mutation_binding(), idempotency_key="b" * 64)

    with pytest.raises(ForgeRetryableError, match="github_unavailable"):
        await client.enable_protected_auto_merge(
            mutation,
            expected_head_commit="d" * 40,
        )
    await client.enable_protected_auto_merge(
        mutation,
        expected_head_commit="d" * 40,
    )

    assert enable_requests == 1
    await http.aclose()


@pytest.mark.asyncio
async def test_github_auto_merge_rejects_stale_base_without_graphql_request() -> None:
    paths: list[str] = []

    def route(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/pulls"):
            return response(200, [{"number": 42}])
        if request.url.path.endswith("/pulls/42"):
            return response(
                200,
                {
                    "number": 42,
                    "state": "open",
                    "merged": False,
                    "head": {"sha": "d" * 40},
                    "node_id": "PR_test",
                },
            )
        if request.url.path.endswith("/git/ref/heads/main"):
            return response(200, {"object": {"sha": "f" * 40}})
        return response(404, {})

    http = httpx.AsyncClient(
        base_url="https://api.github.test", transport=httpx.MockTransport(route)
    )
    client = GitHubForgeClient(installation_token, client=http)
    with pytest.raises(ForgeConflictError, match="expected_base_commit_changed"):
        await client.enable_protected_auto_merge(
            ForgeMutation(binding=mutation_binding(), idempotency_key="b" * 64),
            expected_head_commit="d" * 40,
        )
    assert "/graphql" not in paths
    await http.aclose()


@pytest.mark.asyncio
async def test_branch_lookup_timeout_is_retryable() -> None:
    def route(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/pulls"):
            return response(200, [])
        raise httpx.ReadTimeout("timed out", request=request)

    http = httpx.AsyncClient(
        base_url="https://api.github.test", transport=httpx.MockTransport(route)
    )
    client = GitHubForgeClient(installation_token, client=http)
    with pytest.raises(ForgeRetryableError, match="github_unavailable"):
        await client.ensure_protected_pull_request(
            ForgeMutation(binding=mutation_binding(), idempotency_key="b" * 64)
        )
    await http.aclose()


@pytest.mark.asyncio
async def test_remote_protocol_disconnect_is_retryable() -> None:
    def route(request: httpx.Request) -> httpx.Response:
        raise httpx.RemoteProtocolError("peer disconnected", request=request)

    http = httpx.AsyncClient(
        base_url="https://api.github.test", transport=httpx.MockTransport(route)
    )
    client = GitHubForgeClient(installation_token, client=http)
    with pytest.raises(ForgeRetryableError, match="github_unavailable"):
        await client.observe(
            ForgeMutation(binding=mutation_binding(), idempotency_key="b" * 64)
        )
    await http.aclose()


@pytest.mark.asyncio
async def test_installation_token_provider_caches_then_refreshes_before_expiry() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    current = [NOW]
    issued = 0

    def route(request: httpx.Request) -> httpx.Response:
        nonlocal issued
        issued += 1
        assert request.headers["authorization"].startswith("Bearer eyJ")
        assert json.loads(request.content) == {"repository_ids": [789]}
        return response(
            201,
            {
                "token": f"token-{issued}",
                "expires_at": (current[0] + timedelta(hours=1)).isoformat(),
            },
        )

    http = httpx.AsyncClient(
        base_url="https://api.github.test", transport=httpx.MockTransport(route)
    )
    provider = GitHubAppInstallationTokenProvider(
        app_id=123,
        installation_id=456,
        repository_id=789,
        private_key_pem=pem,
        client=http,
        clock=lambda: current[0],
    )

    assert await provider() == "token-1"
    assert await provider() == "token-1"
    current[0] += timedelta(minutes=56)
    assert await provider() == "token-2"
    assert issued == 2
    await http.aclose()


@pytest.mark.asyncio
async def test_checks_only_attester_binds_decision_digest_and_head() -> None:
    captured: dict[str, object] = {}

    def route(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer installation-token"
        return response(201, {"id": 99})

    http = httpx.AsyncClient(
        base_url="https://api.github.test", transport=httpx.MockTransport(route)
    )
    attester = GitHubGovernanceAttester(
        installation_token,
        client=http,
        clock=lambda: NOW,
    )
    binding = mutation_binding()
    await attester.attest(
        ForgeMutation(binding=binding, idempotency_key="b" * 64),
        head_commit="d" * 40,
    )

    assert captured["name"] == "OpenNosh governance attestation"
    assert captured["head_sha"] == "d" * 40
    assert captured["conclusion"] == "success"
    assert binding.approved_changes.digest in captured["output"]["summary"]  # type: ignore[index]
    await http.aclose()
