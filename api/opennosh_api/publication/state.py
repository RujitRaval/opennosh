from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import TypeAlias
from uuid import UUID


class PublicationState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    RETRYING = "retrying"
    BLOCKED = "blocked"
    FAILED = "failed"
    PUBLISHED = "published"
    COMMITTED = "committed"
    SIGNED = "signed"
    PUBLISH_BLOCKED = "publish_blocked"
    PUBLISH_RETRYING = "publish_retrying"
    QUARANTINED = "quarantined"


class PublicationStepName(StrEnum):
    COMMIT_RECORD = "commit_record"
    COPY_COMMIT = "copy_commit"
    COPY_EVIDENCE = "copy_evidence"
    SIGN_RELEASE = "sign_release"
    PUBLISH_RELEASE = "publish_release"
    COPY_RELEASE = "copy_release"
    CONFIRM_REGISTRY = "confirm_registry"
    SIGN_RECEIPT = "sign_receipt"
    PUBLISH_RECEIPT_REGISTRY = "publish_receipt_registry"
    COPY_RECEIPT = "copy_receipt"


class PublicationStepState(StrEnum):
    PENDING = "pending"
    LEASED = "leased"
    RETRYING = "retrying"
    BLOCKED = "blocked"
    FAILED = "failed"
    VERIFIED = "verified"


class ObservationStatus(StrEnum):
    ABSENT = "absent"
    VERIFIED = "verified"
    RETRYABLE_FAILURE = "retryable_failure"
    CONFLICT = "conflict"
    TERMINAL_FAILURE = "terminal_failure"


@dataclass(frozen=True, slots=True)
class ProtocolStepDefinition:
    name: PublicationStepName
    ordinal: int
    destination: str


def publication_protocol(forge_target: str) -> tuple[ProtocolStepDefinition, ...]:
    """Build the explicit receipt-gated publication protocol for one forge target."""

    destinations = {
        PublicationStepName.COMMIT_RECORD: forge_target,
        PublicationStepName.COPY_COMMIT: "urn:opennosh:durability:git",
        PublicationStepName.COPY_EVIDENCE: "urn:opennosh:durability:evidence",
        PublicationStepName.SIGN_RELEASE: f"{forge_target}#release-signature",
        PublicationStepName.PUBLISH_RELEASE: f"{forge_target}#release",
        PublicationStepName.COPY_RELEASE: "urn:opennosh:durability:release",
        PublicationStepName.CONFIRM_REGISTRY: "urn:opennosh:registry:release",
        PublicationStepName.SIGN_RECEIPT: "urn:opennosh:receipt:signer",
        PublicationStepName.PUBLISH_RECEIPT_REGISTRY: "urn:opennosh:registry:receipt",
        PublicationStepName.COPY_RECEIPT: "urn:opennosh:durability:receipt",
    }
    return tuple(
        ProtocolStepDefinition(
            name=name,
            ordinal=ordinal,
            destination=destinations[name],
        )
        for ordinal, name in enumerate(PublicationStepName)
    )


@dataclass(frozen=True, slots=True)
class PublicationStepSnapshot:
    name: PublicationStepName
    ordinal: int
    destination: str
    state: PublicationStepState
    step_version: int = 1
    attempt_count: int = 0
    queue_job_id: int | None = None
    lease_token: UUID | None = None
    lease_expires_at: datetime | None = None
    next_attempt_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.ordinal < 0:
            raise ValueError("Publication step ordinal cannot be negative")
        if not self.destination:
            raise ValueError("Publication step destination cannot be empty")
        if self.step_version < 1:
            raise ValueError("Publication step version must be positive")
        if self.attempt_count < 0:
            raise ValueError("Publication step attempts cannot be negative")
        if self.state is PublicationStepState.LEASED and (
            self.lease_token is None or self.lease_expires_at is None
        ):
            raise ValueError("Leased publication steps require a token and expiry")


@dataclass(frozen=True, slots=True)
class DurableAcknowledgementSnapshot:
    step: PublicationStepName
    destination: str
    content_digest: str
    external_reference: str | None
    verified_at: datetime
    context: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if not self.destination:
            raise ValueError("Acknowledgement destination cannot be empty")
        if len(self.content_digest) != 64:
            raise ValueError("Acknowledgement content digest must be SHA-256")
        if self.verified_at.tzinfo is None or self.verified_at.utcoffset() is None:
            raise ValueError("Acknowledgement verification time must include a timezone")
        object.__setattr__(self, "context", MappingProxyType(dict(self.context)))


@dataclass(frozen=True, slots=True)
class PublicationSnapshot:
    publication_id: UUID
    workflow_version: str
    workflow_revision: int
    state: PublicationState
    pack_id: str
    record_id: str
    approved_payload_digest: str
    expected_base_commit: str
    required_checks: tuple[str, ...]
    forge_target: str
    steps: tuple[PublicationStepSnapshot, ...]
    acknowledgements: tuple[DurableAcknowledgementSnapshot, ...]

    def __post_init__(self) -> None:
        if self.workflow_revision < 0:
            raise ValueError("Workflow revision cannot be negative")
        if len(self.approved_payload_digest) != 64:
            raise ValueError("Approved payload digest must be SHA-256")
        if not self.steps:
            raise ValueError("Publication snapshot must contain protocol steps")
        ordinals = tuple(step.ordinal for step in self.steps)
        if ordinals != tuple(range(len(self.steps))):
            raise ValueError("Publication steps must have contiguous ordered ordinals")
        identities = {(step.name, step.destination) for step in self.steps}
        if len(identities) != len(self.steps):
            raise ValueError("Publication step destinations must be unique")
        names = tuple(step.name for step in self.steps)
        if names != tuple(PublicationStepName):
            raise ValueError("Publication steps must match the explicit protocol order")
        expected_destinations = tuple(
            definition.destination for definition in publication_protocol(self.forge_target)
        )
        actual_destinations = tuple(step.destination for step in self.steps)
        if actual_destinations != expected_destinations:
            raise ValueError("Publication step destinations must match the canonical protocol")
        acknowledgement_identities = {
            (acknowledgement.step, acknowledgement.destination)
            for acknowledgement in self.acknowledgements
        }
        if len(acknowledgement_identities) != len(self.acknowledgements):
            raise ValueError("Publication acknowledgements must have unique destinations")
        if not acknowledgement_identities.issubset(identities):
            raise ValueError("Publication acknowledgements must belong to protocol steps")
        verified_identities = {
            (step.name, step.destination)
            for step in self.steps
            if step.state is PublicationStepState.VERIFIED
        }
        if not verified_identities.issubset(acknowledgement_identities):
            raise ValueError("Every verified step requires a durable acknowledgement")


@dataclass(frozen=True, slots=True)
class ExternalObservation:
    step: PublicationStepName
    status: ObservationStatus
    observed_at: datetime
    destination: str
    effect_idempotency_key: str
    adapter_identity: str
    adapter_version: str
    content_digest: str | None = None
    external_reference: str | None = None
    retry_at: datetime | None = None
    code: str | None = None
    context: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("External observation time must include a timezone")
        if self.retry_at is not None and (
            self.retry_at.tzinfo is None or self.retry_at.utcoffset() is None
        ):
            raise ValueError("External retry time must include a timezone")
        if self.status is ObservationStatus.VERIFIED and self.content_digest is None:
            raise ValueError("Verified observations require a content digest")
        if self.content_digest is not None and len(self.content_digest) != 64:
            raise ValueError("Observation content digest must be SHA-256")
        if len(self.effect_idempotency_key) != 64:
            raise ValueError("Observation effect key must be SHA-256")
        if not self.adapter_identity:
            raise ValueError("Observation adapter identity cannot be empty")
        if not self.adapter_version:
            raise ValueError("Observation adapter version cannot be empty")
        object.__setattr__(self, "context", MappingProxyType(dict(self.context)))


@dataclass(frozen=True, slots=True)
class EffectIntent:
    publication_id: UUID
    workflow_version: str
    workflow_revision: int
    step: PublicationStepName
    destination: str
    approved_payload_digest: str
    idempotency_key: str
    forge_target: str


@dataclass(frozen=True, slots=True)
class TransitionOutcome:
    publication_state: PublicationState
    step: PublicationStepName | None = None
    destination: str | None = None
    observation: ExternalObservation | None = None


@dataclass(frozen=True, slots=True)
class WaitCondition:
    step: PublicationStepName
    destination: str
    until: datetime
    reason: str
    observation: ExternalObservation | None = None


@dataclass(frozen=True, slots=True)
class QuarantineOutcome:
    step: PublicationStepName
    observation: ExternalObservation


@dataclass(frozen=True, slots=True)
class TerminalFailureOutcome:
    step: PublicationStepName
    observation: ExternalObservation


@dataclass(frozen=True, slots=True)
class NoOpOutcome:
    reason: str


PlannerOutcome: TypeAlias = (
    EffectIntent
    | TransitionOutcome
    | WaitCondition
    | QuarantineOutcome
    | TerminalFailureOutcome
    | NoOpOutcome
)


def effect_idempotency_key(
    *,
    publication_id: UUID,
    workflow_version: str,
    step: PublicationStepName,
    destination: str,
    approved_payload_digest: str,
) -> str:
    material = "|".join(
        (
            str(publication_id),
            workflow_version,
            step.value,
            destination,
            approved_payload_digest,
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
