from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
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
    merged_tree_digest: str | None = None
    merged_payload_digest: str | None = None

    def __post_init__(self) -> None:
        names = tuple(name for name, _ in self.checks)
        if len(names) != len(set(names)):
            raise ValueError("Forge check names must be unique")
        if self.state is ForgePullRequestState.MERGED:
            if (
                self.merged_at is None
                or self.merged_commit is None
                or self.merged_tree_digest is None
            ):
                raise ValueError("Merged forge observations require time, commit, and tree proof")
            if self.merged_at.tzinfo is None or self.merged_at.utcoffset() is None:
                raise ValueError("Forge merge time must include a timezone")
            if len(self.merged_tree_digest) != 64 or any(
                character not in "0123456789abcdef"
                for character in self.merged_tree_digest
            ):
                raise ValueError("Merged forge tree proof must be SHA-256")
        if self.state is ForgePullRequestState.OPEN and self.head_commit is None:
            raise ValueError("Open forge observations require the checked head commit")
        if self.state is not ForgePullRequestState.OPEN and self.auto_merge_enabled:
            raise ValueError("Only open pull requests can have auto-merge enabled")


@dataclass(frozen=True, slots=True)
class MergedPackMaterial:
    """Bounded repository bytes proven to belong to one merged Git tree."""

    commit_sha: str
    tree_digest: str
    files: Mapping[str, bytes]

    def __post_init__(self) -> None:
        if len(self.commit_sha) not in {40, 64} or any(
            character not in "0123456789abcdef" for character in self.commit_sha
        ):
            raise ValueError("Merged pack commit must be a full lowercase Git hash")
        _validate_digest(self.tree_digest, "Merged pack tree proof")
        if not self.files:
            raise ValueError("Merged pack material cannot be empty")
        object.__setattr__(self, "files", MappingProxyType(dict(self.files)))


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


class ForgeMaterialClient(ForgeClient, Protocol):
    async def read_merged_pack(
        self,
        mutation: ForgeMutation,
        *,
        expected_commit: str,
        expected_tree_digest: str,
    ) -> MergedPackMaterial: ...


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


def _validate_digest(value: str, label: str) -> None:
    if len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError(f"{label} must be SHA-256")
