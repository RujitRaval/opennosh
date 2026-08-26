from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from opennosh_api.publication.adapters import (
    MissingPublicationAdapterError,
    PublicationEffectError,
)
from opennosh_api.publication.executor import PublicationEffectExecutor
from opennosh_api.publication.state import (
    EffectIntent,
    ExternalObservation,
    ObservationStatus,
    PublicationStepName,
)

NOW = datetime(2026, 8, 25, 12, tzinfo=UTC)


class PersistentFakeAdapter:
    identity = "persistent-fake"
    version = "1"

    def __init__(self) -> None:
        self.effects: set[str] = set()
        self.apply_count = 0

    async def apply(self, intent: EffectIntent) -> None:
        self.apply_count += 1
        self.effects.add(intent.idempotency_key)

    async def observe(self, intent: EffectIntent) -> ExternalObservation:
        verified = intent.idempotency_key in self.effects
        return ExternalObservation(
            step=intent.step,
            status=ObservationStatus.VERIFIED if verified else ObservationStatus.ABSENT,
            observed_at=NOW,
            destination=intent.destination,
            effect_idempotency_key=intent.idempotency_key,
            adapter_identity=self.identity,
            adapter_version=self.version,
            content_digest="a" * 64 if verified else None,
            external_reference="b" * 40 if verified else None,
        )


def intent() -> EffectIntent:
    return EffectIntent(
        publication_id=UUID("11111111-1111-4111-8111-111111111111"),
        workflow_version="1.0",
        workflow_revision=0,
        step=PublicationStepName.COMMIT_RECORD,
        destination="https://forge.example/opennosh/packs",
        approved_payload_digest="a" * 64,
        idempotency_key="c" * 64,
        forge_target="https://forge.example/opennosh/packs",
    )


@pytest.mark.asyncio
async def test_executor_observes_before_effect_and_never_repeats_verified_effect() -> None:
    adapter = PersistentFakeAdapter()
    executor = PublicationEffectExecutor({PublicationStepName.COMMIT_RECORD: adapter})

    first = await executor.execute(intent(), now=NOW)
    second = await executor.execute(intent(), now=NOW)

    assert first.effect_attempted is True
    assert second.effect_attempted is False
    assert adapter.apply_count == 1
    assert second.observation.status is ObservationStatus.VERIFIED


@pytest.mark.asyncio
async def test_crash_after_effect_recovers_by_observation_without_duplicate() -> None:
    adapter = PersistentFakeAdapter()
    executor = PublicationEffectExecutor({PublicationStepName.COMMIT_RECORD: adapter})

    async def crash_after_effect() -> None:
        raise RuntimeError("crash after effect")

    with pytest.raises(RuntimeError, match="crash after effect"):
        await executor.execute(intent(), now=NOW, after_effect=crash_after_effect)

    recovered = await executor.execute(intent(), now=NOW)

    assert recovered.effect_attempted is False
    assert recovered.observation.status is ObservationStatus.VERIFIED
    assert adapter.apply_count == 1


class EventuallyConsistentAdapter(PersistentFakeAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.observation_count = 0

    async def observe(self, effect: EffectIntent) -> ExternalObservation:
        self.observation_count += 1
        if self.observation_count < 3:
            return ExternalObservation(
                step=effect.step,
                status=ObservationStatus.ABSENT,
                observed_at=NOW,
                destination=effect.destination,
                effect_idempotency_key=effect.idempotency_key,
                adapter_identity=self.identity,
                adapter_version=self.version,
            )
        return await super().observe(effect)


@pytest.mark.asyncio
async def test_not_yet_observable_effect_is_retried_without_reapplying() -> None:
    adapter = EventuallyConsistentAdapter()
    executor = PublicationEffectExecutor({PublicationStepName.COMMIT_RECORD: adapter})

    first = await executor.execute(intent(), now=NOW)
    second = await executor.execute(intent(), now=NOW)

    assert first.observation.status is ObservationStatus.RETRYABLE_FAILURE
    assert first.observation.retry_at is not None
    assert second.observation.status is ObservationStatus.VERIFIED
    assert adapter.apply_count == 1


class SequenceAdapter(PersistentFakeAdapter):
    def __init__(self, observations: list[ExternalObservation]) -> None:
        super().__init__()
        self.observations = observations

    async def observe(self, effect: EffectIntent) -> ExternalObservation:
        if self.observations:
            return self.observations.pop(0)
        return await super().observe(effect)


def external_observation(
    status: ObservationStatus,
    *,
    retry_at: datetime | None = None,
) -> ExternalObservation:
    effect = intent()
    return ExternalObservation(
        step=effect.step,
        status=status,
        observed_at=NOW,
        destination=effect.destination,
        effect_idempotency_key=effect.idempotency_key,
        adapter_identity="persistent-fake",
        adapter_version="1",
        content_digest="a" * 64 if status is ObservationStatus.VERIFIED else None,
        retry_at=retry_at,
        code="scripted",
    )


def test_executor_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError, match="positive"):
        PublicationEffectExecutor({}, effect_timeout=timedelta())


@pytest.mark.asyncio
async def test_executor_rejects_naive_time_and_missing_adapter() -> None:
    executor = PublicationEffectExecutor({})
    with pytest.raises(ValueError, match="timezone"):
        await executor.execute(intent(), now=NOW.replace(tzinfo=None))
    with pytest.raises(MissingPublicationAdapterError):
        await executor.execute(intent(), now=NOW)


@pytest.mark.parametrize(
    ("status", "retry_at"),
    [
        (ObservationStatus.CONFLICT, None),
        (ObservationStatus.TERMINAL_FAILURE, None),
        (ObservationStatus.RETRYABLE_FAILURE, NOW + timedelta(minutes=1)),
    ],
)
@pytest.mark.asyncio
async def test_preobserved_non_due_outcomes_skip_effect(
    status: ObservationStatus,
    retry_at: datetime | None,
) -> None:
    adapter = SequenceAdapter([external_observation(status, retry_at=retry_at)])
    result = await PublicationEffectExecutor({PublicationStepName.COMMIT_RECORD: adapter}).execute(
        intent(), now=NOW
    )
    assert result.effect_attempted is False
    assert adapter.apply_count == 0


@pytest.mark.asyncio
async def test_due_retry_attempts_effect_and_verifies() -> None:
    adapter = SequenceAdapter(
        [
            external_observation(ObservationStatus.RETRYABLE_FAILURE, retry_at=NOW),
            external_observation(ObservationStatus.VERIFIED),
        ]
    )
    result = await PublicationEffectExecutor({PublicationStepName.COMMIT_RECORD: adapter}).execute(
        intent(), now=NOW
    )
    assert result.observation.status is ObservationStatus.VERIFIED
    assert adapter.apply_count == 1


class ErrorAdapter(SequenceAdapter):
    async def apply(self, effect: EffectIntent) -> None:
        raise PublicationEffectError(
            status=ObservationStatus.CONFLICT,
            code="external_conflict",
        )


@pytest.mark.asyncio
async def test_typed_effect_error_becomes_durable_observation() -> None:
    adapter = ErrorAdapter(
        [
            external_observation(ObservationStatus.ABSENT),
            external_observation(ObservationStatus.ABSENT),
        ]
    )
    result = await PublicationEffectExecutor({PublicationStepName.COMMIT_RECORD: adapter}).execute(
        intent(), now=NOW
    )
    assert result.observation.status is ObservationStatus.CONFLICT
    assert result.observation.code == "external_conflict"


class SlowAdapter(SequenceAdapter):
    async def apply(self, effect: EffectIntent) -> None:
        await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_effect_timeout_becomes_retryable_observation() -> None:
    adapter = SlowAdapter(
        [
            external_observation(ObservationStatus.ABSENT),
            external_observation(ObservationStatus.ABSENT),
        ]
    )
    result = await PublicationEffectExecutor(
        {PublicationStepName.COMMIT_RECORD: adapter},
        effect_timeout=timedelta(milliseconds=1),
    ).execute(intent(), now=NOW)
    assert result.observation.status is ObservationStatus.RETRYABLE_FAILURE
    assert result.observation.code == "effect_timeout"
    assert result.observation.retry_at == NOW + timedelta(milliseconds=1, seconds=5)


@pytest.mark.parametrize(
    "overrides",
    [
        {"step": PublicationStepName.COPY_COMMIT},
        {"destination": "urn:other"},
        {"effect_idempotency_key": "d" * 64},
        {"adapter_identity": "other-adapter"},
        {"adapter_version": "2"},
    ],
)
@pytest.mark.asyncio
async def test_executor_rejects_foreign_adapter_observations(
    overrides: dict[str, object],
) -> None:
    foreign = replace(external_observation(ObservationStatus.VERIFIED), **overrides)
    adapter = SequenceAdapter([foreign])
    executor = PublicationEffectExecutor({PublicationStepName.COMMIT_RECORD: adapter})

    with pytest.raises(ValueError, match="different|does not match"):
        await executor.execute(intent(), now=NOW)


class SlowInitialObserveAdapter(PersistentFakeAdapter):
    async def observe(self, effect: EffectIntent) -> ExternalObservation:
        await asyncio.sleep(0.02)
        return await super().observe(effect)


class SlowVerificationAdapter(PersistentFakeAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.observation_count = 0

    async def observe(self, effect: EffectIntent) -> ExternalObservation:
        self.observation_count += 1
        if self.observation_count > 1:
            await asyncio.sleep(0.02)
        return await super().observe(effect)


@pytest.mark.parametrize(
    "adapter",
    [SlowInitialObserveAdapter(), SlowVerificationAdapter()],
)
@pytest.mark.asyncio
async def test_whole_cycle_timeout_bounds_observation_stages(
    adapter: PersistentFakeAdapter,
) -> None:
    result = await PublicationEffectExecutor(
        {PublicationStepName.COMMIT_RECORD: adapter},
        effect_timeout=timedelta(milliseconds=1),
    ).execute(intent(), now=NOW)

    assert result.observation.status is ObservationStatus.RETRYABLE_FAILURE
    assert result.observation.code == "effect_timeout"


@pytest.mark.asyncio
async def test_whole_cycle_timeout_bounds_after_effect_callback() -> None:
    adapter = PersistentFakeAdapter()

    async def slow_callback() -> None:
        await asyncio.sleep(0.02)

    result = await PublicationEffectExecutor(
        {PublicationStepName.COMMIT_RECORD: adapter},
        effect_timeout=timedelta(milliseconds=1),
    ).execute(intent(), now=NOW, after_effect=slow_callback)

    assert result.observation.status is ObservationStatus.RETRYABLE_FAILURE
    assert adapter.apply_count == 1
