from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid5

from opennosh_api.publication.orchestrator import PublicationFailpoint


class InjectedWorkflowCrash(RuntimeError):
    """A named, deterministic worker termination used by recovery scenarios."""


@dataclass(frozen=True, slots=True)
class DeterministicState:
    now: datetime
    id_index: int


class DeterministicClock:
    def __init__(self, now: datetime) -> None:
        self.set(now)

    def __call__(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Deterministic clock requires a timezone-aware value")
        self._now = now

    def advance(self, delta: timedelta) -> datetime:
        if delta < timedelta():
            raise ValueError("Deterministic clock cannot move backwards")
        self._now += delta
        return self._now


class DeterministicIdGenerator:
    def __init__(self, namespace: UUID, *, index: int = 0) -> None:
        if index < 0:
            raise ValueError("Identifier index cannot be negative")
        self._namespace = namespace
        self._index = index

    def __call__(self) -> UUID:
        value = uuid5(self._namespace, str(self._index))
        self._index += 1
        return value

    @property
    def index(self) -> int:
        return self._index

    def restore(self, index: int) -> None:
        if index < 0:
            raise ValueError("Identifier index cannot be negative")
        self._index = index


@dataclass(frozen=True, slots=True)
class ScheduledDecision:
    key: str
    run_at: datetime


class DeterministicScheduler:
    """Records scheduling decisions and exposes ready work without sleeping."""

    def __init__(self, clock: Callable[[], datetime]) -> None:
        self._clock = clock
        self._decisions: dict[str, ScheduledDecision] = {}

    def schedule(self, key: str, run_at: datetime) -> None:
        if not key:
            raise ValueError("Scheduled decision key cannot be empty")
        if run_at.tzinfo is None or run_at.utcoffset() is None:
            raise ValueError("Scheduled decision time must include a timezone")
        self._decisions[key] = ScheduledDecision(key=key, run_at=run_at)

    def ready(self) -> tuple[ScheduledDecision, ...]:
        now = self._clock()
        return tuple(
            sorted(
                (decision for decision in self._decisions.values() if decision.run_at <= now),
                key=lambda decision: (decision.run_at, decision.key),
            )
        )

    def choose(self, key: str) -> ScheduledDecision:
        decision = self._decisions[key]
        if decision.run_at > self._clock():
            raise RuntimeError(f"Scheduled decision is not ready: {key}")
        return self._decisions.pop(key)


class FailpointController:
    def __init__(self, armed: PublicationFailpoint | None = None) -> None:
        self.armed = armed
        self.hits: list[PublicationFailpoint] = []

    async def __call__(self, point: PublicationFailpoint) -> None:
        self.hits.append(point)
        if point is self.armed:
            raise InjectedWorkflowCrash(f"crash:{point.value}")
