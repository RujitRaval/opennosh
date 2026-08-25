from __future__ import annotations

from dataclasses import replace

import pytest
from opennosh_api.publication.planner import plan_next_action
from opennosh_api.publication.reducer import reduce_planner_outcome
from opennosh_api.publication.state import (
    DurableAcknowledgementSnapshot,
    EffectIntent,
    NoOpOutcome,
    ObservationStatus,
    PublicationState,
    PublicationStepName,
    PublicationStepState,
    QuarantineOutcome,
    TerminalFailureOutcome,
    TransitionOutcome,
    WaitCondition,
)
from tests.publication.test_planner import DIGEST, NOW, observation, snapshot


def test_verified_transition_creates_bound_acknowledgement_and_next_revision() -> None:
    source = snapshot(revision=4)
    verified = observation(source, ObservationStatus.VERIFIED)
    outcome = TransitionOutcome(
        publication_state=PublicationState.COMMITTED,
        step=PublicationStepName.COMMIT_RECORD,
        destination=verified.destination,
        observation=verified,
    )

    reduction = reduce_planner_outcome(source, outcome, now=NOW)

    assert reduction is not None
    assert reduction.expected_revision == 4
    assert reduction.next_revision == 5
    assert reduction.step_state is PublicationStepState.VERIFIED
    assert reduction.acknowledgement is not None
    assert reduction.acknowledgement.context["effect_idempotency_key"] == (
        verified.effect_idempotency_key
    )


def test_different_existing_acknowledgement_quarantines_instead_of_overwriting() -> None:
    source = snapshot()
    verified = observation(source, ObservationStatus.VERIFIED)
    conflicting = DurableAcknowledgementSnapshot(
        step=verified.step,
        destination=verified.destination,
        content_digest="f" * 64,
        external_reference="e" * 40,
        verified_at=NOW,
    )
    source = replace(source, acknowledgements=(conflicting,))
    outcome = TransitionOutcome(
        publication_state=PublicationState.COMMITTED,
        step=verified.step,
        destination=verified.destination,
        observation=verified,
    )

    reduction = reduce_planner_outcome(source, outcome, now=NOW)

    assert reduction is not None
    assert reduction.publication_state is PublicationState.QUARANTINED
    assert reduction.failure_code == "durable_acknowledgement_conflict"


def test_final_transition_requires_matching_receipt_proofs() -> None:
    source = snapshot(current=10)
    receipt_acks = tuple(
        replace(acknowledgement, content_digest=DIGEST)
        if acknowledgement.step
        in {
            PublicationStepName.SIGN_RECEIPT,
            PublicationStepName.PUBLISH_RECEIPT_REGISTRY,
            PublicationStepName.COPY_RECEIPT,
        }
        else acknowledgement
        for acknowledgement in source.acknowledgements
    )
    source = replace(source, acknowledgements=receipt_acks)

    reduction = reduce_planner_outcome(
        source,
        TransitionOutcome(publication_state=PublicationState.PUBLISHED),
        now=NOW,
    )

    assert reduction is not None
    assert reduction.accepted_event is not None
    assert reduction.accepted_event.receipt_digest == DIGEST


def test_final_transition_rejects_receipt_digest_disagreement() -> None:
    source = snapshot(current=10)
    acknowledgements = tuple(
        replace(acknowledgement, content_digest="e" * 64)
        if acknowledgement.step is PublicationStepName.COPY_RECEIPT
        else acknowledgement
        for acknowledgement in source.acknowledgements
    )
    source = replace(source, acknowledgements=acknowledgements)

    with pytest.raises(ValueError, match="do not agree"):
        reduce_planner_outcome(
            source,
            TransitionOutcome(publication_state=PublicationState.PUBLISHED),
            now=NOW,
        )


def test_quarantine_observation_never_schedules_another_wakeup() -> None:
    source = snapshot()
    conflict = observation(source, ObservationStatus.CONFLICT)

    reduction = reduce_planner_outcome(
        source,
        QuarantineOutcome(step=conflict.step, observation=conflict),
        now=NOW,
    )

    assert reduction is not None
    assert reduction.publication_state is PublicationState.PUBLISH_BLOCKED
    assert reduction.next_wake_at is None


def test_noop_and_lease_only_wait_do_not_persist() -> None:
    source = snapshot()
    assert reduce_planner_outcome(source, NoOpOutcome(reason="done"), now=NOW) is None
    assert (
        reduce_planner_outcome(
            source,
            WaitCondition(
                step=source.steps[0].name,
                destination=source.steps[0].destination,
                until=NOW,
                reason="active lease",
            ),
            now=NOW,
        )
        is None
    )


@pytest.mark.parametrize(
    ("current", "expected_state"),
    [
        (3, PublicationState.RETRYING),
        (4, PublicationState.PUBLISH_RETRYING),
    ],
)
def test_observed_retry_preserves_pre_and_post_signing_phase(
    current: int,
    expected_state: PublicationState,
) -> None:
    source = snapshot(current=current)
    retry = observation(
        source,
        ObservationStatus.RETRYABLE_FAILURE,
        retry_at=NOW,
    )
    reduction = reduce_planner_outcome(
        source,
        WaitCondition(
            step=retry.step,
            destination=retry.destination,
            until=NOW,
            reason="retry",
            observation=retry,
        ),
        now=NOW,
    )
    assert reduction is not None
    assert reduction.publication_state is expected_state
    assert reduction.step_state is PublicationStepState.RETRYING


def test_terminal_failure_persists_failed_state() -> None:
    source = snapshot()
    failed = observation(source, ObservationStatus.TERMINAL_FAILURE)
    reduction = reduce_planner_outcome(
        source,
        TerminalFailureOutcome(step=failed.step, observation=failed),
        now=NOW,
    )
    assert reduction is not None
    assert reduction.publication_state is PublicationState.FAILED
    assert reduction.step_state is PublicationStepState.FAILED


def test_identical_existing_acknowledgement_is_reused() -> None:
    source = snapshot()
    verified = observation(source, ObservationStatus.VERIFIED)
    existing = DurableAcknowledgementSnapshot(
        step=verified.step,
        destination=verified.destination,
        content_digest=verified.content_digest or "",
        external_reference=verified.external_reference,
        verified_at=verified.observed_at,
        context={
            "adapter_identity": verified.adapter_identity,
            "adapter_version": verified.adapter_version,
            "effect_idempotency_key": verified.effect_idempotency_key,
        },
    )
    source = replace(source, acknowledgements=(existing,))
    reduction = reduce_planner_outcome(
        source,
        TransitionOutcome(
            publication_state=PublicationState.COMMITTED,
            step=verified.step,
            destination=verified.destination,
            observation=verified,
        ),
        now=NOW,
    )
    assert reduction is not None
    assert reduction.acknowledgement is None
    assert reduction.step_state is PublicationStepState.VERIFIED


def test_final_transition_rejects_noncanonical_commit_hash() -> None:
    source = snapshot(current=10)
    acknowledgements = tuple(
        replace(acknowledgement, external_reference="B" * 40)
        if acknowledgement.step is PublicationStepName.COMMIT_RECORD
        else acknowledgement
        for acknowledgement in source.acknowledgements
    )
    source = replace(source, acknowledgements=acknowledgements)
    with pytest.raises(ValueError, match="canonical commit hash"):
        reduce_planner_outcome(
            source,
            TransitionOutcome(publication_state=PublicationState.PUBLISHED),
            now=NOW,
        )


def test_reducer_rejects_naive_time() -> None:
    with pytest.raises(ValueError, match="timezone"):
        reduce_planner_outcome(
            snapshot(),
            NoOpOutcome(reason="done"),
            now=NOW.replace(tzinfo=None),
        )


def test_reducer_rejects_unexecuted_effect_intent() -> None:
    source = snapshot()
    effect = plan_next_action(source, None, now=NOW)
    assert isinstance(effect, EffectIntent)

    with pytest.raises(ValueError, match="executed and observed"):
        reduce_planner_outcome(source, effect, now=NOW)


def test_step_transition_rejects_nonverified_observation() -> None:
    source = snapshot()
    absent = observation(source, ObservationStatus.ABSENT)
    with pytest.raises(ValueError, match="Only verified"):
        reduce_planner_outcome(
            source,
            TransitionOutcome(
                publication_state=PublicationState.COMMITTED,
                step=absent.step,
                destination=absent.destination,
                observation=absent,
            ),
            now=NOW,
        )


def test_final_transition_rejects_incomplete_protocol() -> None:
    with pytest.raises(ValueError, match="every protocol step"):
        reduce_planner_outcome(
            snapshot(current=9),
            TransitionOutcome(publication_state=PublicationState.PUBLISHED),
            now=NOW,
        )


def test_later_step_conflict_quarantines_publication() -> None:
    source = snapshot(current=4)
    conflict = observation(source, ObservationStatus.CONFLICT)
    reduction = reduce_planner_outcome(
        source,
        QuarantineOutcome(step=conflict.step, observation=conflict),
        now=NOW,
    )

    assert reduction is not None
    assert reduction.publication_state is PublicationState.QUARANTINED
