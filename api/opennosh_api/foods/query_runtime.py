"""Bound expensive public catalogue work within one API process."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from time import monotonic

from sqlalchemy.engine import RowMapping


class FoodSearchQueueFullError(RuntimeError):
    """The bounded search queue could not admit another query in time."""


class FoodSearchQueryRuntime:
    """Serialize cache misses; immutable snapshot rows never contain personal foods."""

    def __init__(
        self,
        *,
        capacity: int = 128,
        ttl_seconds: float = 60,
        queue_timeout_seconds: float = 3,
        max_waiters: int = 8,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._capacity = capacity
        self._ttl_seconds = ttl_seconds
        self._queue_timeout_seconds = queue_timeout_seconds
        self._max_waiters = max_waiters
        self._clock = clock
        self._lock = asyncio.Lock()
        self._waiters = 0
        self._cache: OrderedDict[str, tuple[float, list[RowMapping]]] = OrderedDict()

    def _get(self, key: str) -> list[RowMapping] | None:
        cached = self._cache.get(key)
        if cached is None:
            return None
        expires_at, rows = cached
        if expires_at <= self._clock():
            del self._cache[key]
            return None
        self._cache.move_to_end(key)
        return list(rows)

    async def run(
        self, key: str, execute: Callable[[], Awaitable[list[RowMapping]]]
    ) -> list[RowMapping]:
        cached = self._get(key)
        if cached is not None:
            return cached
        if self._waiters >= self._max_waiters:
            raise FoodSearchQueueFullError
        self._waiters += 1
        try:
            try:
                async with asyncio.timeout(self._queue_timeout_seconds):
                    await self._lock.acquire()
            except TimeoutError as error:
                raise FoodSearchQueueFullError from error
        finally:
            self._waiters -= 1
        try:
            # Identical concurrent requests reuse the first completed execution.
            cached = self._get(key)
            if cached is not None:
                return cached
            rows = await execute()
            self._cache[key] = (self._clock() + self._ttl_seconds, list(rows))
            self._cache.move_to_end(key)
            while len(self._cache) > self._capacity:
                self._cache.popitem(last=False)
            return rows
        finally:
            self._lock.release()
