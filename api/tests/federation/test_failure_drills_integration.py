from __future__ import annotations

import asyncio
import os

import asyncpg
import pytest
from opennosh_api.federation.drills import (
    DrillCaseContract,
    FailureDrillCase,
    exercise_controlled_failure,
    load_failure_drill_contract,
)
from opennosh_api.jobs.worker import asyncpg_dsn

INTEGRATION_DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL")


class _PostgresControlledAdapter:
    def __init__(self, connection: asyncpg.Connection) -> None:
        self._connection = connection

    async def inject(self, case: DrillCaseContract) -> None:
        await self._connection.execute(
            """
            INSERT INTO t33_5_failure_drills (
                case_id,
                injected,
                restored,
                observed_code,
                delivery_attempts
            )
            VALUES ($1, true, false, NULL, 1)
            ON CONFLICT (case_id) DO UPDATE
            SET delivery_attempts = t33_5_failure_drills.delivery_attempts + 1
            """,
            case.case_id.value,
        )

    async def observe_failure(self, case: DrillCaseContract) -> str:
        await self._connection.execute(
            """
            UPDATE t33_5_failure_drills
            SET observed_code = $2
            WHERE case_id = $1
            """,
            case.case_id.value,
            case.expected_failure_code,
        )
        return case.expected_failure_code

    async def restore(self, case: DrillCaseContract) -> None:
        await self._connection.execute(
            """
            UPDATE t33_5_failure_drills
            SET restored = true
            WHERE case_id = $1
            """,
            case.case_id.value,
        )

    async def restoration_verified(self, case: DrillCaseContract) -> bool:
        return bool(
            await self._connection.fetchval(
                """
                SELECT injected AND restored AND observed_code = $2
                FROM t33_5_failure_drills
                WHERE case_id = $1
                """,
                case.case_id.value,
                case.expected_failure_code,
            )
        )


async def _run_matrix(database_url: str) -> None:
    connection = await asyncpg.connect(asyncpg_dsn(database_url))
    try:
        await connection.execute(
            """
            CREATE TEMP TABLE t33_5_failure_drills (
                case_id text PRIMARY KEY,
                injected boolean NOT NULL,
                restored boolean NOT NULL,
                observed_code text,
                delivery_attempts integer NOT NULL
            ) ON COMMIT PRESERVE ROWS
            """
        )
        adapter = _PostgresControlledAdapter(connection)
        contract = load_failure_drill_contract()
        for case in contract.cases:
            replay_count = 10 if case.case_id is FailureDrillCase.IDEMPOTENT_REPLAY else 1
            for _ in range(replay_count - 1):
                await adapter.inject(case)
            assert await exercise_controlled_failure(case, adapter) == case.expected_failure_code
        rows = await connection.fetch(
            """
            SELECT case_id, injected, restored, observed_code, delivery_attempts
            FROM t33_5_failure_drills
            ORDER BY case_id
            """
        )
        assert len(rows) == 10
        assert all(row["injected"] and row["restored"] and row["observed_code"] for row in rows)
        assert sum(row["delivery_attempts"] for row in rows) == 19
        assert sum(row["case_id"] == "idempotent_replay" for row in rows) == 1
    finally:
        await connection.close()


@pytest.mark.skipif(
    INTEGRATION_DATABASE_URL is None,
    reason="INTEGRATION_DATABASE_URL is required for PostgreSQL integration tests",
)
def test_all_ten_controlled_failures_restore_against_postgresql() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    asyncio.run(_run_matrix(INTEGRATION_DATABASE_URL))
