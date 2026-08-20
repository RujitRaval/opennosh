from __future__ import annotations

import asyncio
import json
import os

import opennosh_api.foods.open_food_facts as open_food_facts_service
import opennosh_api.foods.router as foods_router
import pytest
from alembic import command
from fastapi.testclient import TestClient
from opennosh_api.foods.open_food_facts import (
    OpenFoodFactsExportTimeoutError,
    cache_product,
    export_cached_products,
)
from opennosh_api.integrations.open_food_facts import (
    OPEN_FOOD_FACTS_ATTRIBUTION,
    OpenFoodFactsClient,
    OpenFoodFactsNotFoundError,
    OpenFoodFactsProduct,
    OpenFoodFactsRateLimitedError,
    OpenFoodFactsTimeoutError,
    OpenFoodFactsUpstreamError,
)
from opennosh_api.main import create_app
from opennosh_api.settings import Settings
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.tests.test_migrations import migration_config

INTEGRATION_DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL")
BARCODE = "3017620422003"
NUTRIENTS = {
    "basis": "per_100g",
    "nutrients": {
        "energy_kcal": "539",
        "protein_g": "6.3",
        "fat_g": "30.9",
        "carbohydrate_g": "57.5",
    },
    "density_g_per_ml": None,
}


async def _reset(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE foods_odbl, foods_community CASCADE"))
            await connection.execute(
                text(
                    "DELETE FROM auth_rate_limits WHERE scope LIKE 'open-food-facts-%'"
                )
            )
    finally:
        await engine.dispose()


async def _store_counts(database_url: str) -> tuple[int, int, dict[str, object]]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            community = int(
                await connection.scalar(text("SELECT count(*) FROM foods_community")) or 0
            )
            odbl = int(await connection.scalar(text("SELECT count(*) FROM foods_odbl")) or 0)
            stored = (
                await connection.execute(
                    text(
                        "SELECT database_license, contents_license, nutrients_json "
                        "FROM foods_odbl WHERE barcode = :barcode"
                    ),
                    {"barcode": BARCODE},
                )
            ).mappings().one()
            return community, odbl, dict(stored)
    finally:
        await engine.dispose()


async def _odbl_count(database_url: str) -> int:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            return int(await connection.scalar(text("SELECT count(*) FROM foods_odbl")) or 0)
    finally:
        await engine.dispose()


async def _reset_all_consumption_data(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    TRUNCATE auth_rate_limits, auth_sessions, log_entries,
                             recipe_ingredients, recipes, foods_reference,
                             foods_community, foods_odbl, foods_custom, users CASCADE
                    """
                )
            )
    finally:
        await engine.dispose()


async def _cache_same_product_concurrently(
    database_url: str, product: OpenFoodFactsProduct
) -> tuple[list[bool], int]:
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def store() -> bool:
        async with sessions() as database:
            response = await cache_product(database, product)
            return response.cached

    try:
        cached_states = await asyncio.gather(store(), store())
        async with engine.connect() as connection:
            row_count = int(
                await connection.scalar(
                    text("SELECT count(*) FROM foods_odbl WHERE barcode = :barcode"),
                    {"barcode": product.barcode},
                )
                or 0
            )
        return sorted(cached_states), row_count
    finally:
        await engine.dispose()


async def _insert_export_rows(database_url: str, *, first: int, last: int) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO foods_odbl (
                        barcode, product_name, nutrients_json, source_url, attribution_text
                    )
                    SELECT lpad(item::text, 14, '0'),
                           'Fixture product ' || item::text,
                           CAST(:nutrients AS jsonb),
                           'https://world.openfoodfacts.org/product/' ||
                               lpad(item::text, 14, '0'),
                           'Open Food Facts contributors'
                    FROM generate_series(CAST(:first AS integer), CAST(:last AS integer)) AS item
                    """
                ),
                {"first": first, "last": last, "nutrients": json.dumps(NUTRIENTS)},
            )
    finally:
        await engine.dispose()


def _settings(database_url: str, **overrides: object) -> Settings:
    return Settings(
        database_url=database_url,
        app_environment="test",
        open_food_facts_enabled=True,
        open_food_facts_base_url="https://off.example.test",
        **overrides,
        _env_file=None,
    )


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_lookup_is_opt_in_cached_license_isolated_and_exported_separately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_reset(INTEGRATION_DATABASE_URL))
    calls: list[str] = []

    async def fetch(_client: OpenFoodFactsClient, barcode: str) -> OpenFoodFactsProduct:
        calls.append(barcode)
        return OpenFoodFactsProduct(
            barcode=barcode,
            product_name="Fixture spread",
            brand="Fixture Foods",
            nutrients_json=NUTRIENTS,
            source_url=f"https://world.openfoodfacts.org/product/{barcode}",
        )

    monkeypatch.setattr(OpenFoodFactsClient, "fetch", fetch)
    with TestClient(create_app(_settings(INTEGRATION_DATABASE_URL))) as client:
        first = client.get(f"/api/v1/foods/barcode/{BARCODE}")
        second = client.get(f"/api/v1/foods/barcode/{BARCODE}")
        exported = client.get("/api/v1/export/foods/openfoodfacts")

    assert first.status_code == 200
    assert first.json()["cached"] is False
    assert second.status_code == 200
    assert second.json()["cached"] is True
    assert calls == [BARCODE]
    attribution = first.json()["attribution"]
    assert attribution["database_license"] == "ODbL-1.0"
    assert attribution["contents_license"] == "DbCL-1.0"
    assert exported.status_code == 200
    assert exported.json()["dataset"] == "opennosh-open-food-facts-cache"
    assert exported.json()["database_license"] == "ODbL-1.0"
    assert exported.json()["contents_license"] == "DbCL-1.0"
    assert [entry["barcode"] for entry in exported.json()["entries"]] == [BARCODE]

    community_count, odbl_count, stored = asyncio.run(
        _store_counts(INTEGRATION_DATABASE_URL)
    )
    assert community_count == 0
    assert odbl_count == 1
    assert stored["database_license"] == "ODbL-1.0"
    assert stored["contents_license"] == "DbCL-1.0"
    assert "image" not in str(stored["nutrients_json"]).casefold()


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_disabled_lookup_never_calls_open_food_facts(monkeypatch: pytest.MonkeyPatch) -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")

    async def forbidden_fetch(
        _client: OpenFoodFactsClient, _barcode: str
    ) -> OpenFoodFactsProduct:
        raise AssertionError("disabled integration attempted network access")

    monkeypatch.setattr(OpenFoodFactsClient, "fetch", forbidden_fetch)
    disabled = Settings(
        database_url=INTEGRATION_DATABASE_URL,
        app_environment="test",
        _env_file=None,
    )
    with TestClient(create_app(disabled)) as client:
        response = client.get(f"/api/v1/foods/barcode/{BARCODE}")

    assert response.status_code == 503
    assert response.json() == {"detail": "Open Food Facts barcode lookup is disabled."}


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_barcode_lookup_has_an_independent_local_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_reset(INTEGRATION_DATABASE_URL))

    async def fetch(_client: OpenFoodFactsClient, barcode: str) -> OpenFoodFactsProduct:
        return OpenFoodFactsProduct(
            barcode=barcode,
            product_name="Fixture spread",
            brand=None,
            nutrients_json=NUTRIENTS,
            source_url=f"https://world.openfoodfacts.org/product/{barcode}",
            attribution_text=OPEN_FOOD_FACTS_ATTRIBUTION,
        )

    monkeypatch.setattr(OpenFoodFactsClient, "fetch", fetch)
    settings = _settings(
        INTEGRATION_DATABASE_URL,
        open_food_facts_lookup_rate_limit_attempts=1,
    )
    with TestClient(create_app(settings), client=("203.0.113.18", 50_000)) as client:
        responses = [client.get(f"/api/v1/foods/barcode/{BARCODE}") for _ in range(2)]

    assert [response.status_code for response in responses] == [200, 429]
    assert int(responses[-1].headers["Retry-After"]) > 0


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
@pytest.mark.parametrize(
    ("upstream_error", "expected_status", "expected_retry_after"),
    [
        (OpenFoodFactsNotFoundError(), 404, None),
        (OpenFoodFactsTimeoutError(), 504, None),
        (OpenFoodFactsRateLimitedError("17"), 503, "17"),
        (OpenFoodFactsRateLimitedError("unsafe\nvalue"), 503, None),
        (OpenFoodFactsUpstreamError("fixture upstream error"), 502, None),
    ],
)
def test_upstream_failures_are_mapped_without_caching(
    monkeypatch: pytest.MonkeyPatch,
    upstream_error: Exception,
    expected_status: int,
    expected_retry_after: str | None,
) -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_reset(INTEGRATION_DATABASE_URL))

    async def fail(_client: OpenFoodFactsClient, _barcode: str) -> OpenFoodFactsProduct:
        raise upstream_error

    monkeypatch.setattr(OpenFoodFactsClient, "fetch", fail)
    with TestClient(create_app(_settings(INTEGRATION_DATABASE_URL))) as client:
        response = client.get(f"/api/v1/foods/barcode/{BARCODE}")

    assert response.status_code == expected_status
    assert response.headers.get("Retry-After") == expected_retry_after
    assert asyncio.run(_odbl_count(INTEGRATION_DATABASE_URL)) == 0


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_invalid_barcode_is_rejected_without_upstream_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert INTEGRATION_DATABASE_URL is not None

    async def forbidden_fetch(
        _client: OpenFoodFactsClient, _barcode: str
    ) -> OpenFoodFactsProduct:
        raise AssertionError("invalid barcode attempted network access")

    monkeypatch.setattr(OpenFoodFactsClient, "fetch", forbidden_fetch)
    with TestClient(create_app(_settings(INTEGRATION_DATABASE_URL))) as client:
        response = client.get("/api/v1/foods/barcode/3017620422004")

    assert response.status_code == 422


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_cache_misses_share_one_global_upstream_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_reset(INTEGRATION_DATABASE_URL))
    calls: list[str] = []

    async def fetch(_client: OpenFoodFactsClient, barcode: str) -> OpenFoodFactsProduct:
        calls.append(barcode)
        return OpenFoodFactsProduct(
            barcode=barcode,
            product_name="Fixture spread",
            brand=None,
            nutrients_json=NUTRIENTS,
            source_url=f"https://world.openfoodfacts.org/product/{barcode}",
        )

    monkeypatch.setattr(OpenFoodFactsClient, "fetch", fetch)
    settings = _settings(
        INTEGRATION_DATABASE_URL,
        open_food_facts_upstream_rate_limit_attempts=1,
    )
    app = create_app(settings)
    with TestClient(app, client=("203.0.113.20", 50_000)) as first_client:
        first = first_client.get(f"/api/v1/foods/barcode/{BARCODE}")
    with TestClient(app, client=("203.0.113.21", 50_001)) as second_client:
        second = second_client.get("/api/v1/foods/barcode/036000291452")

    assert first.status_code == 200
    assert second.status_code == 429
    assert calls == [BARCODE]


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_concurrent_same_barcode_cache_writes_are_conflict_safe() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_reset(INTEGRATION_DATABASE_URL))
    product = OpenFoodFactsProduct(
        barcode=BARCODE,
        product_name="Concurrent fixture spread",
        brand="Fixture Foods",
        nutrients_json=NUTRIENTS,
        source_url=f"https://world.openfoodfacts.org/product/{BARCODE}",
    )

    cached_states, row_count = asyncio.run(
        _cache_same_product_concurrently(INTEGRATION_DATABASE_URL, product)
    )

    assert cached_states == [False, True]
    assert row_count == 1


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_empty_export_has_an_independent_rate_limit() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_reset(INTEGRATION_DATABASE_URL))
    settings = _settings(
        INTEGRATION_DATABASE_URL,
        open_food_facts_export_rate_limit_attempts=1,
    )

    with TestClient(create_app(settings), client=("203.0.113.30", 50_000)) as client:
        empty = client.get("/api/v1/export/foods/openfoodfacts")
        limited = client.get("/api/v1/export/foods/openfoodfacts")

    assert empty.status_code == 200
    assert empty.json()["entries"] == []
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) > 0


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_export_accepts_exact_row_limit_and_rejects_one_more() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_reset(INTEGRATION_DATABASE_URL))
    asyncio.run(
        _insert_export_rows(
            INTEGRATION_DATABASE_URL,
            first=1,
            last=open_food_facts_service.EXPORT_ROW_LIMIT,
        )
    )
    settings = _settings(
        INTEGRATION_DATABASE_URL,
        open_food_facts_export_rate_limit_attempts=3,
    )

    with TestClient(create_app(settings), client=("203.0.113.31", 50_000)) as client:
        at_limit = client.get("/api/v1/export/foods/openfoodfacts")
        asyncio.run(
            _insert_export_rows(
                INTEGRATION_DATABASE_URL,
                first=open_food_facts_service.EXPORT_ROW_LIMIT + 1,
                last=open_food_facts_service.EXPORT_ROW_LIMIT + 1,
            )
        )
        over_limit = client.get("/api/v1/export/foods/openfoodfacts")

    assert at_limit.status_code == 200
    assert len(at_limit.json()["entries"]) == open_food_facts_service.EXPORT_ROW_LIMIT
    assert over_limit.status_code == 503
    assert over_limit.json()["detail"] == (
        "The Open Food Facts cache is temporarily too large for JSON export."
    )


def test_export_database_timeout_is_controlled_and_other_errors_propagate() -> None:
    class DatabaseFailure(Exception):
        def __init__(self, sqlstate: str) -> None:
            super().__init__(sqlstate)
            self.sqlstate = sqlstate

    class FailingDatabase:
        def __init__(self, sqlstate: str) -> None:
            self.error = DBAPIError("SELECT", {}, DatabaseFailure(sqlstate))
            self.rolled_back = False

        async def execute(self, *_args: object, **_kwargs: object) -> None:
            return None

        async def stream_scalars(self, *_args: object, **_kwargs: object) -> None:
            raise self.error

        async def rollback(self) -> None:
            self.rolled_back = True

    async def exercise() -> None:
        timed_out = FailingDatabase("57014")
        with pytest.raises(OpenFoodFactsExportTimeoutError):
            await export_cached_products(timed_out, statement_timeout_ms=100)  # type: ignore[arg-type]
        assert timed_out.rolled_back is True

        unexpected = FailingDatabase("08006")
        with pytest.raises(DBAPIError) as raised:
            await export_cached_products(unexpected, statement_timeout_ms=100)  # type: ignore[arg-type]
        assert raised.value is unexpected.error
        assert unexpected.rolled_back is False

    asyncio.run(exercise())


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_export_timeout_is_mapped_to_a_stable_api_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_reset(INTEGRATION_DATABASE_URL))

    async def timeout(*_args: object, **_kwargs: object) -> object:
        raise OpenFoodFactsExportTimeoutError

    monkeypatch.setattr(foods_router, "export_cached_products", timeout)
    with TestClient(create_app(_settings(INTEGRATION_DATABASE_URL))) as client:
        response = client.get("/api/v1/export/foods/openfoodfacts")

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Open Food Facts export timed out. Try again later."
    }


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_lookup_created_food_can_be_logged_and_used_in_a_private_recipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_reset_all_consumption_data(INTEGRATION_DATABASE_URL))

    async def fetch(_client: OpenFoodFactsClient, barcode: str) -> OpenFoodFactsProduct:
        return OpenFoodFactsProduct(
            barcode=barcode,
            product_name="Lookup-created spread",
            brand="Fixture Foods",
            nutrients_json=NUTRIENTS,
            source_url=f"https://world.openfoodfacts.org/product/{barcode}",
        )

    monkeypatch.setattr(OpenFoodFactsClient, "fetch", fetch)
    settings = _settings(INTEGRATION_DATABASE_URL, auth_rate_limit_attempts=50)
    with TestClient(create_app(settings)) as client:
        registered = client.post(
            "/api/v1/auth/register",
            json={
                "email": "off-consumer@example.test",
                "password": "correct horse battery staple",
            },
        )
        assert registered.status_code == 201
        csrf_token = registered.json()["csrf_token"]

        lookup = client.get(f"/api/v1/foods/barcode/{BARCODE}")
        logged = client.post(
            "/api/v1/logs",
            headers={"X-CSRF-Token": csrf_token},
            json={
                "logged_at": "2026-08-20T12:00:00Z",
                "meal_slot": "lunch",
                "food": {"source": "openfoodfacts", "source_id": BARCODE},
                "quantity": {"amount": "50", "unit": "g"},
            },
        )
        recipe = client.post(
            "/api/v1/recipes",
            headers={"X-CSRF-Token": csrf_token},
            json={
                "name": "Lookup-created recipe",
                "yield_grams": "100",
                "ingredients": [
                    {
                        "food": {"source": "openfoodfacts", "source_id": BARCODE},
                        "grams": "100",
                    }
                ],
            },
        )

    assert lookup.status_code == 200
    assert lookup.json()["source"] == "openfoodfacts"
    assert logged.status_code == 201
    assert logged.json()["food"] == {
        "source": "openfoodfacts",
        "source_id": BARCODE,
        "name": "Lookup-created spread",
    }
    assert logged.json()["snapshot"]["nutrients"]["energy_kcal"] == "269.50"
    assert recipe.status_code == 201
    assert recipe.json()["ingredients"][0]["food"] == {
        "source": "openfoodfacts",
        "source_id": BARCODE,
        "name": "Lookup-created spread",
    }
    community_count, odbl_count, _stored = asyncio.run(
        _store_counts(INTEGRATION_DATABASE_URL)
    )
    assert community_count == 0
    assert odbl_count == 1


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_export_caps_serialized_json_not_compressed_database_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_reset(INTEGRATION_DATABASE_URL))

    async def fetch(_client: OpenFoodFactsClient, barcode: str) -> OpenFoodFactsProduct:
        return OpenFoodFactsProduct(
            barcode=barcode,
            product_name="Fixture spread",
            brand=None,
            nutrients_json=NUTRIENTS,
            source_url=f"https://world.openfoodfacts.org/product/{barcode}",
        )

    monkeypatch.setattr(OpenFoodFactsClient, "fetch", fetch)
    with TestClient(create_app(_settings(INTEGRATION_DATABASE_URL))) as client:
        assert client.get(f"/api/v1/foods/barcode/{BARCODE}").status_code == 200
        monkeypatch.setattr(open_food_facts_service, "EXPORT_MAX_SERIALIZED_BYTES", 100)
        exported = client.get("/api/v1/export/foods/openfoodfacts")

    assert exported.status_code == 503
    assert exported.json()["detail"] == (
        "The Open Food Facts cache is temporarily too large for JSON export."
    )
