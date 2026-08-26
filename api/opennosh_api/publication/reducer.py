from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

from opennosh_api.publication.receipts import (
    SignedPublicationReceipt,
    signed_receipt_digest,
    validate_receipt_binding,
)
from opennosh_api.publication.state import (
    DurableAcknowledgementSnapshot,
    EffectIntent,
    ExternalObservation,
    NoOpOutcome,
    ObservationStatus,
    PlannerOutcome,
    PublicationSnapshot,
    PublicationState,
    PublicationStepName,
    PublicationStepState,
    QuarantineOutcome,
    TerminalFailureOutcome,
    TransitionOutcome,
    WaitCondition,
)


@dataclass(frozen=True, slots=True)
class AcceptedEventData:
    repository: str
    commit_sha: str
    receipt_digest: str
    published_at: datetime
    event_type: str
    prior_receipt_digest: str | None
    envelope: Mapping[str, object]
    registry_reference: str
    artifact_reference: str


@dataclass(frozen=True, slots=True)
class PublicationReduction:
    expected_revision: int
    next_revision: int
    publication_state: PublicationState
    step: PublicationStepName | None
    destination: str | None
    step_state: PublicationStepState | None
    observation: ExternalObservation | None
    acknowledgement: DurableAcknowledgementSnapshot | None
    next_wake_at: datetime | None
    failure_code: str | None
    failure_context: Mapping[str, object]
    accepted_event: AcceptedEventData | None = None


def reduce_planner_outcome(
    snapshot: PublicationSnapshot,
    outcome: PlannerOutcome,
    *,
    now: datetime,
) -> PublicationReduction | None:
    """Convert one planned outcome into one compare-and-swap persistence command."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Reducer time must include a timezone")
    if isinstance(outcome, EffectIntent):
        raise ValueError("Effects must be executed and observed before reduction")
    if isinstance(outcome, NoOpOutcome):
        return None
    if isinstance(outcome, WaitCondition):
        if outcome.observation is None:
            return None
        _validate_identity(outcome.step, outcome.destination, outcome.observation)
        return _reduction(
            snapshot,
            publication_state=_retrying_state(outcome.step),
            step=outcome.step,
            destination=outcome.destination,
            step_state=PublicationStepState.RETRYING,
            observation=outcome.observation,
            next_wake_at=outcome.until,
            failure_code=outcome.observation.code or outcome.reason,
            failure_context=outcome.observation.context,
        )
    if isinstance(outcome, QuarantineOutcome):
        return _reduction(
            snapshot,
            publication_state=_conflict_state(outcome.step),
            step=outcome.step,
            destination=outcome.observation.destination,
            step_state=PublicationStepState.BLOCKED,
            observation=outcome.observation,
            next_wake_at=None,
            failure_code=outcome.observation.code or "external_state_conflict",
            failure_context=outcome.observation.context,
        )
    if isinstance(outcome, TerminalFailureOutcome):
        return _reduction(
            snapshot,
            publication_state=PublicationState.FAILED,
            step=outcome.step,
            destination=outcome.observation.destination,
            step_state=PublicationStepState.FAILED,
            observation=outcome.observation,
            next_wake_at=None,
            failure_code=outcome.observation.code or "terminal_external_failure",
            failure_context=outcome.observation.context,
        )
    if isinstance(outcome, TransitionOutcome):
        if outcome.step is None:
            proof = _accepted_event_proof(snapshot)
            return _reduction(
                snapshot,
                publication_state=PublicationState.PUBLISHED,
                step=None,
                destination=None,
                step_state=None,
                observation=None,
                next_wake_at=None,
                failure_code=None,
                failure_context={},
                accepted_event=proof,
            )
        if outcome.destination is None or outcome.observation is None:
            raise ValueError("Step transitions require a destination and observation")
        _validate_identity(outcome.step, outcome.destination, outcome.observation)
        if outcome.observation.status is not ObservationStatus.VERIFIED:
            raise ValueError("Only verified observations can complete a step")
        acknowledgement = DurableAcknowledgementSnapshot(
            step=outcome.step,
            destination=outcome.destination,
            content_digest=outcome.observation.content_digest or "",
            external_reference=outcome.observation.external_reference,
            verified_at=outcome.observation.observed_at,
            context={
                **dict(outcome.observation.context),
                "adapter_identity": outcome.observation.adapter_identity,
                "adapter_version": outcome.observation.adapter_version,
                "effect_idempotency_key": outcome.observation.effect_idempotency_key,
            },
        )
        existing = _find_acknowledgement(snapshot, outcome.step, outcome.destination)
        if existing is not None and existing != acknowledgement:
            return _reduction(
                snapshot,
                publication_state=PublicationState.QUARANTINED,
                step=outcome.step,
                destination=outcome.destination,
                step_state=PublicationStepState.BLOCKED,
                observation=outcome.observation,
                next_wake_at=None,
                failure_code="durable_acknowledgement_conflict",
                failure_context={"destination": outcome.destination},
            )
        return _reduction(
            snapshot,
            publication_state=outcome.publication_state,
            step=outcome.step,
            destination=outcome.destination,
            step_state=PublicationStepState.VERIFIED,
            observation=outcome.observation,
            acknowledgement=None if existing is not None else acknowledgement,
            next_wake_at=now,
            failure_code=None,
            failure_context={},
        )
    raise AssertionError(f"Unhandled planner outcome: {type(outcome).__name__}")


def _reduction(
    snapshot: PublicationSnapshot,
    *,
    publication_state: PublicationState,
    step: PublicationStepName | None,
    destination: str | None,
    step_state: PublicationStepState | None,
    observation: ExternalObservation | None,
    next_wake_at: datetime | None,
    failure_code: str | None,
    failure_context: Mapping[str, object],
    acknowledgement: DurableAcknowledgementSnapshot | None = None,
    accepted_event: AcceptedEventData | None = None,
) -> PublicationReduction:
    return PublicationReduction(
        expected_revision=snapshot.workflow_revision,
        next_revision=snapshot.workflow_revision + 1,
        publication_state=publication_state,
        step=step,
        destination=destination,
        step_state=step_state,
        observation=observation,
        acknowledgement=acknowledgement,
        next_wake_at=next_wake_at,
        failure_code=failure_code,
        failure_context=MappingProxyType(dict(failure_context)),
        accepted_event=accepted_event,
    )


def _validate_identity(
    step: PublicationStepName,
    destination: str,
    observation: ExternalObservation,
) -> None:
    if observation.step is not step or observation.destination != destination:
        raise ValueError("Reducer observation does not match the planned effect identity")


def _find_acknowledgement(
    snapshot: PublicationSnapshot,
    step: PublicationStepName,
    destination: str,
) -> DurableAcknowledgementSnapshot | None:
    return next(
        (
            acknowledgement
            for acknowledgement in snapshot.acknowledgements
            if acknowledgement.step is step and acknowledgement.destination == destination
        ),
        None,
    )


def _accepted_event_proof(
    snapshot: PublicationSnapshot,
) -> AcceptedEventData:
    if any(step.state is not PublicationStepState.VERIFIED for step in snapshot.steps):
        raise ValueError("Publication cannot complete before every protocol step verifies")
    commit = _single_acknowledgement(snapshot, PublicationStepName.COMMIT_RECORD)
    signed_receipt = _single_acknowledgement(snapshot, PublicationStepName.SIGN_RECEIPT)
    registry_receipt = _single_acknowledgement(
        snapshot, PublicationStepName.PUBLISH_RECEIPT_REGISTRY
    )
    copied_receipt = _single_acknowledgement(snapshot, PublicationStepName.COPY_RECEIPT)
    receipt_digests = {
        signed_receipt.content_digest,
        registry_receipt.content_digest,
        copied_receipt.content_digest,
    }
    if len(receipt_digests) != 1:
        raise ValueError("Receipt acknowledgements do not agree on one digest")
    commit_hash = commit.external_reference
    if (
        commit_hash is None
        or len(commit_hash) not in {40, 64}
        or any(character not in "0123456789abcdef" for character in commit_hash)
    ):
        raise ValueError("Commit acknowledgement lacks a canonical commit hash")
    envelope_value = signed_receipt.context.get("signed_receipt")
    if not isinstance(envelope_value, dict):
        raise ValueError("Receipt acknowledgement lacks the signed envelope")
    envelope = SignedPublicationReceipt.model_validate(envelope_value)
    validate_receipt_binding(envelope, snapshot)
    if signed_receipt_digest(envelope) != signed_receipt.content_digest:
        raise ValueError("Signed receipt digest does not match its envelope")
    if registry_receipt.external_reference is None:
        raise ValueError("Registry receipt acknowledgement lacks an external reference")
    if copied_receipt.external_reference is None:
        raise ValueError("Receipt artifact acknowledgement lacks an external reference")
    return AcceptedEventData(
        repository=commit.destination,
        commit_sha=commit_hash,
        receipt_digest=signed_receipt.content_digest,
        published_at=envelope.receipt.published_at,
        event_type=envelope.receipt.event_type.value,
        prior_receipt_digest=envelope.receipt.prior_receipt_digest,
        envelope=MappingProxyType(envelope.model_dump(mode="json")),
        registry_reference=registry_receipt.external_reference,
        artifact_reference=copied_receipt.external_reference,
    )


def _single_acknowledgement(
    snapshot: PublicationSnapshot,
    step: PublicationStepName,
) -> DurableAcknowledgementSnapshot:
    matches = [
        acknowledgement
        for acknowledgement in snapshot.acknowledgements
        if acknowledgement.step is step
    ]
    if len(matches) != 1:
        raise ValueError(f"Publication requires one {step.value} acknowledgement")
    return matches[0]


def _retrying_state(step: PublicationStepName) -> PublicationState:
    if step in {
        PublicationStepName.COMMIT_RECORD,
        PublicationStepName.COPY_COMMIT,
        PublicationStepName.COPY_EVIDENCE,
        PublicationStepName.SIGN_RELEASE,
    }:
        return PublicationState.RETRYING
    return PublicationState.PUBLISH_RETRYING


def _conflict_state(step: PublicationStepName) -> PublicationState:
    if step is PublicationStepName.COMMIT_RECORD:
        return PublicationState.PUBLISH_BLOCKED
    return PublicationState.QUARANTINED
