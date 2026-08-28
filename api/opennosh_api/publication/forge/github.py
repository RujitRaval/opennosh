from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import quote

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from opennosh_api.governance.contracts import (
    CANONICAL_FORGE_TARGET,
    PROTECTED_STATUS_CHECKS,
    ApprovedChangeSet,
    ApprovedFileChange,
)
from opennosh_api.publication.forge.contracts import (
    ForgeCheckState,
    ForgeConflictError,
    ForgeMutation,
    ForgeObservation,
    ForgePullRequestState,
    ForgeRetryableError,
    ForgeTerminalError,
)


class InstallationTokenProvider(Protocol):
    async def __call__(self) -> str: ...


class GitHubAppInstallationTokenProvider:
    """Mint and cache short-lived GitHub App installation tokens safely."""

    def __init__(
        self,
        *,
        app_id: int,
        installation_id: int,
        repository_id: int,
        private_key_pem: str,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if app_id <= 0 or installation_id <= 0 or repository_id <= 0:
            raise ValueError("GitHub App, installation, and repository IDs must be positive")
        try:
            key = serialization.load_pem_private_key(
                private_key_pem.encode("utf-8"), password=None
            )
        except (TypeError, ValueError) as error:
            raise ValueError("GitHub App private key is invalid") from error
        if not isinstance(key, rsa.RSAPrivateKey):
            raise ValueError("GitHub App private key must be RSA")
        self._app_id = app_id
        self._installation_id = installation_id
        self._repository_id = repository_id
        self._key = key
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url="https://api.github.com",
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": GitHubForgeClient.version,
                "User-Agent": "opennosh-governed-forge/1",
            },
            timeout=20,
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        self._cached_token: str | None = None
        self._expires_at: datetime | None = None
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __call__(self) -> str:
        now = self._now()
        if self._token_is_fresh(now):
            assert self._cached_token is not None
            return self._cached_token
        async with self._lock:
            now = self._now()
            if self._token_is_fresh(now):
                assert self._cached_token is not None
                return self._cached_token
            jwt = self._app_jwt(now)
            try:
                response = await self._client.post(
                    f"/app/installations/{self._installation_id}/access_tokens",
                    headers={"Authorization": f"Bearer {jwt}"},
                    json={"repository_ids": [self._repository_id]},
                )
            except httpx.TransportError as error:
                raise ForgeRetryableError("github_token_unavailable") from error
            if response.status_code in {408, 429, 500, 502, 503, 504}:
                raise ForgeRetryableError("github_token_unavailable")
            if response.status_code in {401, 403, 404}:
                raise ForgeTerminalError("github_app_not_authorized")
            if response.status_code != 201:
                raise ForgeTerminalError(f"github_token_http_{response.status_code}")
            try:
                payload = response.json()
            except ValueError as error:
                raise ForgeTerminalError("github_token_response_invalid") from error
            token = payload.get("token") if isinstance(payload, dict) else None
            expires_raw = payload.get("expires_at") if isinstance(payload, dict) else None
            if not isinstance(token, str) or not token or not isinstance(expires_raw, str):
                raise ForgeTerminalError("github_token_response_invalid")
            try:
                expires_at = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
            except ValueError as error:
                raise ForgeTerminalError("github_token_response_invalid") from error
            if expires_at <= now + timedelta(minutes=5):
                raise ForgeTerminalError("github_token_expiry_invalid")
            self._cached_token = token
            self._expires_at = expires_at
            return token

    def _token_is_fresh(self, now: datetime) -> bool:
        return (
            self._cached_token is not None
            and self._expires_at is not None
            and now < self._expires_at - timedelta(minutes=5)
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("GitHub token clock must include a timezone")
        return value

    def _app_jwt(self, now: datetime) -> str:
        header = _base64url_json({"alg": "RS256", "typ": "JWT"})
        claims = _base64url_json(
            {
                "iat": int((now - timedelta(seconds=60)).timestamp()),
                "exp": int((now + timedelta(minutes=9)).timestamp()),
                "iss": str(self._app_id),
            }
        )
        material = f"{header}.{claims}".encode("ascii")
        signature = self._key.sign(material, padding.PKCS1v15(), hashes.SHA256())
        return f"{header}.{claims}.{_base64url(signature)}"


class GitHubForgeClient:
    """Least-privilege GitHub App client for one protected squash-merge path."""

    identity = "github-app"
    version = "2022-11-28"

    def __init__(
        self,
        installation_token_provider: InstallationTokenProvider,
        *,
        base_branch: str = "main",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not callable(installation_token_provider):
            raise ValueError("GitHub installation token provider must be callable")
        if not base_branch or any(character.isspace() for character in base_branch):
            raise ValueError("GitHub base branch is invalid")
        self._base_branch = base_branch
        self._installation_token_provider = installation_token_provider
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url="https://api.github.com",
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": self.version,
                "User-Agent": "opennosh-governed-forge/1",
            },
            timeout=20,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def ensure_protected_pull_request(self, mutation: ForgeMutation) -> None:
        owner, repository = _repository(mutation.binding.forge_target)
        branch = _branch_name(mutation.idempotency_key)
        pull = await self._find_pull(owner, repository, branch)
        if pull is None:
            branch_sha = await self._branch_sha(owner, repository, branch)
            if branch_sha is None:
                base_sha = await self._branch_sha(owner, repository, self._base_branch)
                if base_sha != mutation.binding.expected_base_commit:
                    raise ForgeConflictError("expected_base_commit_changed")
                branch_sha = await self._create_commit(
                    owner,
                    repository,
                    mutation,
                    base_sha,
                )
                await self._request(
                    "POST",
                    f"/repos/{owner}/{repository}/git/refs",
                    json={"ref": f"refs/heads/{branch}", "sha": branch_sha},
                    expected={201},
                )
            else:
                recovered = await self._digest_for_ref(
                    owner,
                    repository,
                    branch,
                    mutation.binding.approved_changes,
                    compare_base=mutation.binding.expected_base_commit,
                )
                if recovered != mutation.binding.approved_changes.digest:
                    raise ForgeConflictError("idempotency_branch_payload_mismatch")
            pull = await self._create_pull(owner, repository, branch, mutation)
        if bool(pull.get("merged")):
            return

    async def enable_protected_auto_merge(
        self, mutation: ForgeMutation, *, expected_head_commit: str
    ) -> None:
        if len(expected_head_commit) not in {40, 64} or any(
            character not in "0123456789abcdef" for character in expected_head_commit
        ):
            raise ValueError("Expected pull-request head must be a lowercase Git hash")
        owner, repository = _repository(mutation.binding.forge_target)
        branch = _branch_name(mutation.idempotency_key)
        pull = await self._find_pull(owner, repository, branch)
        if pull is None:
            raise ForgeConflictError("protected_pull_request_missing")
        if bool(pull.get("merged")):
            return
        if pull.get("state") == "closed":
            raise ForgeConflictError("protected_pull_request_closed")
        head = pull.get("head")
        actual_head = head.get("sha") if isinstance(head, dict) else None
        if actual_head != expected_head_commit:
            raise ForgeConflictError("pull_request_head_changed")
        base_sha = await self._branch_sha(owner, repository, self._base_branch)
        if base_sha != mutation.binding.expected_base_commit:
            raise ForgeConflictError("expected_base_commit_changed")
        node_id = pull.get("node_id")
        if not isinstance(node_id, str):
            raise ForgeTerminalError("github_pull_request_node_missing")
        if await self._auto_merge_enabled(node_id):
            return
        payload = await self._request(
            "POST",
            "/graphql",
            json={
                "query": (
                    "mutation($id:ID!){enablePullRequestAutoMerge(input:{"
                    "pullRequestId:$id,mergeMethod:SQUASH}){pullRequest{id "
                    "autoMergeRequest{mergeMethod}}}}"
                ),
                "variables": {"id": node_id},
            },
            expected={200},
        )
        if not isinstance(payload, dict) or payload.get("errors"):
            raise ForgeConflictError("github_auto_merge_rejected")
        try:
            request = payload["data"]["enablePullRequestAutoMerge"]["pullRequest"][
                "autoMergeRequest"
            ]
        except (KeyError, TypeError) as error:
            raise ForgeTerminalError("github_auto_merge_response_invalid") from error
        if not isinstance(request, dict) or request.get("mergeMethod") != "SQUASH":
            raise ForgeConflictError("github_auto_merge_rejected")

    async def observe(self, mutation: ForgeMutation) -> ForgeObservation:
        owner, repository = _repository(mutation.binding.forge_target)
        branch = _branch_name(mutation.idempotency_key)
        pull = await self._find_pull(owner, repository, branch)
        if pull is None:
            return ForgeObservation(state=ForgePullRequestState.ABSENT)
        head = pull.get("head")
        head_sha = head.get("sha") if isinstance(head, dict) else None
        if not isinstance(head_sha, str):
            raise ForgeTerminalError("github_pull_request_head_missing")
        checks = await self._checks(owner, repository, head_sha)
        external_reference = pull.get("html_url")
        reference = external_reference if isinstance(external_reference, str) else None
        if bool(pull.get("merged")):
            merged_at_raw = pull.get("merged_at")
            merged_commit = pull.get("merge_commit_sha")
            if not isinstance(merged_at_raw, str) or not isinstance(merged_commit, str):
                raise ForgeTerminalError("github_merge_proof_missing")
            digest = await self._digest_for_ref(
                owner,
                repository,
                merged_commit,
                mutation.binding.approved_changes,
                pull_number=_pull_number(pull),
            )
            return ForgeObservation(
                state=ForgePullRequestState.MERGED,
                checks=checks,
                external_reference=reference,
                head_commit=head_sha,
                merged_at=datetime.fromisoformat(merged_at_raw.replace("Z", "+00:00")),
                merged_commit=merged_commit,
                merged_payload_digest=digest,
            )
        if pull.get("state") == "closed":
            return ForgeObservation(
                state=ForgePullRequestState.CLOSED,
                checks=checks,
                external_reference=reference,
            )
        head_payload_digest = await self._digest_for_ref(
            owner,
            repository,
            head_sha,
            mutation.binding.approved_changes,
            compare_base=mutation.binding.expected_base_commit,
        )
        node_id = pull.get("node_id")
        if not isinstance(node_id, str):
            raise ForgeTerminalError("github_pull_request_node_missing")
        return ForgeObservation(
            state=ForgePullRequestState.OPEN,
            checks=checks,
            external_reference=reference,
            head_commit=head_sha,
            head_payload_digest=head_payload_digest,
            auto_merge_enabled=await self._auto_merge_enabled(node_id),
        )

    async def _auto_merge_enabled(self, pull_request_node_id: str) -> bool:
        payload = await self._request(
            "POST",
            "/graphql",
            json={
                "query": (
                    "query($id:ID!){node(id:$id){... on PullRequest{"
                    "autoMergeRequest{mergeMethod}}}}"
                ),
                "variables": {"id": pull_request_node_id},
            },
            expected={200},
        )
        if not isinstance(payload, dict) or payload.get("errors"):
            raise ForgeRetryableError("github_auto_merge_state_unavailable")
        try:
            request = payload["data"]["node"]["autoMergeRequest"]
        except (KeyError, TypeError) as error:
            raise ForgeTerminalError("github_auto_merge_state_invalid") from error
        if request is None:
            return False
        if not isinstance(request, dict) or request.get("mergeMethod") != "SQUASH":
            raise ForgeConflictError("github_auto_merge_method_mismatch")
        return True

    async def _create_commit(
        self,
        owner: str,
        repository: str,
        mutation: ForgeMutation,
        base_sha: str,
    ) -> str:
        base_commit = await self._request(
            "GET", f"/repos/{owner}/{repository}/git/commits/{base_sha}", expected={200}
        )
        tree = base_commit.get("tree")
        base_tree = tree.get("sha") if isinstance(tree, dict) else None
        if not isinstance(base_tree, str):
            raise ForgeTerminalError("github_base_tree_missing")
        entries: list[dict[str, str]] = []
        for file in mutation.binding.approved_changes.files:
            blob = await self._request(
                "POST",
                f"/repos/{owner}/{repository}/git/blobs",
                json={"content": file.content, "encoding": "utf-8"},
                expected={201},
            )
            blob_sha = blob.get("sha")
            if not isinstance(blob_sha, str):
                raise ForgeTerminalError("github_blob_sha_missing")
            entries.append({"path": file.path, "mode": "100644", "type": "blob", "sha": blob_sha})
        created_tree = await self._request(
            "POST",
            f"/repos/{owner}/{repository}/git/trees",
            json={"base_tree": base_tree, "tree": entries},
            expected={201},
        )
        tree_sha = created_tree.get("sha")
        if not isinstance(tree_sha, str):
            raise ForgeTerminalError("github_created_tree_missing")
        commit = await self._request(
            "POST",
            f"/repos/{owner}/{repository}/git/commits",
            json={
                "message": f"Publish governed contribution {mutation.binding.decision_id}",
                "tree": tree_sha,
                "parents": [base_sha],
            },
            expected={201},
        )
        commit_sha = commit.get("sha")
        if not isinstance(commit_sha, str):
            raise ForgeTerminalError("github_created_commit_missing")
        return commit_sha

    async def _create_pull(
        self,
        owner: str,
        repository: str,
        branch: str,
        mutation: ForgeMutation,
    ) -> Mapping[str, Any]:
        payload = await self._request(
            "POST",
            f"/repos/{owner}/{repository}/pulls",
            json={
                "title": f"Governed contribution: {mutation.binding.pack_id}",
                "head": branch,
                "base": self._base_branch,
                "body": (
                    "Automated from steward decision "
                    f"`{mutation.binding.decision_id}`.\n\n"
                    f"Approved payload: `{mutation.binding.approved_changes.digest}`"
                ),
                "maintainer_can_modify": False,
            },
            expected={201},
        )
        if not isinstance(payload, dict):
            raise ForgeTerminalError("github_pull_request_invalid")
        return payload

    async def _find_pull(
        self, owner: str, repository: str, branch: str
    ) -> Mapping[str, Any] | None:
        pulls = await self._request(
            "GET",
            f"/repos/{owner}/{repository}/pulls",
            params={"state": "all", "head": f"{owner}:{branch}", "base": self._base_branch},
            expected={200},
        )
        if not isinstance(pulls, list):
            raise ForgeTerminalError("github_pull_list_invalid")
        if not pulls:
            return None
        number = _pull_number(pulls[0])
        detail = await self._request(
            "GET",
            f"/repos/{owner}/{repository}/pulls/{number}",
            expected={200},
        )
        if not isinstance(detail, dict):
            raise ForgeTerminalError("github_pull_request_invalid")
        return detail

    async def _branch_sha(self, owner: str, repository: str, branch: str) -> str | None:
        try:
            headers = await self._authorization_headers()
            response = await self._client.get(
                f"/repos/{owner}/{repository}/git/ref/heads/{quote(branch, safe='/')}",
                headers=headers,
            )
        except httpx.TransportError as error:
            raise ForgeRetryableError("github_unavailable") from error
        if response.status_code == 404:
            return None
        payload = self._decode(response, expected={200})
        target = payload.get("object") if isinstance(payload, dict) else None
        sha = target.get("sha") if isinstance(target, dict) else None
        if not isinstance(sha, str):
            raise ForgeTerminalError("github_branch_sha_missing")
        return sha

    async def _checks(
        self, owner: str, repository: str, commit_sha: str
    ) -> tuple[tuple[str, ForgeCheckState], ...]:
        payload = await self._request(
            "GET",
            f"/repos/{owner}/{repository}/commits/{commit_sha}/check-runs",
            params={"per_page": "100"},
            expected={200},
        )
        raw_runs = payload.get("check_runs")
        if not isinstance(raw_runs, list):
            raise ForgeTerminalError("github_check_runs_invalid")
        results: dict[str, ForgeCheckState] = {}
        for raw in raw_runs:
            if not isinstance(raw, dict) or not isinstance(raw.get("name"), str):
                continue
            state = ForgeCheckState.PENDING
            if raw.get("status") == "completed":
                state = (
                    ForgeCheckState.PASSED
                    if raw.get("conclusion") in {"success", "neutral", "skipped"}
                    else ForgeCheckState.FAILED
                )
            name = str(raw["name"])
            previous = results.get(name)
            severity = {
                ForgeCheckState.PASSED: 0,
                ForgeCheckState.PENDING: 1,
                ForgeCheckState.FAILED: 2,
            }
            if previous is None or severity[state] > severity[previous]:
                results[name] = state
        return tuple(sorted(results.items()))

    async def _digest_for_ref(
        self,
        owner: str,
        repository: str,
        ref: str,
        approved: ApprovedChangeSet,
        *,
        compare_base: str | None = None,
        pull_number: int | None = None,
    ) -> str | None:
        if (compare_base is None) == (pull_number is None):
            raise ValueError("Payload proof requires exactly one changed-path source")
        if compare_base is not None:
            source = await self._request(
                "GET",
                (
                    f"/repos/{owner}/{repository}/compare/"
                    f"{quote(compare_base, safe='')}...{quote(ref, safe='')}"
                ),
                expected={200},
            )
            changed = source.get("files") if isinstance(source, dict) else None
        else:
            changed = await self._request(
                "GET",
                f"/repos/{owner}/{repository}/pulls/{pull_number}/files",
                params={"per_page": "100"},
                expected={200},
            )
        if not isinstance(changed, list):
            raise ForgeTerminalError("github_changed_files_invalid")
        changed_paths = {
            item.get("filename")
            for item in changed
            if isinstance(item, dict) and isinstance(item.get("filename"), str)
        }
        approved_paths = {file.path for file in approved.files}
        if changed_paths != approved_paths:
            return None
        files: list[ApprovedFileChange] = []
        for expected in approved.files:
            payload = await self._request(
                "GET",
                f"/repos/{owner}/{repository}/contents/{quote(expected.path, safe='/')}",
                params={"ref": ref},
                expected={200},
            )
            content = payload.get("content")
            encoding = payload.get("encoding")
            if not isinstance(content, str) or encoding != "base64":
                raise ForgeTerminalError("github_merged_content_invalid")
            try:
                decoded = base64.b64decode(content).decode("utf-8")
            except (ValueError, UnicodeDecodeError) as error:
                raise ForgeTerminalError("github_merged_content_invalid") from error
            files.append(ApprovedFileChange(path=expected.path, content=decoded))
        return ApprovedChangeSet.build(pack_id=approved.pack_id, files=tuple(files)).digest

    async def _request(
        self,
        method: str,
        path: str,
        *,
        expected: set[int],
        json: object | None = None,
        params: Mapping[str, str] | None = None,
    ) -> Any:
        try:
            headers = await self._authorization_headers()
            response = await self._client.request(
                method, path, json=json, params=params, headers=headers
            )
        except httpx.TransportError as error:
            raise ForgeRetryableError("github_unavailable") from error
        return self._decode(response, expected=expected)

    async def _authorization_headers(self) -> dict[str, str]:
        token = await self._installation_token_provider()
        if not token or any(character.isspace() for character in token):
            raise ForgeTerminalError("github_installation_token_invalid")
        return {"Authorization": f"Bearer {token}"}

    @staticmethod
    def _decode(response: httpx.Response, *, expected: set[int]) -> Any:
        if response.status_code not in expected:
            if response.status_code in {408, 429, 500, 502, 503, 504}:
                raise ForgeRetryableError("github_unavailable")
            if response.status_code in {401, 403}:
                raise ForgeTerminalError("github_app_not_authorized")
            if response.status_code == 405:
                raise ForgeRetryableError("github_protected_merge_pending")
            if response.status_code in {409, 422}:
                raise ForgeConflictError("github_state_conflict")
            raise ForgeTerminalError(f"github_http_{response.status_code}")
        try:
            return response.json()
        except ValueError as error:
            raise ForgeTerminalError("github_response_invalid") from error


class GitHubGovernanceAttester:
    """Emit the protected governance check from a separate checks-only GitHub App."""

    check_name = "OpenNosh governance attestation"

    def __init__(
        self,
        installation_token_provider: InstallationTokenProvider,
        *,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if self.check_name not in PROTECTED_STATUS_CHECKS:
            raise RuntimeError("Governance attestation is not protected by policy")
        self._installation_token_provider = installation_token_provider
        self._clock = clock or (lambda: datetime.now(UTC))
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url="https://api.github.com",
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": GitHubForgeClient.version,
                "User-Agent": "opennosh-governance-attester/1",
            },
            timeout=20,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def attest(self, mutation: ForgeMutation, *, head_commit: str) -> None:
        if len(head_commit) not in {40, 64} or any(
            character not in "0123456789abcdef" for character in head_commit
        ):
            raise ValueError("Governance attestation head must be a lowercase Git hash")
        binding = mutation.binding
        owner, repository = _repository(binding.forge_target)
        binding.authorize_at(self._clock())
        token = await self._installation_token_provider()
        if not token or any(character.isspace() for character in token):
            raise ForgeTerminalError("github_attester_token_invalid")
        try:
            response = await self._client.post(
                f"/repos/{owner}/{repository}/check-runs",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "name": self.check_name,
                    "head_sha": head_commit,
                    "status": "completed",
                    "conclusion": "success",
                    "output": {
                        "title": "Governed contribution authorized",
                        "summary": (
                            f"Decision {binding.decision_id} authorizes payload "
                            f"{binding.approved_changes.digest}."
                        ),
                    },
                },
            )
        except httpx.TransportError as error:
            raise ForgeRetryableError("github_attester_unavailable") from error
        if response.status_code in {408, 429, 500, 502, 503, 504}:
            raise ForgeRetryableError("github_attester_unavailable")
        if response.status_code in {401, 403, 404}:
            raise ForgeTerminalError("github_attester_not_authorized")
        if response.status_code != 201:
            raise ForgeTerminalError(f"github_attester_http_{response.status_code}")


def _repository(forge_target: str) -> tuple[str, str]:
    if forge_target != CANONICAL_FORGE_TARGET:
        raise ForgeTerminalError("unsupported_forge_target")
    prefix = "github:"
    if not forge_target.startswith(prefix):
        raise ForgeTerminalError("unsupported_forge_target")
    parts = forge_target[len(prefix) :].split("/")
    if len(parts) != 2 or not all(parts):
        raise ForgeTerminalError("invalid_github_forge_target")
    if any(not part.replace("-", "").replace("_", "").replace(".", "").isalnum() for part in parts):
        raise ForgeTerminalError("invalid_github_forge_target")
    return parts[0], parts[1]


def _branch_name(idempotency_key: str) -> str:
    if len(idempotency_key) != 64 or any(
        character not in "0123456789abcdef" for character in idempotency_key
    ):
        raise ValueError("Forge idempotency key must be SHA-256")
    return f"opennosh/contribution/{idempotency_key[:24]}"


def _pull_number(value: object) -> int:
    if not isinstance(value, dict):
        raise ForgeTerminalError("github_pull_request_invalid")
    number = value.get("number")
    if not isinstance(number, int) or number <= 0:
        raise ForgeTerminalError("github_pull_request_number_missing")
    return number


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64url_json(value: dict[str, object]) -> str:
    return _base64url(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
