from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from opennosh_api.governance.policy import GovernanceBinding


class ForgePullRequestState(StrEnum):
    ABSENT = "absent"
    OPEN = "open"
    MERGED = "merged"
    CLOSED = "closed"


class ForgeCheckState(StrEnum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ForgeMutation:
    binding: GovernanceBinding
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ForgeObservation:
    state: ForgePullRequestState
    checks: tuple[tuple[str, ForgeCheckState], ...] = ()
    external_reference: str | None = None
    head_commit: str | None = None
    head_payload_digest: str | None = None
    auto_merge_enabled: bool = False
    merged_at: datetime | None = None
    merged_commit: str | None = None
    merged_payload_digest: str | None = None

    def __post_init__(self) -> None:
        names = tuple(name for name, _ in self.checks)
        if len(names) != len(set(names)):
            raise ValueError("Forge check names must be unique")
        if self.state is ForgePullRequestState.MERGED:
            if self.merged_at is None or self.merged_commit is None:
                raise ValueError("Merged forge observations require time and commit")
            if self.merged_at.tzinfo is None or self.merged_at.utcoffset() is None:
                raise ValueError("Forge merge time must include a timezone")
        if self.state is ForgePullRequestState.OPEN and self.head_commit is None:
            raise ValueError("Open forge observations require the checked head commit")
        if self.state is not ForgePullRequestState.OPEN and self.auto_merge_enabled:
            raise ValueError("Only open pull requests can have auto-merge enabled")


class ForgeClient(Protocol):
    @property
    def identity(self) -> str: ...

    @property
    def version(self) -> str: ...

    async def ensure_protected_pull_request(self, mutation: ForgeMutation) -> None: ...

    async def enable_protected_auto_merge(
        self, mutation: ForgeMutation, *, expected_head_commit: str
    ) -> None: ...

    async def observe(self, mutation: ForgeMutation) -> ForgeObservation: ...


class ForgeGovernanceAttester(Protocol):
    async def attest(self, mutation: ForgeMutation, *, head_commit: str) -> None: ...


class ForgeClientError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ForgeRetryableError(ForgeClientError):
    pass


class ForgeConflictError(ForgeClientError):
    pass


class ForgeTerminalError(ForgeClientError):
    pass
