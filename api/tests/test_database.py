import asyncio
from types import TracebackType
from typing import Self, cast

import pytest
from opennosh_api.database import SqlAlchemyHealthProbe
from sqlalchemy.ext.asyncio import AsyncEngine


class SlowConnection:
    async def __aenter__(self) -> Self:
        await asyncio.sleep(10)
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


class SlowEngine:
    def connect(self) -> SlowConnection:
        return SlowConnection()


@pytest.mark.asyncio
async def test_database_probe_times_out() -> None:
    engine = cast(AsyncEngine, SlowEngine())
    probe = SqlAlchemyHealthProbe(engine, timeout_seconds=0.01)

    with pytest.raises(TimeoutError):
        await probe.check()
