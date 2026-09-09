from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from opennosh_api.foods import warming
from opennosh_api.settings import Settings


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError("private database details"), TimeoutError()])
async def test_warmer_closes_failed_sessions_and_redacts_errors(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: Exception,
) -> None:
    closed = []

    @asynccontextmanager
    async def sessions():
        try:
            yield AsyncMock()
        finally:
            closed.append(True)

    monkeypatch.setattr(warming, "_fresh_snapshot", AsyncMock(side_effect=failure))
    assert not await warming.warm_food_search_once(cast(Any, sessions), Settings(_env_file=None))
    assert closed == [True]
    assert type(failure).__name__ in caplog.text
    assert "private database details" not in caplog.text


@pytest.mark.asyncio
async def test_warmer_does_not_swallow_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def sessions():
        yield AsyncMock()

    monkeypatch.setattr(warming, "_fresh_snapshot", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await warming.warm_food_search_once(cast(Any, sessions), Settings(_env_file=None))


@pytest.mark.asyncio
async def test_warmer_retries_after_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = []

    async def warm(*args: object) -> bool:
        attempts.append(True)
        return len(attempts) > 1

    async def sleep(seconds: float) -> None:
        assert seconds == 30.0
        if len(attempts) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(warming, "warm_food_search_once", warm)
    monkeypatch.setattr(warming.asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        await warming.run_food_search_warmer(cast(Any, None), Settings(_env_file=None))
    assert len(attempts) == 2
