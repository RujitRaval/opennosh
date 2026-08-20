from __future__ import annotations

import asyncio
from typing import Any

import pytest
from opennosh_api.foods.service import (
    FoodSearchTimeoutError,
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


def test_database_timeout_is_translated_after_rolling_back() -> None:
    database = _FailingDatabase("57014")

    with pytest.raises(FoodSearchTimeoutError):
        asyncio.run(
            search_foods(
                database,  # type: ignore[arg-type]
                query="apple",
                locale=None,
                source=None,
                limit=20,
                offset=0,
                statement_timeout_ms=500,
            )
        )

    assert database.rolled_back is True


def test_non_timeout_database_errors_are_not_hidden() -> None:
    database = _FailingDatabase("08006")

    with pytest.raises(DBAPIError) as raised:
        asyncio.run(
            search_foods(
                database,  # type: ignore[arg-type]
                query="apple",
                locale=None,
                source=None,
                limit=20,
                offset=0,
                statement_timeout_ms=500,
            )
        )

    assert raised.value is database.error
    assert database.rolled_back is False
