from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from alembic import command
from fastapi.testclient import TestClient
from opennosh_api.auth.dependencies import CurrentSession
from opennosh_api.main import create_app
from opennosh_api.models import AuthSession, User, Workout
from opennosh_api.settings import Settings
from opennosh_api.workouts.schemas import WorkoutSetWrite
from opennosh_api.workouts.service import add_workout_set
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.tests.test_migrations import migration_config

INTEGRATION_DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL")


@dataclass(frozen=True)
class WorkoutClients:
    owner: TestClient
    attacker: TestClient
    anonymous: TestClient
    owner_csrf: str
    attacker_csrf: str
    exercise_id: str
    second_exercise_id: str


async def _reset_and_seed(database_url: str) -> tuple[str, str]:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    TRUNCATE auth_rate_limits, auth_sessions, workout_sets,
                             workouts, exercises, users CASCADE
                    """
                )
            )
            exercise_id = await connection.scalar(
                text(
                    """
                    INSERT INTO exercises (
                        slug, name, muscle_groups, equipment, source, source_id,
                        source_url, derivative_source_url, license_spdx,
                        license_url, author, author_url, attribution_text,
                        translation_attribution_json
                    ) VALUES (
                        'barbell-squat', 'Barbell squat', '["quadriceps"]'::jsonb,
                        '["barbell"]'::jsonb, 'wger', '1',
                        'https://wger.de/en/exercise/1/view',
                        'https://wger.de/en/exercise/1/view', 'CC-BY-SA-3.0',
                        'https://creativecommons.org/licenses/by-sa/3.0/',
                        'wger contributors', 'https://wger.de/',
                        'Exercise data from wger contributors', '[]'::jsonb
                    ) RETURNING id
                    """
                )
            )
            second_id = await connection.scalar(
                text(
                    """
                    INSERT INTO exercises (
                        slug, name, muscle_groups, equipment, source, source_id,
                        source_url, license_spdx, license_url, attribution_text,
                        translation_attribution_json
                    ) VALUES (
                        'cable-row', 'Cable row', '["back"]'::jsonb,
                        '["cable"]'::jsonb, 'wger', '2',
                        'https://wger.de/en/exercise/2/view', 'CC-BY-SA-3.0',
                        'https://creativecommons.org/licenses/by-sa/3.0/',
                        'Exercise data from wger contributors', '[]'::jsonb
                    ) RETURNING id
                    """
                )
            )
            return str(exercise_id), str(second_id)
    finally:
        await engine.dispose()


async def _seed_reverse_order_sets(
    database_url: str, workout_id: str, exercise_id: str
) -> list[str]:
    engine = create_async_engine(database_url)
    ids_by_position = [str(uuid4()) for _ in range(3)]
    try:
        async with engine.begin() as connection:
            user_id = await connection.scalar(
                text("SELECT id FROM users WHERE email = 'workout-owner@example.test'")
            )
            for position in (2, 1, 0):
                await connection.execute(
                    text(
                        """
                        INSERT INTO workout_sets (
                            id, user_id, workout_id, exercise_id, set_index, reps,
                            load_value, load_unit
                        ) VALUES (
                            CAST(:id AS uuid), :user_id, CAST(:workout_id AS uuid),
                            CAST(:exercise_id AS uuid), :position, 5, 100, 'kg'
                        )
                        """
                    ),
                    {
                        "id": ids_by_position[position],
                        "user_id": user_id,
                        "workout_id": workout_id,
                        "exercise_id": exercise_id,
                        "position": position,
                    },
                )
        return ids_by_position
    finally:
        await engine.dispose()


async def _append_sets_concurrently(database_url: str, workout_id: str, exercise_id: str) -> None:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as first, factory() as second:

            async def current(session: AsyncSession) -> CurrentSession:
                row = (
                    await session.execute(
                        select(User, AuthSession)
                        .join(AuthSession, AuthSession.user_id == User.id)
                        .where(User.email == "workout-owner@example.test")
                    )
                ).first()
                assert row is not None
                return CurrentSession(user=row[0], session=row[1])

            first_current, second_current = await asyncio.gather(current(first), current(second))
            first_payload = WorkoutSetWrite.model_validate(
                {
                    "exercise_id": exercise_id,
                    "reps": 6,
                    "load_value": "90",
                    "load_unit": "kg",
                }
            )
            second_payload = WorkoutSetWrite.model_validate(
                {
                    "exercise_id": exercise_id,
                    "reps": 7,
                    "load_value": "95",
                    "load_unit": "kg",
                }
            )
            workout_uuid = UUID(workout_id)
            await first.scalar(select(Workout).where(Workout.id == workout_uuid).with_for_update())
            second_started = asyncio.Event()

            async def blocked_append() -> None:
                second_started.set()
                await add_workout_set(second, workout_uuid, second_payload, second_current)

            second_task = asyncio.create_task(blocked_append())
            await second_started.wait()
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(second_task), timeout=0.25)

            await add_workout_set(first, workout_uuid, first_payload, first_current)
            await asyncio.wait_for(second_task, timeout=2)
    finally:
        await engine.dispose()


@pytest.fixture
def workout_clients() -> Iterator[WorkoutClients]:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    exercise_id, second_exercise_id = asyncio.run(_reset_and_seed(INTEGRATION_DATABASE_URL))
    settings = Settings(
        database_url=INTEGRATION_DATABASE_URL,
        app_environment="test",
        auth_rate_limit_attempts=50,
        _env_file=None,
    )
    with (
        TestClient(create_app(settings)) as owner,
        TestClient(create_app(settings)) as attacker,
        TestClient(create_app(settings)) as anonymous,
    ):
        owner_registration = owner.post(
            "/api/v1/auth/register",
            json={"email": "workout-owner@example.test", "password": "owner password 123"},
        )
        attacker_registration = attacker.post(
            "/api/v1/auth/register",
            json={
                "email": "workout-attacker@example.test",
                "password": "attacker password 123",
            },
        )
        assert owner_registration.status_code == 201
        assert attacker_registration.status_code == 201
        yield WorkoutClients(
            owner=owner,
            attacker=attacker,
            anonymous=anonymous,
            owner_csrf=owner_registration.json()["csrf_token"],
            attacker_csrf=attacker_registration.json()["csrf_token"],
            exercise_id=exercise_id,
            second_exercise_id=second_exercise_id,
        )


def _set(
    exercise_id: str,
    *,
    reps: int = 5,
    load_value: str | None = "100",
    load_unit: str = "kg",
) -> dict[str, object]:
    return {
        "exercise_id": exercise_id,
        "reps": reps,
        "load_value": load_value,
        "load_unit": load_unit,
    }


def _create(
    clients: WorkoutClients,
    *,
    performed_at: str = "2026-08-20T23:30:00-04:00",
    sets: list[dict[str, object]] | None = None,
):
    return clients.owner.post(
        "/api/v1/workouts",
        headers={"X-CSRF-Token": clients.owner_csrf},
        json={
            "performed_at": performed_at,
            "notes": "  evening session  ",
            "sets": sets
            if sets is not None
            else [
                _set(clients.exercise_id),
                _set(
                    clients.exercise_id,
                    reps=8,
                    load_value=None,
                    load_unit="bodyweight",
                ),
                _set(
                    clients.second_exercise_id,
                    reps=10,
                    load_value="12",
                    load_unit="machine_units",
                ),
            ],
        },
    )


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_workout_and_set_crud_preserve_order_and_attribution(
    workout_clients: WorkoutClients,
) -> None:
    created = _create(workout_clients)
    assert created.status_code == 201
    assert created.headers["cache-control"] == "no-store"
    body = created.json()
    assert body["performed_at"] == "2026-08-21T03:30:00Z"
    assert body["notes"] == "evening session"
    assert [item["position"] for item in body["sets"]] == [0, 1, 2]
    assert body["sets"][0]["volume"] == "500.000"
    assert body["sets"][1]["volume"] is None
    assert body["sets"][0]["exercise"]["license_spdx"] == "CC-BY-SA-3.0"
    assert body["sets"][0]["exercise"]["attribution_text"] == (
        "Exercise data from wger contributors"
    )
    assert {
        (group["exercise_id"], group["load_unit"], group["volume"])
        for group in body["volume_groups"]
    } == {
        (workout_clients.exercise_id, "kg", "500.000"),
        (workout_clients.second_exercise_id, "machine_units", "120.000"),
    }

    workout_id = body["id"]
    original_ids = [item["id"] for item in body["sets"]]
    appended = workout_clients.owner.post(
        f"/api/v1/workouts/{workout_id}/sets",
        headers={"X-CSRF-Token": workout_clients.owner_csrf},
        json=_set(workout_clients.exercise_id, reps=2, load_value="225", load_unit="lb"),
    )
    assert appended.status_code == 200
    assert [item["position"] for item in appended.json()["sets"]] == [0, 1, 2, 3]

    edited = workout_clients.owner.put(
        f"/api/v1/workouts/{workout_id}/sets/{original_ids[1]}",
        headers={"X-CSRF-Token": workout_clients.owner_csrf},
        json=_set(
            workout_clients.exercise_id,
            reps=12,
            load_value=None,
            load_unit="band",
        ),
    )
    assert edited.status_code == 200
    assert edited.json()["sets"][1]["position"] == 1
    assert edited.json()["sets"][1]["load_unit"] == "band"

    deleted_set = workout_clients.owner.delete(
        f"/api/v1/workouts/{workout_id}/sets/{original_ids[0]}",
        headers={"X-CSRF-Token": workout_clients.owner_csrf},
    )
    assert deleted_set.status_code == 200
    remaining = deleted_set.json()["sets"]
    assert [item["id"] for item in remaining] == [
        original_ids[1],
        original_ids[2],
        appended.json()["sets"][3]["id"],
    ]
    assert [item["position"] for item in remaining] == [0, 1, 2]

    updated = workout_clients.owner.put(
        f"/api/v1/workouts/{workout_id}",
        headers={"X-CSRF-Token": workout_clients.owner_csrf},
        json={"performed_at": "2026-08-21T12:00:00Z", "notes": "updated"},
    )
    assert updated.status_code == 200
    assert [item["id"] for item in updated.json()["sets"]] == [item["id"] for item in remaining]

    listed = workout_clients.owner.get(
        "/api/v1/workouts",
        params={"from": "2026-08-21", "to": "2026-08-21", "limit": 1},
    )
    assert listed.status_code == 200
    assert listed.json()["items"] == [updated.json()]
    assert listed.json()["has_more"] is False
    assert workout_clients.owner.get(f"/api/v1/workouts/{workout_id}").json() == (updated.json())

    removed = workout_clients.owner.delete(
        f"/api/v1/workouts/{workout_id}",
        headers={"X-CSRF-Token": workout_clients.owner_csrf},
    )
    assert removed.status_code == 204
    assert workout_clients.owner.get(f"/api/v1/workouts/{workout_id}").status_code == 404


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_workout_list_paginates_with_stable_offset_and_has_more(
    workout_clients: WorkoutClients,
) -> None:
    older = _create(workout_clients, performed_at="2026-08-20T12:00:00Z", sets=[])
    newer = _create(workout_clients, performed_at="2026-08-20T13:00:00Z", sets=[])
    assert older.status_code == newer.status_code == 201

    first_page = workout_clients.owner.get(
        "/api/v1/workouts",
        params={"from": "2026-08-20", "to": "2026-08-20", "limit": 1},
    )
    second_page = workout_clients.owner.get(
        "/api/v1/workouts",
        params={
            "from": "2026-08-20",
            "to": "2026-08-20",
            "limit": 1,
            "offset": 1,
        },
    )

    assert first_page.status_code == second_page.status_code == 200
    assert [item["id"] for item in first_page.json()["items"]] == [newer.json()["id"]]
    assert first_page.json()["offset"] == 0
    assert first_page.json()["has_more"] is True
    assert [item["id"] for item in second_page.json()["items"]] == [older.json()["id"]]
    assert second_page.json()["offset"] == 1
    assert second_page.json()["has_more"] is False


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_append_rejects_a_501st_set_without_changing_the_workout(
    workout_clients: WorkoutClients,
) -> None:
    created = _create(
        workout_clients,
        sets=[_set(workout_clients.exercise_id)] * 500,
    )
    assert created.status_code == 201
    workout_id = created.json()["id"]
    original_set_ids = [item["id"] for item in created.json()["sets"]]

    rejected = workout_clients.owner.post(
        f"/api/v1/workouts/{workout_id}/sets",
        headers={"X-CSRF-Token": workout_clients.owner_csrf},
        json=_set(workout_clients.exercise_id),
    )

    assert rejected.status_code == 422
    assert rejected.json()["detail"] == "A workout may contain at most 500 sets"
    detail = workout_clients.owner.get(f"/api/v1/workouts/{workout_id}")
    assert detail.status_code == 200
    assert [item["id"] for item in detail.json()["sets"]] == original_set_ids
    assert [item["position"] for item in detail.json()["sets"]] == list(range(500))


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_append_and_update_reject_unknown_exercises_without_changing_state(
    workout_clients: WorkoutClients,
) -> None:
    created = _create(
        workout_clients,
        sets=[_set(workout_clients.exercise_id)],
    )
    assert created.status_code == 201
    original = created.json()
    workout_id = original["id"]
    set_id = original["sets"][0]["id"]
    missing_exercise = str(uuid4())

    appended = workout_clients.owner.post(
        f"/api/v1/workouts/{workout_id}/sets",
        headers={"X-CSRF-Token": workout_clients.owner_csrf},
        json=_set(missing_exercise),
    )
    assert appended.status_code == 404
    assert appended.headers["cache-control"] == "no-store"
    assert workout_clients.owner.get(f"/api/v1/workouts/{workout_id}").json() == original

    updated = workout_clients.owner.put(
        f"/api/v1/workouts/{workout_id}/sets/{set_id}",
        headers={"X-CSRF-Token": workout_clients.owner_csrf},
        json=_set(missing_exercise),
    )
    assert updated.status_code == 404
    assert updated.headers["cache-control"] == "no-store"
    assert workout_clients.owner.get(f"/api/v1/workouts/{workout_id}").json() == original


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_set_deletion_compacts_rows_independent_of_database_row_order(
    workout_clients: WorkoutClients,
) -> None:
    assert INTEGRATION_DATABASE_URL is not None
    created = _create(workout_clients, sets=[])
    assert created.status_code == 201
    workout_id = created.json()["id"]
    ids_by_position = asyncio.run(
        _seed_reverse_order_sets(INTEGRATION_DATABASE_URL, workout_id, workout_clients.exercise_id)
    )

    deleted = workout_clients.owner.delete(
        f"/api/v1/workouts/{workout_id}/sets/{ids_by_position[0]}",
        headers={"X-CSRF-Token": workout_clients.owner_csrf},
    )

    assert deleted.status_code == 200
    assert [item["id"] for item in deleted.json()["sets"]] == ids_by_position[1:]
    assert [item["position"] for item in deleted.json()["sets"]] == [0, 1]


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_concurrent_set_appends_receive_distinct_contiguous_positions(
    workout_clients: WorkoutClients,
) -> None:
    assert INTEGRATION_DATABASE_URL is not None
    created = _create(workout_clients, sets=[])
    assert created.status_code == 201

    asyncio.run(
        _append_sets_concurrently(
            INTEGRATION_DATABASE_URL,
            created.json()["id"],
            workout_clients.exercise_id,
        )
    )

    detail = workout_clients.owner.get(f"/api/v1/workouts/{created.json()['id']}")
    assert detail.status_code == 200
    assert [item["position"] for item in detail.json()["sets"]] == [0, 1]
    assert {item["reps"] for item in detail.json()["sets"]} == {6, 7}


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_volume_endpoint_refuses_cross_unit_aggregation(
    workout_clients: WorkoutClients,
) -> None:
    created = _create(
        workout_clients,
        performed_at="2026-08-20T12:00:00Z",
        sets=[
            _set(workout_clients.exercise_id, reps=5, load_value="100", load_unit="kg"),
            _set(workout_clients.exercise_id, reps=2, load_value="225", load_unit="lb"),
            _set(
                workout_clients.exercise_id,
                reps=10,
                load_value="8",
                load_unit="rpe_only",
            ),
        ],
    )
    assert created.status_code == 201

    params = {
        "from": "2026-08-20",
        "to": "2026-08-20",
        "exercise_id": workout_clients.exercise_id,
    }
    mixed = workout_clients.owner.get("/api/v1/workouts/volume", params=params)
    assert mixed.status_code == 422
    assert "incompatible load units" in mixed.json()["detail"]
    assert mixed.headers["cache-control"] == "no-store"

    kg = workout_clients.owner.get("/api/v1/workouts/volume", params={**params, "load_unit": "kg"})
    pounds = workout_clients.owner.get(
        "/api/v1/workouts/volume", params={**params, "load_unit": "lb"}
    )
    assert kg.json()["volume"] == "500.000"
    assert pounds.json()["volume"] == "450.000"
    assert kg.json()["qualifying_sets"] == pounds.json()["qualifying_sets"] == 1
    for qualitative_unit in ("bodyweight", "band", "rpe_only"):
        qualitative = workout_clients.owner.get(
            "/api/v1/workouts/volume",
            params={**params, "load_unit": qualitative_unit},
        )
        assert qualitative.status_code == 422
        assert qualitative.json()["detail"] == f"Volume is not defined for {qualitative_unit}"


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_workout_operations_are_authenticated_csrf_protected_and_tenant_isolated(
    workout_clients: WorkoutClients,
) -> None:
    created = _create(workout_clients)
    assert created.status_code == 201
    workout_id = created.json()["id"]
    set_id = created.json()["sets"][0]["id"]
    original_detail = workout_clients.owner.get(f"/api/v1/workouts/{workout_id}").json()
    second_workout = _create(workout_clients, sets=[]).json()["id"]
    missing_workout = str(uuid4())
    missing_set = str(uuid4())

    attacker_list = workout_clients.attacker.get(
        "/api/v1/workouts", params={"from": "2026-08-21", "to": "2026-08-21"}
    )
    assert attacker_list.status_code == 200
    assert attacker_list.json()["items"] == []

    for actual, missing in (
        (
            workout_clients.attacker.get(f"/api/v1/workouts/{workout_id}"),
            workout_clients.attacker.get(f"/api/v1/workouts/{missing_workout}"),
        ),
        (
            workout_clients.attacker.put(
                f"/api/v1/workouts/{workout_id}",
                headers={"X-CSRF-Token": workout_clients.attacker_csrf},
                json={"performed_at": "2026-08-21T12:00:00Z", "notes": None},
            ),
            workout_clients.attacker.put(
                f"/api/v1/workouts/{missing_workout}",
                headers={"X-CSRF-Token": workout_clients.attacker_csrf},
                json={"performed_at": "2026-08-21T12:00:00Z", "notes": None},
            ),
        ),
        (
            workout_clients.attacker.delete(
                f"/api/v1/workouts/{workout_id}",
                headers={"X-CSRF-Token": workout_clients.attacker_csrf},
            ),
            workout_clients.attacker.delete(
                f"/api/v1/workouts/{missing_workout}",
                headers={"X-CSRF-Token": workout_clients.attacker_csrf},
            ),
        ),
        (
            workout_clients.attacker.post(
                f"/api/v1/workouts/{workout_id}/sets",
                headers={"X-CSRF-Token": workout_clients.attacker_csrf},
                json=_set(workout_clients.exercise_id),
            ),
            workout_clients.attacker.post(
                f"/api/v1/workouts/{missing_workout}/sets",
                headers={"X-CSRF-Token": workout_clients.attacker_csrf},
                json=_set(workout_clients.exercise_id),
            ),
        ),
        (
            workout_clients.attacker.put(
                f"/api/v1/workouts/{workout_id}/sets/{set_id}",
                headers={"X-CSRF-Token": workout_clients.attacker_csrf},
                json=_set(workout_clients.exercise_id),
            ),
            workout_clients.attacker.put(
                f"/api/v1/workouts/{missing_workout}/sets/{missing_set}",
                headers={"X-CSRF-Token": workout_clients.attacker_csrf},
                json=_set(workout_clients.exercise_id),
            ),
        ),
        (
            workout_clients.attacker.delete(
                f"/api/v1/workouts/{workout_id}/sets/{set_id}",
                headers={"X-CSRF-Token": workout_clients.attacker_csrf},
            ),
            workout_clients.attacker.delete(
                f"/api/v1/workouts/{missing_workout}/sets/{missing_set}",
                headers={"X-CSRF-Token": workout_clients.attacker_csrf},
            ),
        ),
    ):
        assert actual.status_code == missing.status_code == 404
        assert actual.json() == missing.json()

    wrong_parent = workout_clients.owner.put(
        f"/api/v1/workouts/{second_workout}/sets/{set_id}",
        headers={"X-CSRF-Token": workout_clients.owner_csrf},
        json=_set(workout_clients.exercise_id),
    )
    missing_under_parent = workout_clients.owner.put(
        f"/api/v1/workouts/{second_workout}/sets/{missing_set}",
        headers={"X-CSRF-Token": workout_clients.owner_csrf},
        json=_set(workout_clients.exercise_id),
    )
    assert wrong_parent.status_code == missing_under_parent.status_code == 404
    assert wrong_parent.json() == missing_under_parent.json()

    attacker_volume = workout_clients.attacker.get(
        "/api/v1/workouts/volume",
        params={
            "from": "2026-08-21",
            "to": "2026-08-21",
            "exercise_id": workout_clients.exercise_id,
        },
    )
    assert attacker_volume.status_code == 200
    assert attacker_volume.json()["volume"] is None
    assert attacker_volume.json()["qualifying_sets"] == 0

    csrf_cases = (
        (
            "post",
            "/api/v1/workouts",
            {"json": {"performed_at": "2026-08-21T12:00:00Z", "sets": []}},
        ),
        (
            "put",
            f"/api/v1/workouts/{workout_id}",
            {"json": {"performed_at": "2026-08-21T12:00:00Z", "notes": None}},
        ),
        ("delete", f"/api/v1/workouts/{workout_id}", {}),
        (
            "post",
            f"/api/v1/workouts/{workout_id}/sets",
            {"json": _set(workout_clients.exercise_id)},
        ),
        (
            "put",
            f"/api/v1/workouts/{workout_id}/sets/{set_id}",
            {"json": _set(workout_clients.exercise_id)},
        ),
        ("delete", f"/api/v1/workouts/{workout_id}/sets/{set_id}", {}),
    )
    for method, path, kwargs in csrf_cases:
        missing_csrf = getattr(workout_clients.owner, method)(path, **kwargs)
        assert missing_csrf.status_code == 403
        assert missing_csrf.headers["cache-control"] == "no-store"

    assert workout_clients.owner.get(f"/api/v1/workouts/{workout_id}").json() == original_detail

    for method, path, kwargs in (
        (
            "get",
            "/api/v1/workouts",
            {"params": {"from": "2026-08-21", "to": "2026-08-21"}},
        ),
        ("get", f"/api/v1/workouts/{workout_id}", {}),
        (
            "get",
            "/api/v1/workouts/volume",
            {
                "params": {
                    "from": "2026-08-21",
                    "to": "2026-08-21",
                    "exercise_id": workout_clients.exercise_id,
                }
            },
        ),
        (
            "post",
            "/api/v1/workouts",
            {"json": {"performed_at": "2026-08-21T12:00:00Z", "sets": []}},
        ),
        (
            "post",
            f"/api/v1/workouts/{workout_id}/sets",
            {"json": _set(workout_clients.exercise_id)},
        ),
        (
            "put",
            f"/api/v1/workouts/{workout_id}/sets/{set_id}",
            {"json": _set(workout_clients.exercise_id)},
        ),
        ("delete", f"/api/v1/workouts/{workout_id}/sets/{set_id}", {}),
        ("delete", f"/api/v1/workouts/{workout_id}", {}),
    ):
        response = getattr(workout_clients.anonymous, method)(path, **kwargs)
        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store"


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_invalid_exercises_dates_and_timestamp_sentinels_are_private(
    workout_clients: WorkoutClients,
) -> None:
    missing_exercise = _create(workout_clients, sets=[_set(str(uuid4()))])
    assert missing_exercise.status_code == 404
    assert missing_exercise.headers["cache-control"] == "no-store"

    minimum_sentinel = _create(workout_clients, performed_at="0001-01-01T00:00:00Z", sets=[])
    minimum = _create(workout_clients, performed_at="0001-01-01T00:00:00.000001Z", sets=[])
    maximum_sentinel = _create(workout_clients, performed_at="9999-12-31T23:59:59.999999Z", sets=[])
    maximum = _create(workout_clients, performed_at="9999-12-31T23:59:59.999998Z", sets=[])
    assert minimum_sentinel.status_code == maximum_sentinel.status_code == 422
    assert minimum.status_code == maximum.status_code == 201

    maximum_list = workout_clients.owner.get(
        "/api/v1/workouts", params={"from": "9999-12-31", "to": "9999-12-31"}
    )
    assert maximum_list.status_code == 200
    assert maximum_list.json()["items"] == [maximum.json()]

    reversed_range = workout_clients.owner.get(
        "/api/v1/workouts", params={"from": "2026-08-21", "to": "2026-08-20"}
    )
    assert reversed_range.status_code == 422
    assert reversed_range.json()["detail"] == "from must be on or before to"


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_list_and_volume_use_the_same_half_open_utc_day_boundary(
    workout_clients: WorkoutClients,
) -> None:
    before_midnight = _create(
        workout_clients,
        performed_at="2026-08-20T23:59:59.999999Z",
        sets=[_set(workout_clients.exercise_id, reps=2, load_value="100")],
    )
    at_midnight = _create(
        workout_clients,
        performed_at="2026-08-21T00:00:00Z",
        sets=[_set(workout_clients.exercise_id, reps=3, load_value="100")],
    )
    assert before_midnight.status_code == at_midnight.status_code == 201

    params = {"from": "2026-08-20", "to": "2026-08-20"}
    listed = workout_clients.owner.get("/api/v1/workouts", params=params)
    volume = workout_clients.owner.get(
        "/api/v1/workouts/volume",
        params={**params, "exercise_id": workout_clients.exercise_id},
    )

    assert [item["id"] for item in listed.json()["items"]] == [before_midnight.json()["id"]]
    assert volume.status_code == 200
    assert volume.json()["volume"] == "200.000"
    assert volume.json()["qualifying_sets"] == 1
