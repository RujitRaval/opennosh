from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from fastapi.testclient import TestClient
from opennosh_api.exercises.service import SEARCH_PLAN_MAX_EXECUTION_MS, _search_statement
from opennosh_api.importers.wger import import_wger
from opennosh_api.main import create_app
from opennosh_api.models import Exercise
from opennosh_api.settings import Settings
from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.tests.test_migrations import migration_config

INTEGRATION_DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL")
FIXTURE = Path(__file__).parents[1] / "fixtures" / "wger" / "valid.json"


def _plan_nodes(node: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = [node]
    for child in node.get("Plans", []):
        nodes.extend(_plan_nodes(child))
    return nodes


async def _reset_and_seed(database_url: str) -> tuple[str, str]:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE workout_sets, workouts, exercises CASCADE"))
            await connection.execute(
                text(
                    "DELETE FROM auth_rate_limits "
                    "WHERE scope IN ('exercise-search-ip', 'exercise-export-ip')"
                )
            )
        async with factory() as session, session.begin():
            report = await import_wger(session, [FIXTURE])
            assert report.rows_inserted == 2
            private_exercise = Exercise(
                slug="private-custom-movement",
                name="Private custom movement",
                muscle_groups=["core"],
                equipment=[],
                source="private",
                source_id="private-1",
                source_url="https://example.test/exercises/private-1",
                license_spdx="CC0-1.0",
                license_url="https://creativecommons.org/publicdomain/zero/1.0/",
                attribution_text="Private user-authored exercise",
            )
            session.add(private_exercise)
            await session.flush()
        async with factory() as session:
            exercise_id = await session.scalar(
                select(Exercise.id).where(Exercise.source == "wger", Exercise.source_id == "101")
            )
            assert exercise_id is not None
            return str(exercise_id), str(private_exercise.id)
    finally:
        await engine.dispose()


def _client(database_url: str, **settings: object) -> TestClient:
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
def test_search_detail_and_export_return_complete_safe_attribution() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    squat_id, private_id = asyncio.run(_reset_and_seed(INTEGRATION_DATABASE_URL))

    with _client(INTEGRATION_DATABASE_URL) as client:
        first = client.get(
            "/api/v1/exercises/search",
            params={"q": "back", "limit": 1},
        )
        second = client.get(
            "/api/v1/exercises/search",
            params={"q": "back", "limit": 1, "offset": 1},
        )
        filtered = client.get(
            "/api/v1/exercises/search",
            params={"q": "squat", "muscle": "QUADS", "equipment": "Barbell"},
        )
        wrong_filter = client.get(
            "/api/v1/exercises/search",
            params={"q": "squat", "equipment": "cable"},
        )
        translated = client.get("/api/v1/exercises/search", params={"q": "Kniebeuge"})
        alias = client.get("/api/v1/exercises/search", params={"q": "Cable Row"})
        note = client.get("/api/v1/exercises/search", params={"q": "knees tracking"})
        exact = client.get(
            "/api/v1/exercises/search", params={"q": "Barbell Back Squat", "limit": 2}
        )
        detail = client.get(f"/api/v1/exercises/{squat_id}")
        missing = client.get("/api/v1/exercises/00000000-0000-0000-0000-000000000000")
        private_search = client.get("/api/v1/exercises/search", params={"q": "private"})
        private_detail = client.get(f"/api/v1/exercises/{private_id}")
        attributed_export = client.get("/api/v1/export/exercises")

    assert first.status_code == second.status_code == 200
    assert first.json()["has_more"] is True
    assert second.json()["has_more"] is False
    assert filtered.status_code == 200
    assert [item["slug"] for item in filtered.json()["items"]] == ["wger-101"]
    assert wrong_filter.json()["items"] == []
    assert [item["slug"] for item in translated.json()["items"]] == ["wger-101"]
    assert [item["slug"] for item in alias.json()["items"]] == ["wger-102"]
    assert [item["slug"] for item in note.json()["items"]] == ["wger-101"]
    assert exact.json()["items"][0]["slug"] == "wger-101"
    assert detail.status_code == 200
    body = detail.json()
    assert body["name"] == "Barbell Back Squat"
    assert body["translations"][0]["description"] == (
        "Brace your torso and squat with the bar across your upper back."
    )
    assert body["attribution"]["license_spdx"] == "CC-BY-SA-3.0"
    assert body["attribution"]["source_url"].startswith("https://wger.de/")
    assert len(body["attribution"]["translations"]) == 2
    assert "<" not in detail.text and ">" not in detail.text
    assert missing.status_code == 404
    assert private_search.json()["items"] == []
    assert private_detail.status_code == 404
    export_body = attributed_export.json()
    assert attributed_export.status_code == 200
    assert export_body["dataset"] == "opennosh-wger-exercises"
    assert export_body["license_spdx"] == "CC-BY-SA-3.0"
    assert "ShareAlike" in export_body["share_alike_notice"]
    assert len(export_body["entries"]) == 2
    assert all(
        item["attribution"]["license_spdx"] == "CC-BY-SA-3.0" for item in export_body["entries"]
    )


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_search_rejects_invalid_bounds_and_rate_limits_public_clients() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_reset_and_seed(INTEGRATION_DATABASE_URL))

    with _client(
        INTEGRATION_DATABASE_URL,
        exercise_search_rate_limit_attempts=20,
    ) as client:
        invalid_responses = [
            client.get("/api/v1/exercises/search", params=params)
            for params in (
                {"q": ""},
                {"q": "a"},
                {"q": "--"},
                {"q": "a\x00"},
                {"q": "squat", "muscle": "<script>"},
                {"q": "squat", "equipment": "\x00"},
                {"q": "squat", "limit": 51},
                {"q": "squat", "offset": 10_001},
            )
        ]
    assert all(response.status_code == 422 for response in invalid_responses)

    asyncio.run(_reset_and_seed(INTEGRATION_DATABASE_URL))
    with TestClient(
        create_app(
            Settings(
                database_url=INTEGRATION_DATABASE_URL,
                app_environment="test",
                exercise_search_rate_limit_attempts=1,
                exercise_search_rate_limit_window_seconds=60,
                _env_file=None,
            )
        ),
        client=("203.0.113.75", 50_000),
    ) as client:
        responses = [
            client.get("/api/v1/exercises/search", params={"q": "squat"}) for _ in range(2)
        ]

    assert [response.status_code for response in responses] == [200, 429]
    assert int(responses[-1].headers["retry-after"]) > 0


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_export_rate_limits_public_clients_and_enforces_byte_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_reset_and_seed(INTEGRATION_DATABASE_URL))

    with _client(INTEGRATION_DATABASE_URL, exercise_export_rate_limit_attempts=1) as client:
        responses = [client.get("/api/v1/export/exercises") for _ in range(2)]
    assert [response.status_code for response in responses] == [200, 429]

    asyncio.run(_reset_and_seed(INTEGRATION_DATABASE_URL))
    monkeypatch.setattr("opennosh_api.exercises.service.EXPORT_MAX_DATABASE_BYTES", 1)
    with _client(INTEGRATION_DATABASE_URL) as client:
        too_large = client.get("/api/v1/export/exercises")
    assert too_large.status_code == 503
    assert too_large.json()["detail"] == (
        "Exercise export is temporarily too large for the JSON endpoint."
    )


async def _explain_representative_search(database_url: str) -> tuple[float, set[str]]:
    engine = create_async_engine(database_url)
    try:
        statement = _search_statement(
            query="needle",
            muscle=None,
            equipment=None,
            limit=20,
            offset=0,
        )
        compiled = statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE workout_sets, workouts, exercises CASCADE"))
            await connection.execute(
                text(
                    """
                    INSERT INTO exercises (
                        slug, name, muscle_groups, equipment, search_text, source,
                        source_id, source_url, license_spdx, license_url, author,
                        attribution_text, translations_json, translation_attribution_json
                    )
                    SELECT
                        'wger-' || value,
                        CASE WHEN value = 5000 THEN 'Needle overhead press'
                             ELSE 'Catalogue movement ' || value END,
                        CASE WHEN value = 5000 THEN '["rare-muscle"]'::jsonb
                             ELSE '["shoulders"]'::jsonb END,
                        CASE WHEN value = 5000 THEN '["rare-machine"]'::jsonb
                             ELSE '["barbell"]'::jsonb END,
                        CASE WHEN value = 5000 THEN 'needle overhead press shoulders barbell'
                             ELSE 'catalogue movement shoulders barbell ' || value END,
                        'wger', value::text,
                        'https://wger.de/api/v2/exerciseinfo/' || value || '/',
                        'CC-BY-SA-3.0',
                        'https://creativecommons.org/licenses/by-sa/3.0/',
                        'wger contributors', 'wger attribution', '[]'::jsonb, '[]'::jsonb
                    FROM generate_series(1, 10000) AS value
                    """
                )
            )
            await connection.execute(text("ANALYZE exercises"))
            payload = await connection.scalar(
                text(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {compiled}")
            )
            taxonomy_payloads = []
            for taxonomy_sql in (
                "SELECT id FROM exercises "
                "WHERE muscle_groups @> '[\"rare-muscle\"]'::jsonb",
                "SELECT id FROM exercises "
                "WHERE equipment @> '[\"rare-machine\"]'::jsonb",
            ):
                taxonomy_payloads.append(
                    await connection.scalar(
                        text(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {taxonomy_sql}")
                    )
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
        for taxonomy_payload in taxonomy_payloads:
            if isinstance(taxonomy_payload, str):
                taxonomy_payload = json.loads(taxonomy_payload)
            assert isinstance(taxonomy_payload, list)
            taxonomy_report = taxonomy_payload[0]
            index_names.update(
                str(node["Index Name"])
                for node in _plan_nodes(taxonomy_report["Plan"])
                if node.get("Index Name") is not None
            )
            report["Execution Time"] = max(
                float(report["Execution Time"]), float(taxonomy_report["Execution Time"])
            )
        return float(report["Execution Time"]), index_names
    finally:
        await engine.dispose()


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_representative_search_uses_declared_indexes_within_budget() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")

    execution_ms, index_names = asyncio.run(
        _explain_representative_search(INTEGRATION_DATABASE_URL)
    )

    assert execution_ms < SEARCH_PLAN_MAX_EXECUTION_MS
    assert {
        "ix_exercises_search_tsv",
        "ix_exercises_name_trgm",
        "ix_exercises_muscle_groups_gin",
        "ix_exercises_equipment_gin",
    }.issubset(index_names)
