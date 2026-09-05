from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import opennosh_api.foods.router as food_router
import opennosh_api.foods.service as food_service
import pytest
from fastapi import HTTPException, Request
from opennosh_api.foods.cursors import (
    CursorSigningKey,
    SearchCursorError,
    SearchCursorKeyRing,
    SearchCursorPayload,
    search_fingerprint,
)
from opennosh_api.foods.schemas import (
    FoodAttribution,
    FoodSearchItem,
    FoodSearchResponse,
    FoodSource,
)
from opennosh_api.foods.service import (
    FoodSearchTimeoutError,
    SearchSnapshot,
    get_food_detail,
    normalize_locale,
    normalize_pack_ids,
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


def _request(path: str, *, query_string: bytes = b"") -> Request:
    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": query_string,
            "headers": (),
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
        }
    )
    request.state.request_id = "food-search-test-request"
    return request


def _readiness_search_response(*, provenance: str) -> FoodSearchResponse:
    return FoodSearchResponse(
        items=[
            FoodSearchItem(
                id="community:gujarati-plain-thepla",
                source=FoodSource.COMMUNITY,
                source_id="gujarati-plain-thepla",
                name="Plain thepla",
                attribution=FoodAttribution(
                    source=FoodSource.COMMUNITY,
                    license="CC0-1.0",
                    source_license="contributor-original",
                    pack_id="gujarati-home-cooking",
                    provenance=provenance,
                ),
            )
        ],
        limit=10,
        has_more=False,
        snapshot_id=UUID("018f7d40-7b60-7000-8000-000000000001"),
        snapshot_expires_at=datetime(2100, 1, 1, tzinfo=UTC),
    )


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


def test_pack_filter_normalization_is_canonical_bounded_and_deduplicated() -> None:
    assert normalize_pack_ids(None) == ()
    assert normalize_pack_ids(["regional-b", "regional-a", "regional-a"]) == (
        "regional-a",
        "regional-b",
    )
    invalid_filters = (
        ["Regional-A"],
        ["../regional-a"],
        ["a" * 81],
        [f"pack-{i}" for i in range(21)],
    )
    for invalid in invalid_filters:
        with pytest.raises(ValueError):
            normalize_pack_ids(invalid)


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
    assert "pack" in parameter_names
    assert cursor_schema["maxLength"] == 2_048


def test_readiness_directly_returns_the_approved_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _readiness_search_response(provenance="published_recipe_calculation")

    async def ready_search(*_args: Any, **_kwargs: Any) -> FoodSearchResponse:
        return expected

    monkeypatch.setattr(food_router, "search_foods", ready_search)
    response = asyncio.run(
        food_router.readiness(
            _request("/api/v1/foods/readiness"),
            object(),
            Settings(app_environment="test", _env_file=None),
        )
    )

    assert response.status == "ready"
    assert response.result.id == "community:gujarati-plain-thepla"
    assert response.latency_ms >= 0


def test_readiness_directly_rejects_metadata_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    drifted = _readiness_search_response(provenance="own_measurement")

    async def drifted_search(*_args: Any, **_kwargs: Any) -> FoodSearchResponse:
        return drifted

    monkeypatch.setattr(food_router, "search_foods", drifted_search)
    with pytest.raises(HTTPException) as raised:
        asyncio.run(
            food_router.readiness(
                _request("/api/v1/foods/readiness"),
                object(),
                Settings(app_environment="test", _env_file=None),
            )
        )

    assert raised.value.status_code == 503
    assert raised.value.detail == "Food search readiness failed."


def test_search_directly_logs_and_maps_a_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def allow_rate_limit(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def timeout_search(*_args: Any, **_kwargs: Any) -> FoodSearchResponse:
        raise FoodSearchTimeoutError

    monkeypatch.setattr(food_router, "enforce_rate_limit", allow_rate_limit)
    monkeypatch.setattr(food_router, "search_foods", timeout_search)
    with pytest.raises(HTTPException) as raised:
        asyncio.run(
            food_router.search(
                _request("/api/v1/foods/search", query_string=b"q=apple"),
                object(),
                Settings(app_environment="test", _env_file=None),
                q="apple",
            )
        )

    assert raised.value.status_code == 503
    assert raised.value.detail == "Food search timed out. Try a more specific query."


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


@pytest.mark.parametrize(
    ("sqlstate", "expected_error", "rolled_back"),
    [
        ("57014", food_service.FoodSearchProjectionBusyError, True),
        ("08006", DBAPIError, False),
    ],
)
def test_projection_build_database_errors_are_classified(
    monkeypatch: pytest.MonkeyPatch,
    sqlstate: str,
    expected_error: type[Exception],
    rolled_back: bool,
) -> None:
    error = DBAPIError("snapshot build", {}, _DriverError(sqlstate))

    async def failing_snapshot(*_args: Any, **_kwargs: Any) -> SearchSnapshot:
        raise error

    monkeypatch.setattr(food_service, "_fresh_snapshot", failing_snapshot)
    database = _RecordingDatabase()

    with pytest.raises(expected_error) as raised:
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

    if sqlstate != "57014":
        assert raised.value is error
    assert database.rolled_back is rolled_back


def test_cursor_release_set_mismatch_requires_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot_id = UUID("018f7d40-7b60-7000-8000-000000000001")
    now = datetime(2026, 9, 2, tzinfo=UTC)
    selected_pack_ids = ("regional-pack",)
    cursor = _key_ring().encode(
        SearchCursorPayload(
            v=2,
            sid=snapshot_id,
            fp=search_fingerprint(
                query="apple",
                locale=None,
                source="federation",
                pack_ids=selected_pack_ids,
                federation_enabled=True,
            ),
            rv=2,
            rs="a" * 64,
            pos=(1, "1", "apple", "federation", "release:record"),
            size=20,
            exp=int(now.timestamp()) + 900,
        )
    )

    async def no_active_projection(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def mismatched_snapshot(*_args: Any, **_kwargs: Any) -> SearchSnapshot:
        return SearchSnapshot(
            snapshot_id=snapshot_id,
            expires_at=datetime(2100, 1, 1, tzinfo=UTC),
            release_set_digest="b" * 64,
            selected_pack_ids=selected_pack_ids,
        )

    monkeypatch.setattr(food_service, "active_federation_projection", no_active_projection)
    monkeypatch.setattr(food_service, "_existing_snapshot", mismatched_snapshot)

    with pytest.raises(SearchCursorError, match="different pack release set"):
        asyncio.run(
            search_foods(
                _RecordingDatabase(),  # type: ignore[arg-type]
                query="apple",
                locale=None,
                source=FoodSource.FEDERATION,
                limit=20,
                cursor=cursor,
                key_ring=_key_ring(),
                cursor_lifetime_seconds=900,
                snapshot_refresh_seconds=300,
                snapshot_retention_seconds=1_200,
                snapshot_build_timeout_ms=30_000,
                statement_timeout_ms=500,
                federation_enabled=True,
                selected_pack_ids=selected_pack_ids,
                now=now,
            )
        )


def test_food_detail_handles_missing_native_and_federated_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _MissingDatabase:
        async def scalar(self, *_args: Any, **_kwargs: Any) -> None:
            return None

    database = _MissingDatabase()
    assert asyncio.run(get_food_detail(database, FoodSource.USDA, "missing")) is None  # type: ignore[arg-type]
    assert (
        asyncio.run(get_food_detail(database, FoodSource.COMMUNITY, "missing")) is None  # type: ignore[arg-type]
    )

    async def missing_federated(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(food_service, "federation_food_detail", missing_federated)
    assert (
        asyncio.run(get_food_detail(database, FoodSource.FEDERATION, "release:missing"))  # type: ignore[arg-type]
        is None
    )


def test_federated_food_detail_preserves_variant_and_nutrition_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = {
        "source": "federation",
        "source_id": "018f7d40-7b60-7000-8000-000000000099:apple",
        "source_record_id": "apple",
        "name": "Apple",
        "name_local": None,
        "category": "fruit",
        "license": "CC0-1.0",
        "source_uri": "https://example.test/apple",
        "source_license": "public-domain",
        "contributed_by": "test-maintainer",
        "pack_id": "regional-pack",
        "pack_version": "1.0.0",
        "provenance": "government_database",
        "release_version": "2026.09",
        "release_digest": "a" * 64,
        "equivalence_group_id": "b" * 64,
        "variant_id": "federation:018f7d40-7b60-7000-8000-000000000099:apple",
        "conflict": True,
        "variant_count": 2,
        "nutrients_json": {"basis": "per_100g", "nutrients": {}},
        "portions_json": [{"name": "medium", "grams": "182"}],
    }

    async def federated(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return row

    monkeypatch.setattr(food_service, "federation_food_detail", federated)
    detail = asyncio.run(
        get_food_detail(_RecordingDatabase(), FoodSource.FEDERATION, row["source_id"])  # type: ignore[arg-type]
    )

    assert detail is not None
    assert detail.variant_id == row["variant_id"]
    assert detail.conflict is True
    assert detail.variant_count == 2
    assert detail.nutrients == row["nutrients_json"]
    assert detail.portions[0].grams == 182
