from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_PLAIN_TEXT = re.compile(r"^[^\x00-\x08\x0b\x0c\x0e-\x1f\x7f]*$")
_IPV4 = re.compile(r"(?<![0-9])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])")
_INTERNAL_HOSTNAME = re.compile(
    r"(?i)(?<![a-z0-9-])(?:[a-z0-9-]+\.)+(?:internal|local|localhost|svc|cluster\.local)\b"
)
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)\b(?:authorization|password|passwd|secret|token|api[-_ ]?key)\s*[:=]\s*\S+"
)
_PROVIDER_RESOURCE_ID = re.compile(
    r"(?i)(?:\barn:(?:aws|aws-cn|aws-us-gov):|\b(?:dpg|srv)-[a-z0-9]{8,}\b)"
)
_IPV6 = re.compile(
    r"(?i)(?<![0-9a-f:])(?:"
    r"(?:[0-9a-f]{1,4}:){2,7}[0-9a-f]{0,4}"
    r"|[0-9a-f]{0,4}(?::[0-9a-f]{0,4})*::[0-9a-f]{0,4}(?::[0-9a-f]{0,4})*"
    r")(?![0-9a-f:])"
)


class PublicComponentState(StrEnum):
    OPERATIONAL = "operational"
    DEGRADED = "degraded"
    OUTAGE = "outage"
    MAINTENANCE = "maintenance"
    UNKNOWN = "unknown"


class PublicStatusUnknownReason(StrEnum):
    MISSING_EVIDENCE = "missing_evidence"
    STALE_EVIDENCE = "stale_evidence"
    MALFORMED_EVIDENCE = "malformed_evidence"


class PublicIncidentState(StrEnum):
    INVESTIGATING = "investigating"
    IDENTIFIED = "identified"
    MONITORING = "monitoring"
    RESOLVED = "resolved"


def normalize_public_text(value: str, *, maximum: int) -> str:
    if "\n" in value or "\r" in value:
        raise ValueError("Public operations text must not contain log-shaped multiline content")
    normalized = " ".join(unicodedata.normalize("NFKC", value).split())
    if not normalized or len(normalized) > maximum or not _PLAIN_TEXT.fullmatch(normalized):
        raise ValueError("Public operations text is invalid")
    if any(
        pattern.search(normalized)
        for pattern in (
            _IPV4,
            _IPV6,
            _INTERNAL_HOSTNAME,
            _CREDENTIAL_ASSIGNMENT,
            _PROVIDER_RESOURCE_ID,
        )
    ):
        raise ValueError(
            "Public operations text contains private infrastructure or credential data"
        )
    return normalized


def _require_utc(value: datetime, *, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    if value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{label} must use UTC")
    return value


def _validate_sorted_unique(values: tuple[str, ...], *, label: str) -> tuple[str, ...]:
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{label} must be sorted and unique")
    return values


ComponentId = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=64)]
ReleaseVersion = Annotated[
    str,
    Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$", max_length=64),
]
Sha256Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ComponentObservationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    component_id: ComponentId
    state: PublicComponentState
    successful: bool
    observed_at: datetime
    evidence_digest: Sha256Digest
    affected_versions: Annotated[tuple[ReleaseVersion, ...], Field(max_length=20)] = ()

    @field_validator("state")
    @classmethod
    def reject_projected_unknown(cls, value: PublicComponentState) -> PublicComponentState:
        if value is PublicComponentState.UNKNOWN:
            raise ValueError("Unknown is projected from absent or invalid monitor evidence")
        return value

    @field_validator("observed_at")
    @classmethod
    def validate_observed_at(cls, value: datetime) -> datetime:
        return _require_utc(value, label="Component observation time")

    @field_validator("affected_versions")
    @classmethod
    def validate_versions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_sorted_unique(value, label="Affected versions")


class PublicComponentStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    component_id: ComponentId
    display_name: Annotated[str, Field(min_length=1, max_length=80)]
    state: PublicComponentState
    reason: PublicStatusUnknownReason | None = None
    observed_at: datetime | None = None
    freshness_window_seconds: Annotated[int, Field(ge=30, le=3600)]
    evidence_digest: Sha256Digest | None = None
    affected_versions: Annotated[tuple[ReleaseVersion, ...], Field(max_length=20)] = ()

    @model_validator(mode="after")
    def validate_evidence_shape(self) -> Self:
        if self.observed_at is not None:
            _require_utc(self.observed_at, label="Public component observation time")
        unknown = self.state is PublicComponentState.UNKNOWN
        if unknown != (self.reason is not None):
            raise ValueError("Unknown component status requires exactly one safe reason")
        if not unknown and (self.observed_at is None or self.evidence_digest is None):
            raise ValueError("Published component state requires monitor evidence")
        if self.reason is PublicStatusUnknownReason.MISSING_EVIDENCE and (
            self.observed_at is not None or self.evidence_digest is not None
        ):
            raise ValueError("Missing monitor evidence cannot carry proof metadata")
        _validate_sorted_unique(self.affected_versions, label="Affected versions")
        return self


class PublicStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    configuration_digest: Sha256Digest
    components: Annotated[tuple[PublicComponentStatus, ...], Field(max_length=20)]


class IncidentRecoveryEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["verified"] = "verified"
    observed_at: datetime
    content_sha256: Sha256Digest

    @field_validator("observed_at")
    @classmethod
    def validate_observed_at(cls, value: datetime) -> datetime:
        return _require_utc(value, label="Recovery evidence observation time")


class PublicIncidentEventInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: PublicIncidentState
    public_summary: Annotated[str, Field(min_length=1, max_length=1000)]
    affected_component_ids: Annotated[tuple[ComponentId, ...], Field(min_length=1, max_length=20)]
    affected_versions: Annotated[tuple[ReleaseVersion, ...], Field(min_length=1, max_length=20)]
    guidance: Annotated[str, Field(min_length=1, max_length=1000)]
    occurred_at: datetime
    recovery_evidence: IncidentRecoveryEvidence | None = None

    @field_validator("public_summary", "guidance")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return normalize_public_text(value, maximum=1000)

    @field_validator("affected_component_ids")
    @classmethod
    def validate_components(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_sorted_unique(value, label="Affected component IDs")

    @field_validator("affected_versions")
    @classmethod
    def validate_versions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_sorted_unique(value, label="Affected versions")

    @field_validator("occurred_at")
    @classmethod
    def validate_occurred_at(cls, value: datetime) -> datetime:
        return _require_utc(value, label="Incident event time")

    @model_validator(mode="after")
    def validate_recovery_evidence(self) -> Self:
        resolved = self.state is PublicIncidentState.RESOLVED
        if resolved != (self.recovery_evidence is not None):
            raise ValueError("Resolved incidents require verified recovery evidence")
        if (
            self.recovery_evidence is not None
            and self.recovery_evidence.observed_at > self.occurred_at
        ):
            raise ValueError("Recovery evidence cannot postdate the resolution event")
        return self


class PublicIncident(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    incident_id: UUID
    title: Annotated[str, Field(min_length=1, max_length=160)]
    public_summary: Annotated[str, Field(min_length=1, max_length=1000)]
    affected_component_ids: Annotated[tuple[ComponentId, ...], Field(min_length=1, max_length=20)]
    affected_versions: Annotated[tuple[ReleaseVersion, ...], Field(min_length=1, max_length=20)]
    guidance: Annotated[str, Field(min_length=1, max_length=1000)]
    state: PublicIncidentState
    opened_at: datetime
    updated_at: datetime
    resolved_at: datetime | None = None
    recovery_evidence: IncidentRecoveryEvidence | None = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return normalize_public_text(value, maximum=160)

    @field_validator("public_summary", "guidance")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return normalize_public_text(value, maximum=1000)

    @field_validator("affected_component_ids")
    @classmethod
    def validate_components(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_sorted_unique(value, label="Affected component IDs")

    @field_validator("affected_versions")
    @classmethod
    def validate_versions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_sorted_unique(value, label="Affected versions")

    @model_validator(mode="after")
    def validate_incident(self) -> Self:
        _require_utc(self.opened_at, label="Incident opening time")
        _require_utc(self.updated_at, label="Incident update time")
        if self.updated_at < self.opened_at:
            raise ValueError("Incident update cannot predate opening")
        resolved = self.state is PublicIncidentState.RESOLVED
        if resolved != (self.resolved_at is not None):
            raise ValueError("Resolved incidents require a resolution time")
        if resolved != (self.recovery_evidence is not None):
            raise ValueError("Resolved incidents require verified recovery evidence")
        if self.resolved_at is not None:
            _require_utc(self.resolved_at, label="Incident resolution time")
            if self.resolved_at != self.updated_at:
                raise ValueError("Incident resolution time must equal the final event time")
        return self


class PublicIncidentListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    incidents: Annotated[tuple[PublicIncident, ...], Field(max_length=100)]


__all__ = [
    "ComponentObservationEvidence",
    "IncidentRecoveryEvidence",
    "PublicComponentState",
    "PublicComponentStatus",
    "PublicIncident",
    "PublicIncidentEventInput",
    "PublicIncidentListResponse",
    "PublicIncidentState",
    "PublicStatusResponse",
    "PublicStatusUnknownReason",
    "normalize_public_text",
]
