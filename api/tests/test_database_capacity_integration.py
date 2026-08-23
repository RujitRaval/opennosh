from __future__ import annotations

import os

import pytest
from opennosh_api.capacity import ConnectionBudget
from opennosh_api.database import DatabaseIdentity, DatabasePoolMetrics, build_engine
from sqlalchemy import text
from sqlalchemy.exc import TimeoutError as SqlAlchemyTimeoutError

INTEGRATION_DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL")


@pytest.mark.skipif(
    INTEGRATION_DATABASE_URL is None, reason="PostgreSQL integration URL not configured"
)
@pytest.mark.asyncio
async def test_mixed_load_stays_inside_pool_and_recovers_after_saturation() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    budget = ConnectionBudget(
        pool_size=2,
        max_overflow=0,
        acquisition_timeout_ms=50,
        statement_timeout_ms=1000,
        max_in_flight_database_sections=2,
    )
    identity = DatabaseIdentity(deployment_id="integration", role="web")
    metrics = DatabasePoolMetrics(identity, budget.pool_size)
    engine = build_engine(
        INTEGRATION_DATABASE_URL,
        identity=identity,
        budget=budget,
        metrics=metrics,
    )
    try:
        first = await engine.connect()
        second = await engine.connect()
        with pytest.raises(SqlAlchemyTimeoutError):
            await engine.connect()
        assert metrics.snapshot()["active"] == 2

        await first.close()
        async with engine.connect() as recovered:
            assert await recovered.scalar(text("SELECT 1")) == 1
        await second.close()

        assert metrics.snapshot()["active"] == 0
        assert int(metrics.snapshot()["idle"]) <= budget.pool_size
    finally:
        await engine.dispose()
