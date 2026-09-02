from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import opennosh_api.foods.router as foods_router_module
import pytest
from alembic import command
from fastapi.testclient import TestClient
from opennosh_api.foods.service import (
    _SNAPSHOT_SEARCH_VECTOR,
    FOOD_SEARCH_SNAPSHOT_INSERT_SQL,
    FOOD_SEARCH_SQL,
    SEARCH_PLAN_MAX_EXECUTION_MS,
    FoodSearchProjectionBusyError,
    FoodSearchTimeoutError,
    _existing_snapshot,
    _fresh_snapshot,
)
from opennosh_api.main import create_app
from opennosh_api.settings import Settings
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.tests.problem_assertions import problem_without_request_id
from api.tests.test_migrations import migration_config

INTEGRATION_DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL")

_NUTRIENTS = """{
  "basis": "per_100g",
  "nutrients": {
    "energy_kcal": "100",
    "protein_g": "1",
    "carbohydrate_g": "20",
    "fat_g": "1"
  }
}"""


async def _seed_ranked_foods(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("TRUNCATE food_search_snapshots, foods_reference, foods_community CASCADE")
            )
            await connection.execute(
                text("DELETE FROM auth_rate_limits WHERE scope = 'food-search-ip'")
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO foods_reference (
                        fdc_id, description, food_category, nutrients_json, portions_json
                    ) VALUES (
                        '100', 'Apple USDA generic', 'fruit', CAST(:nutrients AS jsonb),
                        '[{"name":"medium","grams":"182"}]'::jsonb
                    )
                    """
                ),
                {"nutrients": _NUTRIENTS},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO foods_community (
                        pack_id, pack_version, slug, name, name_local, locale, category,
                        provenance, source_uri, source_license, nutrients_json,
                        portions_json, contributed_by
                    ) VALUES
                    (
                        'exact-pack', '1.0.0', 'apple', 'Orchard fruit', NULL, 'fr-FR',
                        'fruit', 'own_measurement', NULL, 'contributor-original',
                        CAST(:nutrients AS jsonb), '[]'::jsonb, 'exact-contributor'
                    ),
                    (
                        'locale-pack', '2.0.0', 'local-apple', 'Apple local variety',
                        'सेब', 'en-IN', 'fruit', 'government_database',
                        'https://example.gov/apple', 'public-domain',
                        CAST(:nutrients AS jsonb), '[]'::jsonb, 'locale-contributor'
                    ),
                    (
                        'other-pack', '1.0.0', 'other-apple', 'Apple other locale', NULL,
                        'fr-FR', 'fruit', 'manufacturer_label',
                        'https://example.test/apple', 'contributor-original',
                        CAST(:nutrients AS jsonb), '[]'::jsonb, 'other-contributor'
                    )
                    """
                ),
                {"nutrients": _NUTRIENTS},
            )
    finally:
        await engine.dispose()


def _client(database_url: str, **settings: Any) -> TestClient:
    return TestClient(
        create_app(
            Settings(
                database_url=database_url,
                app_environment="test",
                _env_file=None,
                **settings,
            )
        )
    )


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_search_ranking_pagination_filters_and_attribution() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_seed_ranked_foods(INTEGRATION_DATABASE_URL))

    with _client(INTEGRATION_DATABASE_URL) as client:
        first = client.get(
            "/api/v1/foods/search",
            params={"q": "apple", "locale": "en-IN", "limit": 2},
        )
        second = client.get(
            "/api/v1/foods/search",
            params={
                "q": "apple",
                "locale": "en-IN",
                "limit": 2,
                "cursor": first.json()["next_cursor"],
            },
        )
        usda_only = client.get("/api/v1/foods/search", params={"q": "apple", "source": "usda"})
        community_only = client.get(
            "/api/v1/foods/search",
            params={
                "q": "appl",
                "locale": "en-IN",
                "source": "community",
            },
        )
        without_locale = client.get("/api/v1/foods/search", params={"q": "apple"})

    assert first.status_code == 200
    assert first.json()["schema_version"] == "2.0"
    assert [item["id"] for item in first.json()["items"]] == [
        "community:apple",
        "community:local-apple",
    ]
    assert first.json()["has_more"] is True
    assert first.json()["next_cursor"]
    assert [item["id"] for item in second.json()["items"]] == [
        "usda:100",
        "community:other-apple",
    ]
    assert second.json()["has_more"] is False
    assert second.json()["next_cursor"] is None
    assert second.json()["snapshot_id"] == first.json()["snapshot_id"]
    assert [item["id"] for item in usda_only.json()["items"]] == ["usda:100"]
    assert [item["id"] for item in community_only.json()["items"]] == [
        "community:local-apple",
        "community:apple",
        "community:other-apple",
    ]
    assert [item["id"] for item in without_locale.json()["items"]][:2] == [
        "community:apple",
        "usda:100",
    ]

    exact_attribution = first.json()["items"][0]["attribution"]
    assert exact_attribution == {
        "source": "community",
        "license": "CC0-1.0",
        "source_uri": None,
        "source_license": "contributor-original",
        "contributed_by": "exact-contributor",
        "pack_id": "exact-pack",
        "pack_version": "1.0.0",
        "provenance": "own_measurement",
        "release_version": None,
        "release_digest": None,
    }
    assert usda_only.json()["items"][0]["attribution"]["license"] == "CC0"


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_food_details_are_source_qualified_and_license_separated() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_seed_ranked_foods(INTEGRATION_DATABASE_URL))

    with _client(INTEGRATION_DATABASE_URL) as client:
        community = client.get("/api/v1/foods/community/local-apple")
        usda = client.get("/api/v1/foods/usda/100")
        missing = client.get("/api/v1/foods/community/missing")
        disabled_federation = client.get(
            "/api/v1/foods/federation/018f7d40-7b60-7000-8000-000000000099:apple"
        )
        unsupported = client.get("/api/v1/foods/openfoodfacts/123")
        nul_source_id = client.get("/api/v1/foods/community/apple%00")

    assert community.status_code == 200
    assert community.json()["id"] == "community:local-apple"
    assert community.json()["attribution"]["source_uri"] == "https://example.gov/apple"
    assert community.json()["nutrients"]["basis"] == "per_100g"
    assert usda.status_code == 200
    assert usda.json()["id"] == "usda:100"
    assert usda.json()["attribution"]["source_license"] == "CC0"
    assert usda.json()["portions"][0]["grams"] == "182"
    assert missing.status_code == 404
    assert disabled_federation.status_code == 503
    assert disabled_federation.json()["detail"] == "Federated food search is disabled."
    assert unsupported.status_code == 422
    assert nul_source_id.status_code == 422


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_search_rejects_unbounded_or_invalid_requests() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")

    with _client(INTEGRATION_DATABASE_URL) as client:
        for params in (
            {"q": ""},
            {"q": "a"},
            {"q": "--"},
            {"q": "a\x00"},
            {"q": "apple", "locale": "../../etc"},
            {"q": "apple", "source": "all-stores"},
            {"q": "apple", "pack": "Not-Canonical"},
            {"q": "apple", "limit": 51},
            {"q": "apple", "offset": 10_001},
        ):
            assert client.get("/api/v1/foods/search", params=params).status_code == 422

        disabled = client.get(
            "/api/v1/foods/search",
            params={"q": "apple", "source": "federation"},
        )
        selected_disabled = client.get(
            "/api/v1/foods/search",
            params={"q": "apple", "pack": "regional-pack"},
        )

    assert disabled.status_code == 503
    assert disabled.json()["detail"] == "Federated food search is disabled."
    assert selected_disabled.status_code == 503


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_public_search_is_rate_limited_by_source_ip() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_seed_ranked_foods(INTEGRATION_DATABASE_URL))
    app = create_app(
        Settings(
            database_url=INTEGRATION_DATABASE_URL,
            app_environment="test",
            food_search_rate_limit_attempts=2,
            food_search_rate_limit_window_seconds=60,
            _env_file=None,
        )
    )

    with TestClient(app, client=("203.0.113.50", 50_000)) as client:
        responses = [client.get("/api/v1/foods/search", params={"q": "apple"}) for _ in range(3)]

    assert [response.status_code for response in responses] == [200, 200, 429]
    assert int(responses[-1].headers["retry-after"]) > 0


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_projection_warmup_returns_accurate_retry_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")

    async def busy_search(*_args: Any, **_kwargs: Any) -> None:
        raise FoodSearchProjectionBusyError

    monkeypatch.setattr(foods_router_module, "search_foods", busy_search)
    with _client(INTEGRATION_DATABASE_URL) as client:
        response = client.get("/api/v1/foods/search", params={"q": "apple"})

    assert response.status_code == 503
    assert response.json()["detail"] == ("Food search is being prepared. Try again shortly.")
    assert response.json()["recovery_actions"] == [{"id": "retry", "label": "Try again"}]


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_search_timeout_returns_a_controlled_service_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_seed_ranked_foods(INTEGRATION_DATABASE_URL))

    async def timeout_search(*_args: Any, **_kwargs: Any) -> None:
        raise FoodSearchTimeoutError

    monkeypatch.setattr(foods_router_module, "search_foods", timeout_search)
    with _client(INTEGRATION_DATABASE_URL) as client:
        response = client.get("/api/v1/foods/search", params={"q": "apple"})

    assert response.status_code == 503
    assert problem_without_request_id(response) == {
        "type": "https://opennosh.org/problems/service-unavailable",
        "title": "Service unavailable",
        "status": 503,
        "detail": "Food search timed out. Try a more specific query.",
        "code": "service_unavailable",
        "schema_version": "1.0",
        "recovery_actions": [{"id": "retry", "label": "Try again"}],
    }


async def _execute_search_sql(
    database_url: str,
    statement: str,
    parameters: dict[str, Any] | None = None,
) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            for sql in statement.split(";"):
                if sql.strip():
                    await connection.execute(text(sql), parameters or {})
    finally:
        await engine.dispose()


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_cursor_tamper_mismatch_and_legacy_offset_return_typed_errors() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_seed_ranked_foods(INTEGRATION_DATABASE_URL))

    with _client(INTEGRATION_DATABASE_URL) as client:
        first = client.get(
            "/api/v1/foods/search",
            params={"q": "apple", "locale": "en-IN", "limit": 2},
        )
        cursor = first.json()["next_cursor"]
        assert isinstance(cursor, str)
        tampered = cursor[:-1] + ("A" if cursor[-1] != "A" else "B")
        responses = [
            client.get(
                "/api/v1/foods/search",
                params={
                    "q": "apple",
                    "locale": "en-IN",
                    "limit": 2,
                    "cursor": tampered,
                },
            ),
            client.get(
                "/api/v1/foods/search",
                params={
                    "q": "pear",
                    "locale": "en-IN",
                    "limit": 2,
                    "cursor": cursor,
                },
            ),
            client.get(
                "/api/v1/foods/search",
                params={
                    "q": "apple",
                    "locale": "en-IN",
                    "source": "community",
                    "limit": 2,
                    "cursor": cursor,
                },
            ),
            client.get(
                "/api/v1/foods/search",
                params={
                    "q": "apple",
                    "locale": "en-IN",
                    "limit": 3,
                    "cursor": cursor,
                },
            ),
            client.get(
                "/api/v1/foods/search",
                params={"q": "apple", "offset": 2},
            ),
            client.get(
                "/api/v1/foods/search",
                params={"q": "apple", "cursor": "x" * 2_049},
            ),
        ]

    assert [response.status_code for response in responses] == [
        400,
        409,
        409,
        409,
        422,
        400,
    ]
    assert responses[0].json()["code"] == "search_cursor_invalid"
    assert responses[5].json()["code"] == "search_cursor_invalid"
    for response in responses[1:4]:
        problem = response.json()
        assert problem["code"] == "search_cursor_restart"
        assert problem["recovery_actions"][0]["id"] == "restart_search"
        assert "cursor=" not in problem["recovery_actions"][0]["href"]


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_projection_swap_does_not_change_an_active_pagination_journey() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_seed_ranked_foods(INTEGRATION_DATABASE_URL))

    with _client(INTEGRATION_DATABASE_URL) as client:
        first = client.get(
            "/api/v1/foods/search",
            params={"q": "apple", "locale": "en-IN", "limit": 2},
        )
        cursor = first.json()["next_cursor"]
        original_snapshot = first.json()["snapshot_id"]

        asyncio.run(
            _execute_search_sql(
                INTEGRATION_DATABASE_URL,
                """
                UPDATE food_search_snapshots
                SET created_at = created_at - INTERVAL '1 day'
                WHERE id = CAST(:snapshot_id AS uuid);
                UPDATE foods_community
                SET name = 'Apple changed after projection'
                WHERE slug = 'other-apple';
                INSERT INTO foods_community (
                    pack_id, pack_version, slug, name, locale, category, provenance,
                    source_uri, source_license, nutrients_json, portions_json,
                    contributed_by
                ) VALUES (
                    'new-pack', '1.0.0', 'new-apple', 'Apple newly published', 'en-IN',
                    'fruit', 'own_measurement', NULL, 'contributor-original',
                    '{}'::jsonb, '[]'::jsonb, 'new-contributor'
                )
                """,
                {"snapshot_id": original_snapshot},
            )
        )

        new_first = client.get(
            "/api/v1/foods/search",
            params={"q": "apple", "locale": "en-IN", "limit": 50},
        )
        old_second = client.get(
            "/api/v1/foods/search",
            params={
                "q": "apple",
                "locale": "en-IN",
                "limit": 2,
                "cursor": cursor,
            },
        )

    assert new_first.json()["snapshot_id"] != original_snapshot
    assert "community:new-apple" in {item["id"] for item in new_first.json()["items"]}
    assert "community:new-apple" not in {item["id"] for item in old_second.json()["items"]}
    assert (
        next(
            item["name"]
            for item in old_second.json()["items"]
            if item["id"] == "community:other-apple"
        )
        == "Apple other locale"
    )


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_expired_or_missing_snapshot_returns_restart_action() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_seed_ranked_foods(INTEGRATION_DATABASE_URL))

    with _client(INTEGRATION_DATABASE_URL) as client:
        first = client.get(
            "/api/v1/foods/search",
            params={"q": "apple", "limit": 2},
        )
        asyncio.run(
            _execute_search_sql(
                INTEGRATION_DATABASE_URL,
                """
                UPDATE food_search_snapshots
                SET created_at = now() - INTERVAL '2 seconds',
                    expires_at = now() - INTERVAL '1 second'
                WHERE id = CAST(:snapshot_id AS uuid)
                """,
                {"snapshot_id": first.json()["snapshot_id"]},
            )
        )
        expired = client.get(
            "/api/v1/foods/search",
            params={
                "q": "apple",
                "limit": 2,
                "cursor": first.json()["next_cursor"],
            },
        )

    assert expired.status_code == 409
    assert expired.json()["code"] == "search_cursor_restart"
    assert expired.json()["recovery_actions"][0]["id"] == "restart_search"


async def _assert_active_snapshot_blocks_cleanup(database_url: str, snapshot_id: str) -> None:
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as reader, engine.connect() as cleanup:
            await _existing_snapshot(
                reader,
                snapshot_id=UUID(snapshot_id),
                now=datetime.now(UTC),
            )
            cleanup_transaction = await cleanup.begin()
            await cleanup.execute(text("SET LOCAL lock_timeout = '100ms'"))
            with pytest.raises(DBAPIError) as raised:
                await cleanup.execute(
                    text("DELETE FROM food_search_snapshots WHERE id = CAST(:snapshot_id AS uuid)"),
                    {"snapshot_id": snapshot_id},
                )
            assert getattr(raised.value.orig, "sqlstate", None) == "55P03"
            await cleanup_transaction.rollback()
            await reader.rollback()
    finally:
        await engine.dispose()


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_active_snapshot_reader_blocks_concurrent_cleanup() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_seed_ranked_foods(INTEGRATION_DATABASE_URL))

    with _client(INTEGRATION_DATABASE_URL) as client:
        first = client.get(
            "/api/v1/foods/search",
            params={"q": "apple", "limit": 2},
        )

    assert first.status_code == 200
    asyncio.run(
        _assert_active_snapshot_blocks_cleanup(
            INTEGRATION_DATABASE_URL, first.json()["snapshot_id"]
        )
    )


async def _assert_lock_busy_snapshot_behavior(database_url: str, snapshot_id: str) -> None:
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.connect() as blocker:
            await blocker.execute(
                text(
                    "UPDATE food_search_snapshots "
                    "SET created_at = now() - INTERVAL '10 minutes' "
                    "WHERE id = CAST(:snapshot_id AS uuid)"
                ),
                {"snapshot_id": snapshot_id},
            )
            await blocker.commit()

            lock_transaction = await blocker.begin()
            await blocker.execute(
                text("SELECT pg_advisory_xact_lock(hashtext('opennosh.food-search-projection'))")
            )
            async with sessions() as reader:
                retained = await _fresh_snapshot(
                    reader,
                    now=datetime.now(UTC),
                    refresh_seconds=300,
                    retention_seconds=1_200,
                )
                assert retained.snapshot_id == UUID(snapshot_id)
                await reader.rollback()
            await lock_transaction.rollback()

            await blocker.execute(text("DELETE FROM food_search_snapshots"))
            await blocker.commit()
            cold_lock_transaction = await blocker.begin()
            await blocker.execute(
                text("SELECT pg_advisory_xact_lock(hashtext('opennosh.food-search-projection'))")
            )
            async with sessions() as cold_reader:
                with pytest.raises(FoodSearchProjectionBusyError):
                    await _fresh_snapshot(
                        cold_reader,
                        now=datetime.now(UTC),
                        refresh_seconds=300,
                        retention_seconds=1_200,
                    )
                await cold_reader.rollback()
            await cold_lock_transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_lock_busy_search_uses_retained_snapshot_or_fails_fast_when_cold() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_seed_ranked_foods(INTEGRATION_DATABASE_URL))

    with _client(INTEGRATION_DATABASE_URL) as client:
        first = client.get(
            "/api/v1/foods/search",
            params={"q": "apple", "limit": 2},
        )

    assert first.status_code == 200
    asyncio.run(
        _assert_lock_busy_snapshot_behavior(INTEGRATION_DATABASE_URL, first.json()["snapshot_id"])
    )


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_cursor_survives_restart_and_n_n_minus_one_key_rotation() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_seed_ranked_foods(INTEGRATION_DATABASE_URL))
    old_key = "v1:11111111111111111111111111111111"
    rotated_keys = "v2:22222222222222222222222222222222,v1:11111111111111111111111111111111"

    with _client(
        INTEGRATION_DATABASE_URL,
        food_search_cursor_signing_keys=old_key,
    ) as client:
        first = client.get("/api/v1/foods/search", params={"q": "apple", "limit": 2})
        cursor = first.json()["next_cursor"]

    with _client(
        INTEGRATION_DATABASE_URL,
        food_search_cursor_signing_keys=rotated_keys,
    ) as restarted_client:
        continued = restarted_client.get(
            "/api/v1/foods/search",
            params={"q": "apple", "limit": 2, "cursor": cursor},
        )

    with _client(
        INTEGRATION_DATABASE_URL,
        food_search_cursor_signing_keys=("v2:22222222222222222222222222222222"),
    ) as retired_client:
        retired = retired_client.get(
            "/api/v1/foods/search",
            params={"q": "apple", "limit": 2, "cursor": cursor},
        )

    assert continued.status_code == 200
    assert continued.json()["snapshot_id"] == first.json()["snapshot_id"]
    assert retired.status_code == 409
    assert retired.json()["code"] == "search_cursor_restart"


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_deep_cursor_pages_preserve_exact_ties_without_gaps_or_duplicates() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(
        _execute_search_sql(
            INTEGRATION_DATABASE_URL,
            """
            TRUNCATE food_search_snapshots, foods_reference, foods_community CASCADE;
            DELETE FROM auth_rate_limits WHERE scope = 'food-search-ip';
            INSERT INTO foods_community (
                pack_id, pack_version, slug, name, locale, category, provenance,
                source_uri, source_license, nutrients_json, portions_json,
                contributed_by
            )
            SELECT
                'tie-pack', '1.0.0', 'tie-apple-' || lpad(value::text, 4, '0'),
                'Tie apple', 'en-US', 'tie', 'own_measurement', NULL,
                'contributor-original', '{}'::jsonb, '[]'::jsonb, 'tie'
            FROM generate_series(1, 123) AS value
            """,
        )
    )

    seen: list[str] = []
    cursor: str | None = None
    with _client(INTEGRATION_DATABASE_URL) as client:
        for _page in range(30):
            response = client.get(
                "/api/v1/foods/search",
                params={
                    "q": "tie apple",
                    "limit": 7,
                    **({"cursor": cursor} if cursor is not None else {}),
                },
            )
            assert response.status_code == 200
            payload = response.json()
            seen.extend(item["id"] for item in payload["items"])
            cursor = payload["next_cursor"]
            if cursor is None:
                break

    assert len(seen) == 123
    assert len(set(seen)) == 123
    assert seen == sorted(seen)


def _plan_nodes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = [plan]
    for child in plan.get("Plans", []):
        nodes.extend(_plan_nodes(child))
    return nodes


async def _explain_representative_search(
    database_url: str,
) -> tuple[float, set[str], set[str]]:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("TRUNCATE food_search_snapshots, foods_reference, foods_community CASCADE")
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO foods_reference (
                        fdc_id, description, food_category, nutrients_json, portions_json
                    )
                    SELECT
                        'perf-ref-' || value,
                        CASE WHEN value = 4242 THEN 'Needle quinoa reference'
                             ELSE 'Generic reference food ' || value END,
                        'performance', '{}'::jsonb, '[]'::jsonb
                    FROM generate_series(1, 5000) AS value
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO foods_community (
                        pack_id, pack_version, slug, name, locale, category, provenance,
                        source_uri, source_license, nutrients_json, portions_json,
                        contributed_by
                    )
                    SELECT
                        'perf-pack', '1.0.0', 'perf-community-' || value,
                        CASE WHEN value = 4242 THEN 'Needle quinoa community'
                             ELSE 'Community food ' || value END,
                        'en-US', 'performance', 'own_measurement', NULL,
                        'contributor-original', '{}'::jsonb, '[]'::jsonb, 'perf'
                    FROM generate_series(1, 5000) AS value
                    """
                )
            )
            snapshot_id = await connection.scalar(
                text(
                    """
                    INSERT INTO food_search_snapshots (
                        ranking_version, created_at, expires_at
                    ) VALUES (1, now(), now() + INTERVAL '20 minutes')
                    RETURNING id
                    """
                )
            )
            await connection.execute(
                text(FOOD_SEARCH_SNAPSHOT_INSERT_SQL),
                {
                    "snapshot_id": snapshot_id,
                    "has_pack_filter": False,
                    "selected_pack_ids": [],
                },
            )
            await connection.execute(text("ANALYZE food_search_snapshot_items"))
            parameters = {
                "query": "needle quinoa",
                "slug_query": "needle quinoa",
                "locale": "en-us",
                "source_filter": None,
                "snapshot_id": snapshot_id,
                "has_cursor": False,
                "after_rank": 0,
                "after_score": 0.0,
                "after_name": "",
                "after_source": "",
                "after_source_id": "",
                "fetch_limit": 21,
            }
            statement = text(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {FOOD_SEARCH_SQL}")
            payload = await connection.scalar(statement, parameters)
            await connection.execute(text("SET LOCAL enable_seqscan = off"))
            forced_payload = await connection.scalar(
                text(
                    f"""
                    EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
                    SELECT source_id
                    FROM food_search_snapshot_items AS food
                    WHERE {_SNAPSHOT_SEARCH_VECTOR} @@
                        plainto_tsquery('simple'::regconfig, CAST(:query AS text))
                    """
                ),
                {"query": "needle quinoa"},
            )
        if isinstance(payload, str):
            payload = json.loads(payload)
        if isinstance(forced_payload, str):
            forced_payload = json.loads(forced_payload)
        assert isinstance(payload, list)
        assert isinstance(forced_payload, list)
        report = payload[0]
        forced_report = forced_payload[0]
        index_names = {
            str(node["Index Name"])
            for node in _plan_nodes(report["Plan"])
            if node.get("Index Name") is not None
        }
        forced_index_names = {
            str(node["Index Name"])
            for node in _plan_nodes(forced_report["Plan"])
            if node.get("Index Name") is not None
        }
        return float(report["Execution Time"]), index_names, forced_index_names
    finally:
        await engine.dispose()


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_representative_search_meets_the_explain_plan_budget() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    execution_ms, index_names, forced_index_names = asyncio.run(
        _explain_representative_search(INTEGRATION_DATABASE_URL)
    )

    assert execution_ms < SEARCH_PLAN_MAX_EXECUTION_MS
    assert "pk_food_search_snapshot_items" in index_names
    assert forced_index_names & {
        "ix_food_search_snapshot_items_search_tsv",
        "ix_food_search_snapshot_items_source_id_trgm",
        "ix_food_search_snapshot_items_name_trgm",
        "ix_food_search_snapshot_items_name_local_trgm",
    }
