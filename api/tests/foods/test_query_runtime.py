import asyncio

import pytest
from opennosh_api.foods.query_runtime import FoodSearchQueryRuntime, FoodSearchQueueFullError


def test_duplicate_requests_share_work_and_different_queries_serialize() -> None:
    async def scenario() -> None:
        runtime = FoodSearchQueryRuntime()
        calls = active = peak = 0

        async def execute():
            nonlocal calls, active, peak
            calls += 1
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return []

        await asyncio.gather(*(runtime.run(key, execute) for key in ("rice", "rice", "apple")))
        assert calls == 2
        assert peak == 1

    asyncio.run(scenario())


def test_snapshot_keys_expiry_and_lru_bound_prevent_indefinite_reuse() -> None:
    async def scenario() -> None:
        now = 0.0
        runtime = FoodSearchQueryRuntime(capacity=2, ttl_seconds=10, clock=lambda: now)
        calls = 0

        async def execute():
            nonlocal calls
            calls += 1
            return []

        for key in ("snapshot1-rice", "snapshot1-apple", "snapshot1-rice", "snapshot2-rice"):
            await runtime.run(key, execute)
        assert calls == 3
        await runtime.run("snapshot1-apple", execute)
        assert calls == 4  # evicted, even though not expired
        now = 11
        await runtime.run("snapshot2-rice", execute)
        assert calls == 5

    asyncio.run(scenario())


def test_failure_and_cancellation_release_capacity_without_caching() -> None:
    async def scenario() -> None:
        runtime = FoodSearchQueryRuntime()
        entered = asyncio.Event()

        async def fail():
            raise ValueError("query failed")

        with pytest.raises(ValueError):
            await runtime.run("rice", fail)

        async def blocked():
            entered.set()
            await asyncio.Event().wait()
            return []

        task = asyncio.create_task(runtime.run("rice", blocked))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        async def success():
            return []

        assert await runtime.run("rice", success) == []

    asyncio.run(scenario())


def test_queue_wait_is_bounded_and_cancelled_waiters_do_not_leak() -> None:
    async def scenario() -> None:
        runtime = FoodSearchQueryRuntime(queue_timeout_seconds=0.01, max_waiters=1)
        entered = asyncio.Event()
        finish = asyncio.Event()

        async def blocked():
            entered.set()
            await finish.wait()
            return []

        task = asyncio.create_task(runtime.run("rice", blocked))
        await entered.wait()
        with pytest.raises(FoodSearchQueueFullError):
            await runtime.run("apple", blocked)
        finish.set()
        await task
        assert await runtime.run("apple", blocked) == []

    asyncio.run(scenario())


def test_full_queue_rejects_and_cancelled_waiter_frees_its_slot() -> None:
    async def scenario() -> None:
        runtime = FoodSearchQueryRuntime(max_waiters=1)
        entered = asyncio.Event()
        finish = asyncio.Event()

        async def blocked():
            entered.set()
            await finish.wait()
            return []

        active = asyncio.create_task(runtime.run("rice", blocked))
        await entered.wait()
        queued = asyncio.create_task(runtime.run("apple", blocked))
        await asyncio.sleep(0)
        with pytest.raises(FoodSearchQueueFullError):
            await runtime.run("chicken", blocked)
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        replacement = asyncio.create_task(runtime.run("chicken", blocked))
        await asyncio.sleep(0)
        finish.set()
        await asyncio.gather(active, replacement)

    asyncio.run(scenario())
