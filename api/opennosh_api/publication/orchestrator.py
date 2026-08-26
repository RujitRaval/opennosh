from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import TypeAlias

from opennosh_api.jobs.contracts import JobMessage
from opennosh_api.publication.executor import PublicationEffectExecutor
from opennosh_api.publication.planner import plan_next_action
from opennosh_api.publication.reducer import reduce_planner_outcome
from opennosh_api.publication.repository import PostgresPublicationRepository
from opennosh_api.publication.state import (
    EffectIntent,
    PlannerOutcome,
    TransitionOutcome,
)


class PublicationFailpoint(StrEnum):
    BEFORE_EFFECT = "before_effect"
    AFTER_EFFECT = "after_effect"
    AFTER_VERIFICATION = "after_verification"
    BEFORE_REDUCER = "before_reducer"
    AFTER_REDUCER = "after_reducer"


FailpointHook: TypeAlias = Callable[[PublicationFailpoint], Awaitable[None]]


async def _no_failpoint(_: PublicationFailpoint) -> None:
    return None


class PublicationOrchestrator:
    """Coordinate one planner decision, one lease, and at most one external effect."""

    def __init__(
        self,
        repository: PostgresPublicationRepository,
        executor: PublicationEffectExecutor,
        *,
        owner: str,
        clock: Callable[[], datetime] | None = None,
        failpoint: FailpointHook | None = None,
    ) -> None:
        if not owner:
            raise ValueError("Publication orchestrator owner cannot be empty")
        self._repository = repository
        self._executor = executor
        self._owner = owner
        self._clock = clock or (lambda: datetime.now(UTC))
        self._failpoint = failpoint or _no_failpoint

    async def process(
        self,
        message: JobMessage,
        *,
        queue_job_id: int,
    ) -> PlannerOutcome:
        snapshot = await self._repository.load_or_initialize(message.subject_id)
        if (
            message.workflow_revision is not None
            and message.workflow_revision > snapshot.workflow_revision
        ):
            raise RuntimeError("Publication wake-up references a future workflow revision")

        now = self._now()
        outcome = plan_next_action(snapshot, None, now=now)
        if isinstance(outcome, TransitionOutcome) and outcome.step is None:
            reduction = reduce_planner_outcome(snapshot, outcome, now=now)
            if reduction is None:
                return outcome
            await self._failpoint(PublicationFailpoint.BEFORE_REDUCER)
            await self._repository.apply_unleased(
                snapshot.publication_id,
                reduction,
                now=now,
            )
            await self._failpoint(PublicationFailpoint.AFTER_REDUCER)
            return outcome
        if not isinstance(outcome, EffectIntent):
            return outcome

        lease = await self._repository.claim(
            outcome,
            queue_job_id=queue_job_id,
            owner=self._owner,
            now=now,
        )
        if lease is None:
            return outcome

        await self._failpoint(PublicationFailpoint.BEFORE_EFFECT)

        async def after_effect() -> None:
            await self._failpoint(PublicationFailpoint.AFTER_EFFECT)

        execution = await self._executor.execute(
            outcome,
            now=self._now(),
            after_effect=after_effect,
        )
        await self._failpoint(PublicationFailpoint.AFTER_VERIFICATION)
        observed_outcome = plan_next_action(
            lease.snapshot,
            execution.observation,
            now=self._now(),
        )
        reduction = reduce_planner_outcome(
            lease.snapshot,
            observed_outcome,
            now=self._now(),
        )
        if reduction is None:
            return observed_outcome
        await self._failpoint(PublicationFailpoint.BEFORE_REDUCER)
        await self._repository.apply(lease, reduction, now=self._now())
        await self._failpoint(PublicationFailpoint.AFTER_REDUCER)
        return observed_outcome

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Publication orchestrator clock must include a timezone")
        return value
