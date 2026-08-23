from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import opennosh_api.foods.service as food_service
import pytest
from opennosh_api.foods.cursors import CursorSigningKey, SearchCursorKeyRing
from opennosh_api.foods.service import (
    FoodSearchTimeoutError,
    SearchSnapshot,
    normalize_locale,
    normalize_search_query,
    search_foods,
)
from opennosh_api.main import create_app
from opennosh_api.settings import Settings
from sqlalchemy.exc import DBAPIError


class _DriverError(Exception):
    def __init__(self, sqlstate: str) -> None:
        self.sqlstate = sqlstate


class _RecordingDatabase:
    def __init__(self) -> None:
        self.rolled_back = False

    async def execute(self, *_args: Any, **_kwargs: Any) -> object:
        return object()

    async def rollback(self) -> None:
        self.rolled_back = True


class _FailingDatabase:
    def __init__(self, sqlstate: str) -> None:
        self.execute_calls = 0
        self.rolled_back = False
        self.error = DBAPIError("food search", {}, _DriverError(sqlstate))

    async def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        self.execute_calls += 1
        if self.execute_calls == 1:
            return object()
        raise self.error

    async def rollback(self) -> None:
        self.rolled_back = True


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  apple   pie  ", "apple pie"),
        ("éclair", "éclair"),
        ("豆腐", "豆腐"),
    ],
)
def test_search_query_normalization(raw: str, expected: str) -> None:
    assert normalize_search_query(raw) == expected


@pytest.mark.parametrize("raw", ["", " ", "a", "--", "a\x00", "x" * 101])
def test_search_query_rejects_empty_short_punctuation_and_oversized_values(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_search_query(raw)


def test_locale_normalization_is_case_insensitive_and_validated() -> None:
    assert normalize_locale(None) is None
    assert normalize_locale(" en-IN ") == "en-in"
    with pytest.raises(ValueError, match="BCP 47"):
        normalize_locale("../../etc")


def test_openapi_publishes_the_enforced_query_bounds() -> None:
    schema = create_app(Settings(app_environment="test", _env_file=None)).openapi()
    parameters = schema["paths"]["/api/v1/foods/search"]["get"]["parameters"]
    query_schema = next(parameter["schema"] for parameter in parameters if parameter["name"] == "q")

    assert query_schema["minLength"] == 2
    assert query_schema["maxLength"] == 100
    parameter_names = {parameter["name"] for parameter in parameters}
    cursor_schema = next(
        parameter["schema"] for parameter in parameters if parameter["name"] == "cursor"
    )
    assert any(shape.get("type") == "string" for shape in cursor_schema["anyOf"])

    assert "offset" not in parameter_names
    assert cursor_schema["maxLength"] == 2_048


async def _snapshot(*_args: Any, **_kwargs: Any) -> SearchSnapshot:
    return SearchSnapshot(
        snapshot_id=UUID("018f7d40-7b60-7000-8000-000000000001"),
        expires_at=datetime(2100, 1, 1, tzinfo=UTC),
    )


def _key_ring() -> SearchCursorKeyRing:
    return SearchCursorKeyRing((CursorSigningKey("v1", b"test-search-cursor-secret-00000001"),))


def test_projection_build_has_a_total_wall_clock_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def slow_snapshot(*_args: Any, **_kwargs: Any) -> SearchSnapshot:
        await asyncio.sleep(0.05)
        return await _snapshot()

    monkeypatch.setattr(food_service, "_fresh_snapshot", slow_snapshot)
    database = _RecordingDatabase()

    with pytest.raises(food_service.FoodSearchProjectionBusyError):
        asyncio.run(
            search_foods(
                database,  # type: ignore[arg-type]
                query="apple",
                locale=None,
                source=None,
                limit=20,
                cursor=None,
                key_ring=_key_ring(),
                cursor_lifetime_seconds=900,
                snapshot_refresh_seconds=300,
                snapshot_retention_seconds=1_200,
                snapshot_build_timeout_ms=1,
                statement_timeout_ms=500,
            )
        )

    assert database.rolled_back is True


def test_database_timeout_is_translated_after_rolling_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(food_service, "_fresh_snapshot", _snapshot)
    database = _FailingDatabase("57014")

    with pytest.raises(FoodSearchTimeoutError):
        asyncio.run(
            search_foods(
                database,  # type: ignore[arg-type]
                query="apple",
                locale=None,
                source=None,
                limit=20,
                cursor=None,
                key_ring=_key_ring(),
                cursor_lifetime_seconds=900,
                snapshot_refresh_seconds=300,
                snapshot_retention_seconds=1_200,
                snapshot_build_timeout_ms=30_000,
                statement_timeout_ms=500,
            )
        )

    assert database.rolled_back is True


def test_non_timeout_database_errors_are_not_hidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(food_service, "_fresh_snapshot", _snapshot)
    database = _FailingDatabase("08006")

    with pytest.raises(DBAPIError) as raised:
        asyncio.run(
            search_foods(
                database,  # type: ignore[arg-type]
                query="apple",
                locale=None,
                source=None,
                limit=20,
                cursor=None,
                key_ring=_key_ring(),
                cursor_lifetime_seconds=900,
                snapshot_refresh_seconds=300,
                snapshot_retention_seconds=1_200,
                snapshot_build_timeout_ms=30_000,
                statement_timeout_ms=500,
            )
        )

    assert raised.value is database.error
    assert database.rolled_back is False
