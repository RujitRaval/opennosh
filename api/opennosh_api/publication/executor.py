from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TypeAlias

from opennosh_api.publication.adapters import (
    MissingPublicationAdapterError,
    PublicationAdapterRegistry,
    PublicationEffectAdapter,
    PublicationEffectError,
)
from opennosh_api.publication.state import (
    EffectIntent,
    ExternalObservation,
    ObservationStatus,
)

TimeoutFactory: TypeAlias = Callable[[float | None], AbstractAsyncContextManager[object]]


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    effect_attempted: bool
    observation: ExternalObservation
    adapter_identity: str
    adapter_version: str


class PublicationEffectExecutor:
    """Observe first, perform at most one effect, then independently observe again."""

    def __init__(
        self,
        adapters: PublicationAdapterRegistry,
        *,
        effect_timeout: timedelta = timedelta(seconds=20),
        timeout_factory: TimeoutFactory = asyncio.timeout,
    ) -> None:
        if effect_timeout <= timedelta():
            raise ValueError("Publication effect timeout must be positive")
        self._adapters = dict(adapters)
        self._effect_timeout = effect_timeout
        self._timeout_factory = timeout_factory

    async def execute(
        self,
        intent: EffectIntent,
        *,
        now: datetime,
        after_effect: Callable[[], Awaitable[None]] | None = None,
        before_verification: Callable[[], Awaitable[None]] | None = None,
    ) -> ExecutionResult:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Executor time must include a timezone")
        adapter = self._adapters.get(intent.step)
        if adapter is None:
            raise MissingPublicationAdapterError(
                f"No adapter registered for publication step {intent.step.value}"
            )
        try:
            async with self._timeout_factory(self._effect_timeout.total_seconds()):
                return await self._execute_bounded(
                    intent,
                    adapter,
                    now=now,
                    after_effect=after_effect,
                    before_verification=before_verification,
                )
        except TimeoutError:
            observation = ExternalObservation(
                step=intent.step,
                status=ObservationStatus.RETRYABLE_FAILURE,
                observed_at=now,
                destination=intent.destination,
                effect_idempotency_key=intent.idempotency_key,
                adapter_identity=adapter.identity,
                adapter_version=adapter.version,
                retry_at=now + self._effect_timeout + timedelta(seconds=5),
                code="effect_timeout",
            )
            return ExecutionResult(
                effect_attempted=True,
                observation=observation,
                adapter_identity=adapter.identity,
                adapter_version=adapter.version,
            )

    async def _execute_bounded(
        self,
        intent: EffectIntent,
        adapter: PublicationEffectAdapter,
        *,
        now: datetime,
        after_effect: Callable[[], Awaitable[None]] | None,
        before_verification: Callable[[], Awaitable[None]] | None,
    ) -> ExecutionResult:
        before = await adapter.observe(intent)
        self._validate_observation(intent, before, adapter.identity, adapter.version)
        if before.status not in {
            ObservationStatus.ABSENT,
            ObservationStatus.RETRYABLE_FAILURE,
        } or (
            before.status is ObservationStatus.RETRYABLE_FAILURE
            and before.retry_at is not None
            and before.retry_at > now
        ):
            return ExecutionResult(
                effect_attempted=False,
                observation=before,
                adapter_identity=adapter.identity,
                adapter_version=adapter.version,
            )

        effect_error: PublicationEffectError | None = None
        try:
            await adapter.apply(intent)
        except PublicationEffectError as error:
            effect_error = error
        if after_effect is not None:
            await after_effect()
        if before_verification is not None:
            await before_verification()

        after = await adapter.observe(intent)
        self._validate_observation(intent, after, adapter.identity, adapter.version)
        if after.status is ObservationStatus.ABSENT:
            if effect_error is None:
                effect_error = PublicationEffectError(
                    status=ObservationStatus.RETRYABLE_FAILURE,
                    code="effect_not_yet_observable",
                    retry_at=now + timedelta(seconds=5),
                )
            after = ExternalObservation(
                step=intent.step,
                status=effect_error.status,
                observed_at=now,
                destination=intent.destination,
                effect_idempotency_key=intent.idempotency_key,
                adapter_identity=adapter.identity,
                adapter_version=adapter.version,
                retry_at=effect_error.retry_at,
                code=effect_error.code,
                context=effect_error.context,
            )
        return ExecutionResult(
            effect_attempted=True,
            observation=after,
            adapter_identity=adapter.identity,
            adapter_version=adapter.version,
        )

    @staticmethod
    def _validate_observation(
        intent: EffectIntent,
        observation: ExternalObservation,
        adapter_identity: str | None = None,
        adapter_version: str | None = None,
    ) -> None:
        if observation.step is not intent.step:
            raise ValueError("Adapter returned an observation for a different step")
        if observation.destination != intent.destination:
            raise ValueError("Adapter returned an observation for a different destination")
        if observation.effect_idempotency_key != intent.idempotency_key:
            raise ValueError("Adapter returned an observation for a different effect key")
        if adapter_identity is not None and observation.adapter_identity != adapter_identity:
            raise ValueError("Adapter observation identity does not match the registered adapter")
        if adapter_version is not None and observation.adapter_version != adapter_version:
            raise ValueError("Adapter observation version does not match the registered adapter")
