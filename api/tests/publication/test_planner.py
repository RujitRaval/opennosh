from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st
from opennosh_api.publication.adapters import PublicationEffectError
from opennosh_api.publication.planner import plan_next_action
from opennosh_api.publication.state import (
    DurableAcknowledgementSnapshot,
    EffectIntent,
    ExternalObservation,
    NoOpOutcome,
    ObservationStatus,
    PublicationSnapshot,
    PublicationState,
    PublicationStepName,
    PublicationStepSnapshot,
    PublicationStepState,
    QuarantineOutcome,
    TerminalFailureOutcome,
    TransitionOutcome,
    WaitCondition,
    effect_idempotency_key,
    publication_protocol,
)

NOW = datetime(2026, 8, 25, 12, tzinfo=UTC)
PUBLICATION_ID = UUID("11111111-1111-4111-8111-111111111111")
DIGEST = "a" * 64
FORGE = "https://forge.example/opennosh/packs"


def snapshot(
    *,
    current: int = 0,
    current_state: PublicationStepState = PublicationStepState.PENDING,
    publication_state: PublicationState = PublicationState.PENDING,
    revision: int = 0,
) -> PublicationSnapshot:
    definitions = publication_protocol(FORGE)
    steps = []
    acknowledgements = []
    for definition in definitions:
        state = (
            PublicationStepState.VERIFIED
            if definition.ordinal < current
            else current_state
            if definition.ordinal == current
            else PublicationStepState.PENDING
        )
        if current >= len(definitions):
            state = PublicationStepState.VERIFIED
        steps.append(
            PublicationStepSnapshot(
                name=definition.name,
                ordinal=definition.ordinal,
                destination=definition.destination,
                state=state,
            )
        )
        if state is PublicationStepState.VERIFIED:
            acknowledgements.append(
                DurableAcknowledgementSnapshot(
                    step=definition.name,
                    destination=definition.destination,
                    content_digest=DIGEST,
                    external_reference=(
                        "b" * 40 if definition.name is PublicationStepName.COMMIT_RECORD else None
                    ),
                    verified_at=NOW,
                )
            )
    return PublicationSnapshot(
        publication_id=PUBLICATION_ID,
        workflow_version="1.0",
        workflow_revision=revision,
        state=publication_state,
        pack_id="commons",
        record_id="lentils",
        approved_payload_digest=DIGEST,
        expected_base_commit="c" * 40,
        required_checks=("schema",),
        forge_target=FORGE,
        steps=tuple(steps),
        acknowledgements=tuple(acknowledgements),
    )


def observation(
    source: PublicationSnapshot,
    status: ObservationStatus,
    *,
    retry_at: datetime | None = None,
) -> ExternalObservation:
    step = next(item for item in source.steps if item.state is not PublicationStepState.VERIFIED)
    return ExternalObservation(
        step=step.name,
        status=status,
        observed_at=NOW,
        destination=step.destination,
        effect_idempotency_key=effect_idempotency_key(
            publication_id=source.publication_id,
            workflow_version=source.workflow_version,
            step=step.name,
            destination=step.destination,
            approved_payload_digest=source.approved_payload_digest,
        ),
        adapter_identity="fake",
        adapter_version="1",
        content_digest=DIGEST if status is ObservationStatus.VERIFIED else None,
        external_reference="b" * 40 if status is ObservationStatus.VERIFIED else None,
        retry_at=retry_at,
        code="test",
    )


@pytest.mark.parametrize(
    ("status", "expected_type"),
    [
        (ObservationStatus.ABSENT, EffectIntent),
        (ObservationStatus.VERIFIED, TransitionOutcome),
        (ObservationStatus.CONFLICT, QuarantineOutcome),
        (ObservationStatus.TERMINAL_FAILURE, TerminalFailureOutcome),
    ],
)
def test_exhaustive_observation_transition_table(
    status: ObservationStatus,
    expected_type: type[object],
) -> None:
    source = snapshot()
    assert isinstance(
        plan_next_action(source, observation(source, status), now=NOW),
        expected_type,
    )


def test_retryable_observation_always_crosses_the_durable_wait_boundary() -> None:
    source = snapshot(revision=7)
    future = observation(
        source,
        ObservationStatus.RETRYABLE_FAILURE,
        retry_at=NOW + timedelta(minutes=1),
    )
    wait = plan_next_action(source, future, now=NOW)
    assert isinstance(wait, WaitCondition)

    ready = plan_next_action(source, replace(future, retry_at=NOW), now=NOW)
    immediate = plan_next_action(source, replace(future, retry_at=None), now=NOW)
    assert isinstance(ready, WaitCondition)
    assert isinstance(immediate, WaitCondition)
    assert ready.until == NOW
    assert immediate.until == NOW


def test_live_lease_blocks_later_steps() -> None:
    source = snapshot()
    leased_step = replace(
        source.steps[0],
        state=PublicationStepState.LEASED,
        lease_token=UUID("22222222-2222-4222-8222-222222222222"),
        lease_expires_at=NOW + timedelta(seconds=10),
    )
    source = replace(source, steps=(leased_step, *source.steps[1:]))

    outcome = plan_next_action(source, None, now=NOW)

    assert isinstance(outcome, WaitCondition)
    assert outcome.step is PublicationStepName.COMMIT_RECORD


@pytest.mark.parametrize(
    "state",
    [
        PublicationState.BLOCKED,
        PublicationState.FAILED,
        PublicationState.PUBLISHED,
        PublicationState.PUBLISH_BLOCKED,
        PublicationState.QUARANTINED,
    ],
)
def test_terminal_publication_states_are_noops(state: PublicationState) -> None:
    assert isinstance(
        plan_next_action(snapshot(publication_state=state), None, now=NOW),
        NoOpOutcome,
    )


def test_all_receipt_gated_steps_transition_to_published() -> None:
    source = snapshot(current=len(publication_protocol(FORGE)))
    outcome = plan_next_action(source, None, now=NOW)

    assert outcome == TransitionOutcome(publication_state=PublicationState.PUBLISHED)


@given(revision=st.integers(min_value=0, max_value=1_000_000))
def test_planner_is_deterministic_and_effect_key_ignores_cas_revision(revision: int) -> None:
    source = snapshot(revision=revision)

    first = plan_next_action(source, None, now=NOW)
    second = plan_next_action(source, None, now=NOW)

    assert first == second
    assert isinstance(first, EffectIntent)
    assert first.idempotency_key == effect_idempotency_key(
        publication_id=PUBLICATION_ID,
        workflow_version="1.0",
        step=PublicationStepName.COMMIT_RECORD,
        destination=FORGE,
        approved_payload_digest=DIGEST,
    )


def test_planner_rejects_naive_time_and_foreign_observation() -> None:
    source = snapshot()
    with pytest.raises(ValueError, match="timezone"):
        plan_next_action(source, None, now=NOW.replace(tzinfo=None))

    foreign = replace(observation(source, ObservationStatus.VERIFIED), destination="elsewhere")
    with pytest.raises(ValueError, match="does not belong"):
        plan_next_action(source, foreign, now=NOW)


@pytest.mark.parametrize(
    "step_state",
    [PublicationStepState.BLOCKED, PublicationStepState.FAILED],
)
def test_blocked_and_failed_steps_do_not_progress(
    step_state: PublicationStepState,
) -> None:
    outcome = plan_next_action(snapshot(current_state=step_state), None, now=NOW)
    assert isinstance(outcome, NoOpOutcome)


def test_expired_lease_is_reclaimable() -> None:
    source = snapshot()
    leased_step = replace(
        source.steps[0],
        state=PublicationStepState.LEASED,
        lease_token=UUID("22222222-2222-4222-8222-222222222222"),
        lease_expires_at=NOW - timedelta(seconds=1),
    )
    source = replace(source, steps=(leased_step, *source.steps[1:]))

    assert isinstance(plan_next_action(source, None, now=NOW), EffectIntent)


@pytest.mark.parametrize(
    ("current", "expected_state"),
    [
        (0, PublicationState.COMMITTED),
        (2, PublicationState.COMMITTED),
        (3, PublicationState.SIGNED),
        (9, PublicationState.SIGNED),
    ],
)
def test_verified_steps_preserve_publication_phase(
    current: int,
    expected_state: PublicationState,
) -> None:
    source = snapshot(current=current)
    outcome = plan_next_action(
        source,
        observation(source, ObservationStatus.VERIFIED),
        now=NOW,
    )
    assert isinstance(outcome, TransitionOutcome)
    assert outcome.publication_state is expected_state


def test_snapshot_rejects_noncanonical_destination() -> None:
    source = snapshot()
    steps = (replace(source.steps[0], destination="legacy:commit_record"), *source.steps[1:])
    with pytest.raises(ValueError, match="canonical protocol"):
        replace(source, steps=steps)


def test_leased_step_requires_fencing_token_and_expiry() -> None:
    definition = publication_protocol(FORGE)[0]
    with pytest.raises(ValueError, match="token and expiry"):
        PublicationStepSnapshot(
            name=definition.name,
            ordinal=definition.ordinal,
            destination=definition.destination,
            state=PublicationStepState.LEASED,
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"ordinal": -1},
        {"destination": ""},
        {"step_version": 0},
        {"attempt_count": -1},
    ],
)
def test_step_metadata_guards(overrides: dict[str, object]) -> None:
    definition = publication_protocol(FORGE)[0]
    values: dict[str, object] = {
        "name": definition.name,
        "ordinal": definition.ordinal,
        "destination": definition.destination,
        "state": PublicationStepState.PENDING,
    }
    values.update(overrides)
    with pytest.raises(ValueError):
        PublicationStepSnapshot(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "overrides",
    [
        {"destination": ""},
        {"content_digest": "short"},
        {"verified_at": NOW.replace(tzinfo=None)},
    ],
)
def test_acknowledgement_proof_guards(overrides: dict[str, object]) -> None:
    values: dict[str, object] = {
        "step": PublicationStepName.COMMIT_RECORD,
        "destination": FORGE,
        "content_digest": DIGEST,
        "external_reference": "b" * 40,
        "verified_at": NOW,
    }
    values.update(overrides)
    with pytest.raises(ValueError):
        DurableAcknowledgementSnapshot(**values)  # type: ignore[arg-type]


def test_snapshot_revision_digest_order_and_proof_guards() -> None:
    source = snapshot()
    with pytest.raises(ValueError, match="revision"):
        replace(source, workflow_revision=-1)
    with pytest.raises(ValueError, match="digest"):
        replace(source, approved_payload_digest="short")
    with pytest.raises(ValueError, match="ordered ordinals"):
        replace(source, steps=tuple(reversed(source.steps)))

    verified_first = replace(source.steps[0], state=PublicationStepState.VERIFIED)
    with pytest.raises(ValueError, match="durable acknowledgement"):
        replace(source, steps=(verified_first, *source.steps[1:]))


def test_observation_trust_boundary_guards() -> None:
    source = snapshot()
    verified = observation(source, ObservationStatus.VERIFIED)
    for overrides in (
        {"observed_at": NOW.replace(tzinfo=None)},
        {"content_digest": "short"},
        {"effect_idempotency_key": "short"},
        {"adapter_identity": ""},
        {"adapter_version": ""},
    ):
        values: dict[str, object] = {
            "step": verified.step,
            "status": verified.status,
            "observed_at": verified.observed_at,
            "destination": verified.destination,
            "effect_idempotency_key": verified.effect_idempotency_key,
            "adapter_identity": verified.adapter_identity,
            "adapter_version": verified.adapter_version,
            "content_digest": verified.content_digest,
        }
        values.update(overrides)
        with pytest.raises(ValueError):
            ExternalObservation(**values)  # type: ignore[arg-type]


def test_effect_error_rejects_nonfailure_status() -> None:
    with pytest.raises(ValueError, match="failure observation"):
        PublicationEffectError(status=ObservationStatus.ABSENT, code="invalid")
