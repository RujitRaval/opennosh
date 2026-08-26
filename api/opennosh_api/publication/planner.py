from __future__ import annotations

from datetime import datetime

from opennosh_api.publication.receipts import (
    SignedPublicationReceipt,
    receipt_draft_from_snapshot,
)
from opennosh_api.publication.state import (
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
    effect_idempotency_key,
)

_TERMINAL_STATES = frozenset(
    {
        PublicationState.BLOCKED,
        PublicationState.FAILED,
        PublicationState.PUBLISHED,
        PublicationState.PUBLISH_BLOCKED,
        PublicationState.QUARANTINED,
    }
)


def plan_next_action(
    snapshot: PublicationSnapshot,
    observation: ExternalObservation | None,
    *,
    now: datetime,
) -> PlannerOutcome:
    """Return one deterministic next action without touching external state."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Planner time must include a timezone")
    if snapshot.state in _TERMINAL_STATES:
        return NoOpOutcome(reason=f"publication is {snapshot.state.value}")

    step = next(
        (
            candidate
            for candidate in snapshot.steps
            if candidate.state is not PublicationStepState.VERIFIED
        ),
        None,
    )
    if step is None:
        return TransitionOutcome(publication_state=PublicationState.PUBLISHED)

    if step.state in {PublicationStepState.BLOCKED, PublicationStepState.FAILED}:
        return NoOpOutcome(reason=f"step {step.name.value} is {step.state.value}")
    if (
        observation is None
        and step.state is PublicationStepState.LEASED
        and step.lease_expires_at is not None
        and step.lease_expires_at > now
    ):
        return WaitCondition(
            step=step.name,
            destination=step.destination,
            until=step.lease_expires_at,
            reason="active lease",
        )
    if observation is not None and (
        observation.step is not step.name or observation.destination != step.destination
    ):
        raise ValueError("Observation does not belong to the next protocol step")

    if observation is None or observation.status is ObservationStatus.ABSENT:
        return _effect_intent(snapshot, step.name, step.destination)

    if observation.status is ObservationStatus.VERIFIED:
        return TransitionOutcome(
            publication_state=_verified_publication_state(step.name),
            step=step.name,
            destination=step.destination,
            observation=observation,
        )
    if observation.status is ObservationStatus.RETRYABLE_FAILURE:
        # A retryable observation must cross the durable reducer boundary before
        # another effect can be attempted. A due or omitted retry time therefore
        # means "enqueue immediately", not "perform a second effect in this run".
        return WaitCondition(
            step=step.name,
            destination=step.destination,
            until=observation.retry_at or now,
            reason=observation.code or "retryable external state",
            observation=observation,
        )
    if observation.status is ObservationStatus.CONFLICT:
        return QuarantineOutcome(step=step.name, observation=observation)
    if observation.status is ObservationStatus.TERMINAL_FAILURE:
        return TerminalFailureOutcome(step=step.name, observation=observation)
    raise AssertionError(f"Unhandled observation status: {observation.status}")


def _effect_intent(
    snapshot: PublicationSnapshot,
    step: PublicationStepName,
    destination: str,
) -> EffectIntent:
    context: dict[str, object] = {}
    if step is PublicationStepName.SIGN_RECEIPT:
        context["receipt_draft"] = receipt_draft_from_snapshot(snapshot).model_dump(mode="json")
    elif step in {
        PublicationStepName.PUBLISH_RECEIPT_REGISTRY,
        PublicationStepName.COPY_RECEIPT,
    }:
        signed_acknowledgements = [
            acknowledgement
            for acknowledgement in snapshot.acknowledgements
            if acknowledgement.step is PublicationStepName.SIGN_RECEIPT
        ]
        if len(signed_acknowledgements) != 1:
            raise ValueError("Receipt replication requires one signing acknowledgement")
        envelope = signed_acknowledgements[0].context.get("signed_receipt")
        if not isinstance(envelope, dict):
            raise ValueError("Receipt signing acknowledgement lacks its signed envelope")
        SignedPublicationReceipt.model_validate(envelope)
        context["signed_receipt"] = envelope
    return EffectIntent(
        publication_id=snapshot.publication_id,
        workflow_version=snapshot.workflow_version,
        workflow_revision=snapshot.workflow_revision,
        step=step,
        destination=destination,
        approved_payload_digest=snapshot.approved_payload_digest,
        idempotency_key=effect_idempotency_key(
            publication_id=snapshot.publication_id,
            workflow_version=snapshot.workflow_version,
            step=step,
            destination=destination,
            approved_payload_digest=snapshot.approved_payload_digest,
        ),
        forge_target=snapshot.forge_target,
        context=context,
    )


def _verified_publication_state(
    step: PublicationStepName,
) -> PublicationState:
    if step in {
        PublicationStepName.COMMIT_RECORD,
        PublicationStepName.COPY_COMMIT,
        PublicationStepName.COPY_EVIDENCE,
    }:
        return PublicationState.COMMITTED
    return PublicationState.SIGNED
