from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from opennosh_api.exercises import router
from opennosh_api.exercises import service as exercise_service
from opennosh_api.exercises.service import (
    ExerciseExportLimitError,
    ExerciseExportTimeoutError,
    ExerciseSearchTimeoutError,
    export_exercises,
    prepare_exercise_export,
    search_exercises,
)
from opennosh_api.models import Exercise
from opennosh_api.settings import Settings
from sqlalchemy.exc import DBAPIError
from starlette.requests import Request


class _DriverError(Exception):
    def __init__(self, sqlstate: str) -> None:
        self.sqlstate = sqlstate


def _database_error(sqlstate: str) -> DBAPIError:
    return DBAPIError("SELECT", {}, _DriverError(sqlstate), False)


def _exercise(source_id: str) -> Exercise:
    return Exercise(
        id=uuid4(),
        slug=f"wger-{source_id}",
        name=f"Exercise {source_id}",
        muscle_groups=[],
        equipment=[],
        search_text="exercise",
        source="wger",
        source_id=source_id,
        source_url=f"https://wger.de/api/v2/exerciseinfo/{source_id}/",
        license_spdx="CC-BY-SA-3.0",
        license_url="https://creativecommons.org/licenses/by-sa/3.0/",
        author="wger contributors",
        attribution_text="wger contributors, licensed under CC BY-SA 3.0.",
        translations_json=[],
        translation_attribution_json=[],
    )


class _AsyncExerciseRows:
    def __init__(self, rows: list[Exercise]) -> None:
        self._rows = iter(rows)
        self.closed = False

    def __aiter__(self) -> _AsyncExerciseRows:
        return self

    async def __anext__(self) -> Exercise:
        try:
            return next(self._rows)
        except StopIteration as error:
            raise StopAsyncIteration from error

    async def close(self) -> None:
        self.closed = True


def test_search_timeout_rolls_back_and_other_database_errors_pass_through() -> None:
    async def run() -> None:
        timed_out = AsyncMock()
        timed_out.scalars.side_effect = _database_error("57014")
        with pytest.raises(ExerciseSearchTimeoutError):
            await search_exercises(
                timed_out,
                query="squat",
                muscle=None,
                equipment=None,
                limit=20,
                offset=0,
                statement_timeout_ms=10,
            )
        timed_out.rollback.assert_awaited_once()

        failed = AsyncMock()
        failed.scalars.side_effect = _database_error("08006")
        with pytest.raises(DBAPIError):
            await search_exercises(
                failed,
                query="squat",
                muscle=None,
                equipment=None,
                limit=20,
                offset=0,
                statement_timeout_ms=10,
            )
        failed.rollback.assert_not_awaited()

    asyncio.run(run())


def test_export_enforces_row_boundary_and_translates_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        monkeypatch.setattr("opennosh_api.exercises.service.EXPORT_ROW_LIMIT", 1)
        at_limit = AsyncMock()
        at_limit.scalar.return_value = 1
        at_limit.scalars.return_value = SimpleNamespace(all=lambda: [_exercise("1")])
        result = await export_exercises(at_limit, statement_timeout_ms=10)
        assert len(result.entries) == 1

        over_limit = AsyncMock()
        over_limit.scalar.return_value = 1
        over_limit.scalars.return_value = SimpleNamespace(
            all=lambda: [_exercise("1"), _exercise("2")]
        )
        with pytest.raises(ExerciseExportLimitError):
            await export_exercises(over_limit, statement_timeout_ms=10)

        timed_out = AsyncMock()
        timed_out.scalar.side_effect = _database_error("57014")
        with pytest.raises(ExerciseExportTimeoutError):
            await export_exercises(timed_out, statement_timeout_ms=10)
        timed_out.rollback.assert_awaited_once()

    asyncio.run(run())


def test_production_exercise_export_enforces_streamed_row_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        monkeypatch.setattr(exercise_service, "EXPORT_ROW_LIMIT", 1)
        rows = _AsyncExerciseRows([_exercise("1"), _exercise("2")])
        database = AsyncMock()
        database.stream_scalars.return_value = rows

        with pytest.raises(ExerciseExportLimitError):
            await prepare_exercise_export(database, statement_timeout_ms=10)

        assert rows.closed is True
        database.rollback.assert_awaited_once()

    asyncio.run(run())


def test_router_returns_controlled_503_for_database_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        monkeypatch.setattr(router, "enforce_rate_limit", AsyncMock())
        monkeypatch.setattr(
            router, "search_exercises", AsyncMock(side_effect=ExerciseSearchTimeoutError)
        )
        request = Request({"type": "http", "client": ("203.0.113.1", 1234)})
        database = AsyncMock()
        settings = Settings(_env_file=None)
        with pytest.raises(HTTPException) as search_error:
            await router.search(
                request,
                database,
                settings,
                q="squat",
                muscle=None,
                equipment=None,
                limit=20,
                offset=0,
            )
        assert search_error.value.status_code == 503

        monkeypatch.setattr(
            router,
            "prepare_exercise_export",
            AsyncMock(side_effect=ExerciseExportTimeoutError),
        )
        monkeypatch.setattr(
            router, "_acquire_capacity", AsyncMock(return_value=asyncio.Semaphore(1))
        )
        with pytest.raises(HTTPException) as export_error:
            await router.attributed_export(request, database, settings)
        assert export_error.value.status_code == 503

    asyncio.run(run())
