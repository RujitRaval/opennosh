from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_SOURCE_DRILL_CONTRACT_PATH = (
    Path(__file__).resolve().parents[3] / "config" / "federation-failure-drills.v1.json"
)
_PACKAGED_DRILL_CONTRACT_PATH = (
    Path(__file__).resolve().parents[1] / "federation-failure-drills.v1.json"
)
DEFAULT_DRILL_CONTRACT_PATH = (
    _SOURCE_DRILL_CONTRACT_PATH
    if _SOURCE_DRILL_CONTRACT_PATH.is_file()
    else _PACKAGED_DRILL_CONTRACT_PATH
)
_SHA256 = r"^[0-9a-f]{64}$"
_COMMIT = r"^[0-9a-f]{40}$"
_EVIDENCE_IDENTIFIER = r"^[A-Za-z0-9][A-Za-z0-9._:/#-]{0,199}$"


class FailureDrillCase(StrEnum):
    IDEMPOTENT_REPLAY = "idempotent_replay"
    FORGE_OUTAGE = "forge_outage"
    SIGNER_OUTAGE = "signer_outage"
    R2_IMMUTABLE_CONFLICT = "r2_immutable_conflict"
    WORKER_RESTART = "worker_restart"
    DATABASE_LEASE_RECOVERY = "database_lease_recovery"
    ROLLBACK_EQUIVOCATION_REFUSAL = "rollback_equivocation_refusal"
    FORGE_CREDENTIAL_ROTATION = "forge_credential_rotation"
    WORKER_PAUSE_RESUME = "worker_pause_resume"
    PUBLIC_NAVIGATION_ROLLBACK = "public_navigation_rollback"


class PublicCheckName(StrEnum):
    API_HEALTH = "api_health"
    LATEST_POINTER = "latest_pointer"
    MANIFEST = "manifest"
    LATEST_FOOD = "latest_food"
    PINNED_FOOD = "pinned_food"
    PROVENANCE = "provenance"
    RECEIPT = "receipt"


class EvidenceKind(StrEnum):
    RENDER_EVENT = "render_event"
    GITHUB_AUDIT = "github_audit"
    R2_OBJECT = "r2_object"
    POSTGRES_SNAPSHOT = "postgres_snapshot"
    HTTP_SNAPSHOT = "http_snapshot"
    WORKER_LOG = "worker_log"


class DrillCaseContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sequence: int = Field(ge=1, le=10)
    case_id: FailureDrillCase
    injection: str = Field(min_length=1, max_length=500)
    expected_failure_code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,99}$")
    expected_side_effect_delta: int = Field(ge=0, le=100)
    evidence_kinds: tuple[EvidenceKind, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_evidence_kinds(self) -> DrillCaseContract:
        if len(set(self.evidence_kinds)) != len(self.evidence_kinds):
            raise ValueError("drill_evidence_kinds_duplicate")
        return self


class FailureDrillContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    recovery_limit_seconds: Literal[600]
    navigation_rollback_limit_seconds: Literal[300]
    required_public_checks: tuple[PublicCheckName, ...]
    cases: tuple[DrillCaseContract, ...]

    @model_validator(mode="after")
    def require_canonical_matrix(self) -> FailureDrillContract:
        expected_cases = tuple(FailureDrillCase)
        if tuple(case.case_id for case in self.cases) != expected_cases:
            raise ValueError("drill_case_matrix_not_canonical")
        if tuple(case.sequence for case in self.cases) != tuple(range(1, 11)):
            raise ValueError("drill_case_sequence_not_canonical")
        if self.required_public_checks != tuple(PublicCheckName):
            raise ValueError("drill_public_checks_not_canonical")
        return self


class ReleaseIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    release_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
    publication_id: UUID
    manifest_digest: str = Field(pattern=_SHA256)
    receipt_digest: str = Field(pattern=_SHA256)
    pointer_key_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    public_origin: str = Field(min_length=1, max_length=2048)

    @field_validator("public_origin")
    @classmethod
    def require_canonical_public_origin(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path
            or value != f"https://{parsed.netloc}"
        ):
            raise ValueError("drill_public_origin_not_canonical")
        return value


class EvidenceReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: EvidenceKind
    identifier: str = Field(pattern=_EVIDENCE_IDENTIFIER)
    digest: str = Field(pattern=_SHA256)


class PublicCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: PublicCheckName
    status_code: Literal[200]
    digest: str = Field(pattern=_SHA256)


class DrillResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sequence: int = Field(ge=1, le=10)
    case_id: FailureDrillCase
    started_at: datetime
    failure_observed_at: datetime
    restoration_started_at: datetime
    recovered_at: datetime
    expected_failure_code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,99}$")
    observed_failure_code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,99}$")
    claims_enabled_before: Literal[False]
    claims_enabled_during: Literal[False]
    claims_enabled_after: Literal[False]
    activation_ids_present_before: Literal[False]
    activation_ids_present_during: Literal[False]
    activation_ids_present_after: Literal[False]
    false_published_count: Literal[0]
    immutable_overwrite_count: Literal[0]
    side_effect_delta: int = Field(ge=0, le=100)
    restoration_verified: Literal[True]
    public_checks: tuple[PublicCheck, ...]
    release_identity_after: ReleaseIdentity
    evidence: tuple[EvidenceReference, ...] = Field(min_length=1)

    @field_validator(
        "started_at",
        "failure_observed_at",
        "restoration_started_at",
        "recovered_at",
    )
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("drill_timestamp_requires_timezone")
        return value

    @model_validator(mode="after")
    def require_ordered_complete_observation(self) -> DrillResult:
        if not (
            self.started_at
            <= self.failure_observed_at
            <= self.restoration_started_at
            <= self.recovered_at
        ):
            raise ValueError("drill_timestamps_not_ordered")
        if tuple(check.name for check in self.public_checks) != tuple(PublicCheckName):
            raise ValueError("drill_public_checks_incomplete_or_out_of_order")
        if len({reference.kind for reference in self.evidence}) != len(self.evidence):
            raise ValueError("drill_evidence_kind_duplicate")
        return self


class FailureDrillReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    contract_digest: str = Field(pattern=_SHA256)
    production_commit: str = Field(pattern=_COMMIT)
    captured_at: datetime
    baseline: ReleaseIdentity
    drills: tuple[DrillResult, ...]

    @field_validator("captured_at")
    @classmethod
    def require_aware_captured_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("drill_timestamp_requires_timezone")
        return value

    @model_validator(mode="after")
    def require_canonical_results(self) -> FailureDrillReport:
        if tuple(result.case_id for result in self.drills) != tuple(FailureDrillCase):
            raise ValueError("drill_results_missing_duplicate_or_out_of_order")
        if tuple(result.sequence for result in self.drills) != tuple(range(1, 11)):
            raise ValueError("drill_result_sequence_not_canonical")
        if any(result.release_identity_after != self.baseline for result in self.drills):
            raise ValueError("drill_release_identity_drift")
        if self.drills and self.captured_at < self.drills[-1].recovered_at:
            raise ValueError("drill_report_captured_before_recovery")
        return self


class FailureDrillInvariantError(ValueError):
    pass


class FailureDrillSecretError(ValueError):
    pass


class ControlledFailureDrillAdapter(Protocol):
    async def inject(self, case: DrillCaseContract) -> None: ...

    async def observe_failure(self, case: DrillCaseContract) -> str: ...

    async def restore(self, case: DrillCaseContract) -> None: ...

    async def restoration_verified(self, case: DrillCaseContract) -> bool: ...


_SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN [^-\r\n]*(?:PRIVATE KEY|OPENSSH PRIVATE KEY)-----", re.I),
    re.compile(rb"(?:github_pat_|gh[opsu]_)[A-Za-z0-9_]{16,}"),
    re.compile(rb"AKIA[0-9A-Z]{16}"),
    re.compile(rb"[a-z][a-z0-9+.-]*://[^/\s:@]+:[^@\s]+@", re.I),
    re.compile(rb"(?:PRIVATE_KEY|SECRET|PASSWORD|TOKEN)\s*=", re.I),
    re.compile(
        rb'"[^"\r\n]*(?:private[_-]?key|secret|password|token)[^"\r\n]*"\s*:',
        re.I,
    ),
)


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def canonical_digest(value: BaseModel) -> str:
    return hashlib.sha256(canonical_json(value.model_dump(mode="json"))).hexdigest()


def load_failure_drill_contract(
    path: Path = DEFAULT_DRILL_CONTRACT_PATH,
) -> FailureDrillContract:
    return FailureDrillContract.model_validate_json(path.read_text(encoding="utf-8"))


def scan_report_bytes(payload: bytes) -> None:
    if any(pattern.search(payload) is not None for pattern in _SECRET_PATTERNS):
        raise FailureDrillSecretError("drill_report_secret_pattern_detected")


def parse_failure_drill_report(payload: bytes) -> FailureDrillReport:
    scan_report_bytes(payload)
    return FailureDrillReport.model_validate_json(payload)


def validate_failure_drill_report(
    report: FailureDrillReport,
    contract: FailureDrillContract,
) -> str:
    if report.contract_digest != canonical_digest(contract):
        raise FailureDrillInvariantError("drill_contract_digest_mismatch")
    for expected, observed in zip(contract.cases, report.drills, strict=True):
        if observed.expected_failure_code != expected.expected_failure_code:
            raise FailureDrillInvariantError("drill_expected_failure_code_mismatch")
        if observed.observed_failure_code != expected.expected_failure_code:
            raise FailureDrillInvariantError("drill_observed_failure_code_mismatch")
        if observed.side_effect_delta != expected.expected_side_effect_delta:
            raise FailureDrillInvariantError("drill_side_effect_delta_mismatch")
        observed_kinds = {reference.kind for reference in observed.evidence}
        if not set(expected.evidence_kinds).issubset(observed_kinds):
            raise FailureDrillInvariantError("drill_required_evidence_missing")
        elapsed = (observed.recovered_at - observed.started_at).total_seconds()
        limit = (
            contract.navigation_rollback_limit_seconds
            if observed.case_id is FailureDrillCase.PUBLIC_NAVIGATION_ROLLBACK
            else contract.recovery_limit_seconds
        )
        if elapsed > limit:
            raise FailureDrillInvariantError("drill_recovery_limit_exceeded")
    return canonical_digest(report)


async def exercise_controlled_failure(
    case: DrillCaseContract,
    adapter: ControlledFailureDrillAdapter,
) -> str:
    """Exercise one synthetic failure while guaranteeing restoration is attempted."""

    try:
        await adapter.inject(case)
        observed_code = await adapter.observe_failure(case)
    finally:
        await adapter.restore(case)
    if not await adapter.restoration_verified(case):
        raise FailureDrillInvariantError("drill_restoration_not_verified")
    if observed_code != case.expected_failure_code:
        raise FailureDrillInvariantError("drill_observed_failure_code_mismatch")
    return observed_code
