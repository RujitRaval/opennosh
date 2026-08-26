from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from opennosh_api.jobs import JobLane, JobMessage, JobQueue, JobRequest
from opennosh_api.publication.orchestrator import PublicationFailpoint
from opennosh_api.publication.state import (
    EffectIntent,
    ObservationStatus,
    PublicationStepName,
)

from api.tests.workflow_testkit import (
    FORGE_TARGET,
    REQUIRED_PUBLICATION_FAILPOINTS,
    DeterministicClock,
    DeterministicIdGenerator,
    DeterministicScheduler,
    ExternalSystemKind,
    FailpointController,
    InjectedWorkflowCrash,
    PersistentExternalState,
    PersistentJobQueue,
    assert_complete_scenario_coverage,
    publication_adapter_registry,
    publication_crash_scenarios,
)

NOW = datetime(2026, 8, 25, 12, tzinfo=UTC)
NAMESPACE = UUID("8fcbab6d-892a-43fc-8d70-453718c35c7e")


def test_deterministic_clock_ids_and_scheduler_need_no_sleep() -> None:
    clock = DeterministicClock(NOW)
    ids = DeterministicIdGenerator(NAMESPACE)
    scheduler = DeterministicScheduler(clock)

    first = ids()
    checkpoint = ids.index
    second = ids()
    ids.restore(checkpoint)
    assert ids() == second
    assert first != second

    scheduler.schedule("later", NOW + timedelta(seconds=30))
    scheduler.schedule("now", NOW)
    assert tuple(item.key for item in scheduler.ready()) == ("now",)
    assert scheduler.choose("now").key == "now"
    clock.advance(timedelta(seconds=30))
    assert tuple(item.key for item in scheduler.ready()) == ("later",)


def test_clock_and_scheduler_reject_probabilistic_or_naive_inputs() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        DeterministicClock(NOW.replace(tzinfo=None))
    clock = DeterministicClock(NOW)
    with pytest.raises(ValueError, match="backwards"):
        clock.advance(timedelta(seconds=-1))
    scheduler = DeterministicScheduler(clock)
    with pytest.raises(ValueError, match="timezone"):
        scheduler.schedule("bad", NOW.replace(tzinfo=None))


@pytest.mark.asyncio
async def test_failpoint_controller_records_exact_boundary_before_crashing() -> None:
    controller = FailpointController(PublicationFailpoint.BEFORE_VERIFICATION)
    await controller(PublicationFailpoint.BEFORE_EFFECT)
    with pytest.raises(InjectedWorkflowCrash, match="before_verification"):
        await controller(PublicationFailpoint.BEFORE_VERIFICATION)
    assert controller.hits == [
        PublicationFailpoint.BEFORE_EFFECT,
        PublicationFailpoint.BEFORE_VERIFICATION,
    ]


def test_generated_matrix_covers_every_registry_step_and_required_failpoint() -> None:
    scenarios = publication_crash_scenarios(FORGE_TARGET)
    assert_complete_scenario_coverage(scenarios, FORGE_TARGET)
    assert len(scenarios) == 60
    assert {scenario.failpoint for scenario in scenarios} == set(REQUIRED_PUBLICATION_FAILPOINTS)


def test_persistent_state_supports_every_planned_external_system_and_restore() -> None:
    state = PersistentExternalState()
    for system in ExternalSystemKind:
        state.apply(
            system,
            f"key-{system.value}",
            content_digest="a" * 64,
            external_reference=f"urn:test:{system.value}",
        )
    checkpoint = state.snapshot()
    state.apply(
        ExternalSystemKind.FORGE,
        "later",
        content_digest="b" * 64,
    )
    state.restore(checkpoint)

    assert {effect.system for effect in state.effects} == set(ExternalSystemKind)
    assert state.observe(ExternalSystemKind.FORGE, "later") is None


def test_deterministic_helpers_reject_invalid_identifiers_and_scheduling() -> None:
    with pytest.raises(ValueError, match="negative"):
        DeterministicIdGenerator(NAMESPACE, index=-1)
    ids = DeterministicIdGenerator(NAMESPACE)
    with pytest.raises(ValueError, match="negative"):
        ids.restore(-1)

    scheduler = DeterministicScheduler(DeterministicClock(NOW))
    with pytest.raises(ValueError, match="empty"):
        scheduler.schedule("", NOW)
    scheduler.schedule("future", NOW + timedelta(seconds=1))
    with pytest.raises(RuntimeError, match="not ready"):
        scheduler.choose("future")


def test_persistent_external_state_rejects_conflicting_idempotency_binding() -> None:
    state = PersistentExternalState()
    state.apply(ExternalSystemKind.FORGE, "same-key", content_digest="a" * 64)
    with pytest.raises(RuntimeError, match="different external state"):
        state.apply(ExternalSystemKind.FORGE, "same-key", content_digest="b" * 64)
    assert state.apply_count(ExternalSystemKind.SEARCH, "unknown") == 0


def test_scripted_adapter_results_survive_checkpoint_and_replay_in_order() -> None:
    state = PersistentExternalState()
    state.script_observations(
        ExternalSystemKind.REGISTRY,
        "effect-key",
        [ObservationStatus.RETRYABLE_FAILURE, ObservationStatus.CONFLICT],
    )
    checkpoint = state.snapshot()

    assert (
        state.next_scripted_observation(ExternalSystemKind.REGISTRY, "effect-key")
        is ObservationStatus.RETRYABLE_FAILURE
    )
    state.restore(checkpoint)
    assert (
        state.next_scripted_observation(ExternalSystemKind.REGISTRY, "effect-key")
        is ObservationStatus.RETRYABLE_FAILURE
    )
    assert (
        state.next_scripted_observation(ExternalSystemKind.REGISTRY, "effect-key")
        is ObservationStatus.CONFLICT
    )
    assert state.next_scripted_observation(ExternalSystemKind.REGISTRY, "effect-key") is None
    with pytest.raises(ValueError, match="cannot be empty"):
        state.script_observations(ExternalSystemKind.REGISTRY, "empty", [])


@pytest.mark.parametrize("mutation", ["missing", "duplicate"])
def test_scenario_coverage_guard_rejects_registry_drift(mutation: str) -> None:
    scenarios = publication_crash_scenarios(FORGE_TARGET)
    broken = scenarios[:-1] if mutation == "missing" else (*scenarios, scenarios[0])
    with pytest.raises(AssertionError, match="matrix is incomplete"):
        assert_complete_scenario_coverage(tuple(broken), FORGE_TARGET)


@pytest.mark.asyncio
async def test_publication_adapter_emits_scripted_retry_and_conflict_observations() -> None:
    clock = DeterministicClock(NOW)
    state = PersistentExternalState()
    effect = EffectIntent(
        publication_id=UUID("11111111-1111-4111-8111-111111111111"),
        workflow_version="1.0",
        workflow_revision=0,
        step=PublicationStepName.COMMIT_RECORD,
        destination=FORGE_TARGET,
        approved_payload_digest="a" * 64,
        idempotency_key="c" * 64,
        forge_target=FORGE_TARGET,
    )
    state.script_observations(
        ExternalSystemKind.FORGE,
        effect.idempotency_key,
        [ObservationStatus.RETRYABLE_FAILURE, ObservationStatus.CONFLICT],
    )
    adapter = publication_adapter_registry(state, clock)[PublicationStepName.COMMIT_RECORD]

    retry = await adapter.observe(effect)
    conflict = await adapter.observe(effect)

    assert retry.status is ObservationStatus.RETRYABLE_FAILURE
    assert retry.retry_at == NOW + timedelta(seconds=5)
    assert conflict.status is ObservationStatus.CONFLICT
    assert conflict.code == "scripted_conflict"


@pytest.mark.asyncio
async def test_persistent_queue_matches_job_contract_dedupes_and_restores() -> None:
    clock = DeterministicClock(NOW)
    queue = PersistentJobQueue(clock)
    request = JobRequest(
        message=JobMessage(
            lane=JobLane.PUBLICATION,
            job_type="publication.wake",
            subject_id=UUID("11111111-1111-4111-8111-111111111111"),
            idempotency_key="publication-queue-testkit",
            workflow_revision=7,
        ),
        run_after=NOW + timedelta(seconds=30),
        deduplication_key="publication-queue-testkit-dedupe",
    )

    first = await queue.enqueue(None, request)  # type: ignore[arg-type]
    duplicate = await queue.enqueue(None, request)  # type: ignore[arg-type]
    checkpoint = queue.snapshot()
    assert isinstance(queue, JobQueue)
    assert first.enqueued is True and first.job_id == 1
    assert duplicate.enqueued is False and duplicate.job_id is None
    assert (await queue.health(None)).eligible == 0  # type: ignore[arg-type]

    clock.advance(timedelta(seconds=30))
    assert (await queue.health(None)).eligible == 1  # type: ignore[arg-type]
    await queue.cancel(None, 1)  # type: ignore[arg-type]
    assert (await queue.health(None)).queued == 0  # type: ignore[arg-type]

    queue.restore(checkpoint)
    assert (await queue.health(None)).queued == 1  # type: ignore[arg-type]
    assert queue.jobs[0].request.message.workflow_revision == 7
