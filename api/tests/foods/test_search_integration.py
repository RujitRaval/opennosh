from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import opennosh_api.foods.router as foods_router_module
import pytest
from alembic import command
from fastapi.testclient import TestClient
from opennosh_api.foods.service import (
    FOOD_SEARCH_SQL,
    SEARCH_PLAN_MAX_EXECUTION_MS,
    FoodSearchTimeoutError,
)
from opennosh_api.main import create_app
from opennosh_api.settings import Settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

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
            await connection.execute(text("TRUNCATE foods_reference, foods_community"))
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


def _client(database_url: str) -> TestClient:
    return TestClient(
        create_app(Settings(database_url=database_url, app_environment="test", _env_file=None))
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
            params={"q": "apple", "locale": "en-IN", "limit": 2, "offset": 2},
        )
        usda_only = client.get("/api/v1/foods/search", params={"q": "apple", "source": "usda"})
        community_only = client.get(
            "/api/v1/foods/search", params={"q": "appl", "locale": "en-IN", "source": "community"}
        )
        without_locale = client.get("/api/v1/foods/search", params={"q": "apple"})

    assert first.status_code == 200
    assert [item["id"] for item in first.json()["items"]] == [
        "community:apple",
        "community:local-apple",
    ]
    assert first.json()["has_more"] is True
    assert [item["id"] for item in second.json()["items"]] == [
        "usda:100",
        "community:other-apple",
    ]
    assert second.json()["has_more"] is False
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
            {"q": "apple", "limit": 51},
            {"q": "apple", "offset": 10_001},
        ):
            assert client.get("/api/v1/foods/search", params=params).status_code == 422


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
    assert response.json() == {"detail": "Food search timed out. Try a more specific query."}


def _plan_nodes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = [plan]
    for child in plan.get("Plans", []):
        nodes.extend(_plan_nodes(child))
    return nodes


async def _explain_representative_search(database_url: str) -> tuple[float, set[str]]:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE foods_reference, foods_community"))
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
            await connection.execute(text("ANALYZE foods_reference"))
            await connection.execute(text("ANALYZE foods_community"))
            payload = await connection.scalar(
                text(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {FOOD_SEARCH_SQL}"),
                {
                    "query": "needle quinoa",
                    "slug_query": "needle quinoa",
                    "locale": "en-us",
                    "source_filter": None,
                    "fetch_limit": 21,
                    "offset": 0,
                },
            )
        if isinstance(payload, str):
            payload = json.loads(payload)
        assert isinstance(payload, list)
        report = payload[0]
        index_names = {
            str(node["Index Name"])
            for node in _plan_nodes(report["Plan"])
            if node.get("Index Name") is not None
        }
        return float(report["Execution Time"]), index_names
    finally:
        await engine.dispose()


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_representative_search_meets_the_explain_plan_budget() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    execution_ms, index_names = asyncio.run(
        _explain_representative_search(INTEGRATION_DATABASE_URL)
    )

    assert execution_ms < SEARCH_PLAN_MAX_EXECUTION_MS
    assert {
        "ix_foods_reference_search_tsv",
        "ix_foods_community_search_tsv",
    }.issubset(index_names)
