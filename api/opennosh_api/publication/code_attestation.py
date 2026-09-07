from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any, Literal, Protocol

import httpx

from opennosh_api.governance.contracts import (
    CANONICAL_FORGE_TARGET,
    GOVERNED_BRANCH_PREFIX,
    GOVERNED_PATH_PREFIX,
)
from opennosh_api.publication.forge.contracts import (
    ForgeRetryableError,
    ForgeTerminalError,
)
from opennosh_api.publication.forge.github import InstallationTokenProvider

logger = logging.getLogger(__name__)

ATTESTATION_CHECK = "OpenNosh governance attestation"
MAX_OPEN_PULL_REQUESTS = 100
MAX_FILES_PER_PULL_REQUEST = 999
MAX_RECENT_MAIN_COMMITS = 10
ATTESTATION_OUTAGE_FAILURE_THRESHOLD = 3


@dataclass(frozen=True, slots=True)
class CodeAttestationAvailabilityAlert:
    state: Literal["outage", "recovered"]
    error_code: str
    failed_attempts: int
    occurred_at: datetime


class CodeAttestationAlertDestination(Protocol):
    async def send(self, alert: CodeAttestationAvailabilityAlert) -> None: ...


class WebhookCodeAttestationAlertDestination:
    """Deliver a redacted operational signal to an operator-owned HTTPS endpoint."""

    def __init__(
        self,
        endpoint: str,
        *,
        bearer_token: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._bearer_token = bearer_token
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=5.0)

    async def send(self, alert: CodeAttestationAvailabilityAlert) -> None:
        headers = {"User-Agent": "OpenNosh publication alert/1"}
        if self._bearer_token:
            headers["Authorization"] = f"Bearer {self._bearer_token}"
        response = await self._client.post(
            self._endpoint,
            headers=headers,
            json={
                "schema": "opennosh.governance-code-attestation.availability.v1",
                "component": "governance-code-attestation",
                "state": alert.state,
                "error_code": alert.error_code,
                "failed_attempts": alert.failed_attempts,
                "occurred_at": alert.occurred_at.isoformat(),
            },
        )
        response.raise_for_status()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


@dataclass(frozen=True, slots=True)
class CodeAttestationReport:
    candidates: int
    governed_skipped: int
    unchanged: int
    passed: int
    blocked: int
    merge_candidates: int
    merge_unchanged: int
    merge_propagated: int
    merge_untrusted: int


@dataclass(frozen=True, slots=True)
class _Candidate:
    number: int
    head_commit: str
    head_branch: str


class GitHubCodeAttestationService:
    """Classify non-governed PR heads and emit the source-pinned protected check."""

    def __init__(
        self,
        read_token_provider: InstallationTokenProvider,
        write_token_provider: InstallationTokenProvider,
        *,
        attester_app_id: int,
        repository: str = CANONICAL_FORGE_TARGET.removeprefix("github:"),
        base_branch: str = "main",
        managed_path_prefix: str = GOVERNED_PATH_PREFIX,
        propagate_main_merges: bool = True,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        owner, separator, name = repository.partition("/")
        if not separator or not owner or not name or "/" in name:
            raise ValueError("GitHub repository must be owner/name")
        if attester_app_id <= 0:
            raise ValueError("Attester App ID must be positive")
        if not base_branch or any(character.isspace() for character in base_branch):
            raise ValueError("GitHub base branch is invalid")
        if (
            not managed_path_prefix.endswith("/")
            or managed_path_prefix.startswith("/")
            or ".." in managed_path_prefix.split("/")
        ):
            raise ValueError("Managed path prefix must be a relative directory")
        self._read_token_provider = read_token_provider
        self._write_token_provider = write_token_provider
        self._attester_app_id = attester_app_id
        self._owner = owner
        self._repository = name
        self._base_branch = base_branch
        self._managed_path_prefix = managed_path_prefix
        self._propagate_main_merges = propagate_main_merges
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url="https://api.github.com",
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "opennosh-governance-attester/1",
            },
            timeout=20,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def reconcile_once(self) -> CodeAttestationReport:
        candidates = await self._open_pull_requests()
        governed_skipped = unchanged = passed = blocked = 0
        for candidate in candidates:
            if candidate.head_branch.startswith(GOVERNED_BRANCH_PREFIX):
                governed_skipped += 1
                continue
            paths = await self._stable_changed_paths(candidate)
            if paths is None:
                continue
            managed = tuple(path for path in paths if path.startswith(self._managed_path_prefix))
            conclusion = "action_required" if managed else "success"
            if await self._has_conclusion(candidate.head_commit, conclusion):
                unchanged += 1
                continue
            await self._emit(candidate, conclusion=conclusion, managed_paths=managed)
            if managed:
                blocked += 1
            else:
                passed += 1
        merge_candidates = merge_unchanged = merge_propagated = merge_untrusted = 0
        if self._propagate_main_merges:
            (
                merge_candidates,
                merge_unchanged,
                merge_propagated,
                merge_untrusted,
            ) = await self._propagate_recent_main_merges()
        return CodeAttestationReport(
            candidates=len(candidates),
            governed_skipped=governed_skipped,
            unchanged=unchanged,
            passed=passed,
            blocked=blocked,
            merge_candidates=merge_candidates,
            merge_unchanged=merge_unchanged,
            merge_propagated=merge_propagated,
            merge_untrusted=merge_untrusted,
        )

    async def _propagate_recent_main_merges(self) -> tuple[int, int, int, int]:
        commits = await self._recent_main_commits()
        unchanged = propagated = untrusted = 0
        for commit in commits:
            if await self._has_conclusion(commit, "success"):
                unchanged += 1
                continue
            pull_request = await self._trusted_merge_pull_request(commit)
            if pull_request is None:
                untrusted += 1
                continue
            number, head_commit = pull_request
            if not await self._has_conclusion(head_commit, "success"):
                untrusted += 1
                continue
            await self._emit_propagated_merge(
                merge_commit=commit,
                pull_number=number,
                head_commit=head_commit,
            )
            propagated += 1
        return len(commits), unchanged, propagated, untrusted

    async def _recent_main_commits(self) -> tuple[str, ...]:
        value = await self._read(
            f"/repos/{self._owner}/{self._repository}/commits",
            params={"sha": self._base_branch, "per_page": str(MAX_RECENT_MAIN_COMMITS)},
        )
        if not isinstance(value, list):
            raise ForgeTerminalError("github_main_commits_invalid")
        commits: list[str] = []
        for item in value:
            commit = item.get("sha") if isinstance(item, dict) else None
            if not isinstance(commit, str) or not self._valid_hash(commit):
                raise ForgeTerminalError("github_main_commit_invalid")
            commits.append(commit)
        return tuple(commits)

    async def _trusted_merge_pull_request(self, commit: str) -> tuple[int, str] | None:
        value = await self._read(
            f"/repos/{self._owner}/{self._repository}/commits/{commit}/pulls",
            params={"per_page": "10"},
        )
        if not isinstance(value, list) or len(value) >= 10:
            raise ForgeTerminalError("github_commit_pull_requests_invalid")
        matches: list[tuple[int, str]] = []
        for item in value:
            if not isinstance(item, dict):
                raise ForgeTerminalError("github_commit_pull_request_invalid")
            number = item.get("number")
            head = item.get("head")
            base = item.get("base")
            head_commit = head.get("sha") if isinstance(head, dict) else None
            base_branch = base.get("ref") if isinstance(base, dict) else None
            if (
                item.get("state") == "closed"
                and isinstance(item.get("merged_at"), str)
                and item.get("merge_commit_sha") == commit
                and base_branch == self._base_branch
            ):
                if not isinstance(number, int) or number <= 0:
                    raise ForgeTerminalError("github_commit_pull_request_number_invalid")
                if not isinstance(head_commit, str) or not self._valid_hash(head_commit):
                    raise ForgeTerminalError("github_commit_pull_request_head_invalid")
                matches.append((number, head_commit))
        if not matches:
            return None
        if len(matches) != 1:
            raise ForgeTerminalError("github_commit_pull_requests_ambiguous")
        return matches[0]

    async def _open_pull_requests(self) -> tuple[_Candidate, ...]:
        value = await self._read(
            f"/repos/{self._owner}/{self._repository}/pulls",
            params={
                "state": "open",
                "base": self._base_branch,
                "per_page": str(MAX_OPEN_PULL_REQUESTS),
            },
        )
        if not isinstance(value, list):
            raise ForgeTerminalError("github_pull_requests_invalid")
        if len(value) >= MAX_OPEN_PULL_REQUESTS:
            raise ForgeTerminalError("github_pull_requests_unbounded")
        return tuple(self._candidate(item) for item in value)

    def _candidate(self, value: object) -> _Candidate:
        if not isinstance(value, dict):
            raise ForgeTerminalError("github_pull_request_invalid")
        number = value.get("number")
        head = value.get("head")
        base = value.get("base")
        head_commit = head.get("sha") if isinstance(head, dict) else None
        head_branch = head.get("ref") if isinstance(head, dict) else None
        base_branch = base.get("ref") if isinstance(base, dict) else None
        if value.get("state") != "open":
            raise ForgeTerminalError("github_pull_request_state_invalid")
        if not isinstance(number, int) or number <= 0:
            raise ForgeTerminalError("github_pull_request_number_missing")
        if not isinstance(head_commit, str) or not self._valid_hash(head_commit):
            raise ForgeTerminalError("github_pull_request_head_invalid")
        if not isinstance(head_branch, str) or not head_branch or "\x00" in head_branch:
            raise ForgeTerminalError("github_pull_request_branch_invalid")
        if base_branch != self._base_branch:
            raise ForgeTerminalError("github_pull_request_base_invalid")
        return _Candidate(number, head_commit, head_branch)

    async def _stable_changed_paths(self, candidate: _Candidate) -> tuple[str, ...] | None:
        paths: list[str] = []
        for page in range(1, 11):
            value = await self._read(
                f"/repos/{self._owner}/{self._repository}/pulls/{candidate.number}/files",
                params={"per_page": "100", "page": str(page)},
            )
            if not isinstance(value, list):
                raise ForgeTerminalError("github_pull_request_files_invalid")
            for item in value:
                for path in self._changed_paths(item):
                    paths.append(path)
                    if len(paths) > MAX_FILES_PER_PULL_REQUEST:
                        raise ForgeTerminalError("github_pull_request_files_unbounded")
            if len(value) < 100:
                break
        else:
            raise ForgeTerminalError("github_pull_request_files_unbounded")
        current = await self._read(
            f"/repos/{self._owner}/{self._repository}/pulls/{candidate.number}"
        )
        if not isinstance(current, dict) or current.get("state") != "open":
            return None
        if self._candidate(current) != candidate:
            return None
        return tuple(paths)

    @classmethod
    def _changed_paths(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, dict):
            raise ForgeTerminalError("github_pull_request_file_invalid")
        paths = [value.get("filename")]
        if "previous_filename" in value:
            paths.append(value.get("previous_filename"))
        if any(not cls._valid_path(path) for path in paths):
            raise ForgeTerminalError("github_pull_request_file_invalid")
        return tuple(path for path in paths if isinstance(path, str))

    @staticmethod
    def _valid_path(value: object) -> bool:
        if not isinstance(value, str):
            return False
        path = PurePosixPath(value)
        return bool(
            value
            and not value.startswith("/")
            and "\\" not in value
            and ".." not in path.parts
            and path.as_posix() == value
            and "\x00" not in value
            and len(value) <= 4_096
        )

    async def _has_conclusion(self, head_commit: str, conclusion: str) -> bool:
        value = await self._read(
            f"/repos/{self._owner}/{self._repository}/commits/{head_commit}/check-runs",
            params={"check_name": ATTESTATION_CHECK, "per_page": "100"},
        )
        runs = value.get("check_runs") if isinstance(value, dict) else None
        if not isinstance(runs, list):
            raise ForgeTerminalError("github_check_runs_invalid")
        return any(
            isinstance(run, dict)
            and run.get("name") == ATTESTATION_CHECK
            and run.get("head_sha") == head_commit
            and run.get("status") == "completed"
            and run.get("conclusion") == conclusion
            and isinstance(run.get("app"), dict)
            and run["app"].get("id") == self._attester_app_id
            for run in runs
        )

    async def _emit(
        self,
        candidate: _Candidate,
        *,
        conclusion: str,
        managed_paths: tuple[str, ...],
    ) -> None:
        if managed_paths:
            title = "Governed data change requires steward authorization"
            summary = (
                "This ordinary pull request changes managed packs/ data. "
                "Submit the change through the governed contribution workflow."
            )
        else:
            title = "Non-governed code change verified"
            summary = (
                "The exact pull-request head changes no managed packs/ data; "
                "database-backed contribution authorization is not applicable."
            )
        await self._write(
            f"/repos/{self._owner}/{self._repository}/check-runs",
            {
                "name": ATTESTATION_CHECK,
                "head_sha": candidate.head_commit,
                "status": "completed",
                "conclusion": conclusion,
                "output": {"title": title, "summary": summary},
            },
        )

    async def _emit_propagated_merge(
        self,
        *,
        merge_commit: str,
        pull_number: int,
        head_commit: str,
    ) -> None:
        await self._write(
            f"/repos/{self._owner}/{self._repository}/check-runs",
            {
                "name": ATTESTATION_CHECK,
                "head_sha": merge_commit,
                "status": "completed",
                "conclusion": "success",
                "output": {
                    "title": "Protected merge attestation propagated",
                    "summary": (
                        f"Pull request #{pull_number} merged from attested head "
                        f"{head_commit}; this exact main commit is deployable."
                    ),
                },
            },
        )

    async def _read(self, path: str, *, params: Mapping[str, str] | None = None) -> Any:
        token = await self._token(self._read_token_provider)
        try:
            response = await self._client.get(
                path, params=params, headers={"Authorization": f"Bearer {token}"}
            )
        except httpx.TransportError as error:
            raise ForgeRetryableError("github_code_attestation_unavailable") from error
        return self._decode(response, expected=200)

    async def _write(self, path: str, payload: object) -> None:
        token = await self._token(self._write_token_provider)
        try:
            response = await self._client.post(
                path,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.TransportError as error:
            raise ForgeRetryableError("github_code_attestation_unavailable") from error
        self._decode(response, expected=201)

    @staticmethod
    async def _token(provider: InstallationTokenProvider) -> str:
        token = await provider()
        if not token or any(character.isspace() for character in token):
            raise ForgeTerminalError("github_code_attestation_token_invalid")
        return token

    @staticmethod
    def _decode(response: httpx.Response, *, expected: int) -> Any:
        if response.status_code in {408, 429, 500, 502, 503, 504}:
            raise ForgeRetryableError("github_code_attestation_unavailable")
        if response.status_code in {401, 403, 404}:
            raise ForgeTerminalError("github_code_attestation_not_authorized")
        if response.status_code != expected:
            raise ForgeTerminalError(f"github_code_attestation_http_{response.status_code}")
        try:
            return response.json()
        except ValueError as error:
            raise ForgeTerminalError("github_code_attestation_response_invalid") from error

    @staticmethod
    def _valid_hash(value: object) -> bool:
        return (
            isinstance(value, str)
            and len(value) in {40, 64}
            and all(character in "0123456789abcdef" for character in value)
        )


async def run_code_attestation_loop(
    service: GitHubCodeAttestationService,
    shutdown: asyncio.Event,
    *,
    interval_seconds: float,
    outage_failure_threshold: int = ATTESTATION_OUTAGE_FAILURE_THRESHOLD,
    alert_destination: CodeAttestationAlertDestination | None = None,
) -> None:
    if interval_seconds <= 0:
        raise ValueError("Governance code attestation interval must be positive")
    if outage_failure_threshold <= 0:
        raise ValueError("Governance code attestation outage threshold must be positive")
    consecutive_failures = 0
    last_error_code: str | None = None
    while not shutdown.is_set():
        try:
            report = await service.reconcile_once()
        except ForgeRetryableError as error:
            consecutive_failures += 1
            last_error_code = error.code
            if consecutive_failures % outage_failure_threshold == 0:
                logger.error(
                    "Governance code attestation availability state=outage error=%s "
                    "consecutive_failures=%d alert_every_failures=%d",
                    error.code,
                    consecutive_failures,
                    outage_failure_threshold,
                )
                await _deliver_availability_alert(
                    alert_destination,
                    CodeAttestationAvailabilityAlert(
                        state="outage",
                        error_code=error.code,
                        failed_attempts=consecutive_failures,
                        occurred_at=datetime.now(UTC),
                    ),
                )
            else:
                logger.warning(
                    "Governance code attestation availability state=retrying error=%s "
                    "consecutive_failures=%d alert_after_failures=%d",
                    error.code,
                    consecutive_failures,
                    outage_failure_threshold,
                )
        else:
            if consecutive_failures:
                outage_alerted = consecutive_failures >= outage_failure_threshold
                logger.warning(
                    "Governance code attestation availability state=recovered "
                    "previous_error=%s failed_attempts=%d outage_alerted=%s",
                    last_error_code,
                    consecutive_failures,
                    str(outage_alerted).lower(),
                )
                if outage_alerted:
                    await _deliver_availability_alert(
                        alert_destination,
                        CodeAttestationAvailabilityAlert(
                            state="recovered",
                            error_code=last_error_code or "unknown",
                            failed_attempts=consecutive_failures,
                            occurred_at=datetime.now(UTC),
                        ),
                    )
                consecutive_failures = 0
                last_error_code = None
            if report.passed or report.blocked or report.merge_propagated:
                logger.warning(
                    "Governance code attestation reconciled candidates=%d passed=%d "
                    "blocked=%d governed_skipped=%d unchanged=%d "
                    "merge_candidates=%d merge_propagated=%d merge_untrusted=%d",
                    report.candidates,
                    report.passed,
                    report.blocked,
                    report.governed_skipped,
                    report.unchanged,
                    report.merge_candidates,
                    report.merge_propagated,
                    report.merge_untrusted,
                )
        try:
            await asyncio.wait_for(shutdown.wait(), timeout=interval_seconds)
        except TimeoutError:
            pass


async def _deliver_availability_alert(
    destination: CodeAttestationAlertDestination | None,
    alert: CodeAttestationAvailabilityAlert,
) -> None:
    if destination is None:
        return
    try:
        await destination.send(alert)
    except Exception as error:
        logger.error(
            "Governance code attestation external alert delivery failed "
            "state=%s error_type=%s",
            alert.state,
            type(error).__name__,
        )
