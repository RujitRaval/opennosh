from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from opennosh_api.capacity import ProcessRole
from opennosh_api.entrypoints import _worker, migration, web
from opennosh_api.runtime import (
    ROLE_COMPOSITIONS,
    role_accepts_lane,
    supervise_role,
)


def test_every_process_role_has_one_isolated_composition() -> None:
    assert set(ROLE_COMPOSITIONS) == set(ProcessRole)
    owned_lanes: set[str] = set()
    for role, composition in ROLE_COMPOSITIONS.items():
        assert composition.role is role
        assert composition.database_url_environment == f"{role.value.upper()}_DATABASE_URL"
        assert owned_lanes.isdisjoint(composition.lanes)
        owned_lanes.update(composition.lanes)


def test_old_role_leaves_unknown_job_lane_unclaimed() -> None:
    assert role_accepts_lane(ProcessRole.PUBLICATION, "publication") is True
    assert role_accepts_lane(ProcessRole.PUBLICATION, "future-v2-job") is False


@pytest.mark.parametrize(
    "module",
    [
        "opennosh_api.entrypoints.publication",
        "opennosh_api.entrypoints.evidence",
        "opennosh_api.entrypoints.projection",
        "opennosh_api.entrypoints.reconciler",
        "opennosh_api.entrypoints.scheduler",
    ],
)
def test_worker_entrypoint_import_does_not_import_web_application(module: str) -> None:
    code = f"import {module}; import sys; raise SystemExit('opennosh_api.main' in sys.modules)"
    result = subprocess.run([sys.executable, "-c", code], check=False)
    assert result.returncode == 0


def test_disabled_worker_entrypoint_refuses_to_start() -> None:
    with pytest.raises(ValueError, match="no replica allocation"):
        _worker.run_reserved_worker(ProcessRole.PUBLICATION)


def test_enabled_worker_without_driver_refuses_to_claim_lanes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = Path(__file__).resolve().parents[2] / "config/database-capacity.v1.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["roles"]["publication"]["replicas"] = 1
    manifest_path = tmp_path / "capacity.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        _worker,
        "get_settings",
        lambda: SimpleNamespace(database_capacity_manifest_path=manifest_path),
    )

    with pytest.raises(RuntimeError, match="no installed queue driver"):
        _worker.run_reserved_worker(ProcessRole.PUBLICATION)


def test_web_entrypoint_uses_the_isolated_web_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(web.uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))

    web.main()

    assert calls == [
        (("opennosh_api.main:app",), {"host": "0.0.0.0", "port": 8000, "access_log": False})
    ]


def test_migration_entrypoint_uses_the_migration_role_url_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, str, str | None]] = []
    monkeypatch.delenv("MIGRATION_DATABASE_URL", raising=False)
    settings = SimpleNamespace(
        process_database_url=lambda role: (
            "postgresql+asyncpg://migration@database/opennosh"
            if role.value == "migration"
            else pytest.fail("wrong database role")
        )
    )
    monkeypatch.setattr(migration, "get_settings", lambda: settings)
    monkeypatch.setattr(
        migration.command,
        "upgrade",
        lambda config, revision: calls.append(
            (config, revision, migration.os.environ.get("MIGRATION_DATABASE_URL"))
        ),
    )

    migration.main()

    assert len(calls) == 1
    assert calls[0][1] == "head"
    assert calls[0][2] is not None
    assert calls[0][2].startswith("postgresql+asyncpg://migration@")
    assert "MIGRATION_DATABASE_URL" not in migration.os.environ


class RecordingDriver:
    def __init__(self) -> None:
        self.actions: list[str] = []

    async def start(self) -> None:
        self.actions.append("start")

    def stop_claiming(self) -> None:
        self.actions.append("stop_claiming")

    async def drain(self) -> None:
        self.actions.append("drain")

    async def close(self) -> None:
        self.actions.append("close")


@pytest.mark.asyncio
async def test_role_supervisor_stops_claiming_then_drains_before_close() -> None:
    driver = RecordingDriver()
    shutdown = asyncio.Event()
    shutdown.set()

    await supervise_role(driver, shutdown, drain_timeout_seconds=1)

    assert driver.actions == ["start", "stop_claiming", "drain", "close"]


class FailingStartDriver(RecordingDriver):
    async def start(self) -> None:
        self.actions.append("start")
        raise RuntimeError("startup failed")


class BlockingDrainDriver(RecordingDriver):
    async def drain(self) -> None:
        self.actions.append("drain")
        await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_role_supervisor_closes_after_partial_start_failure() -> None:
    driver = FailingStartDriver()

    with pytest.raises(RuntimeError, match="startup failed"):
        await supervise_role(driver, asyncio.Event(), drain_timeout_seconds=1)

    assert driver.actions == ["start", "close"]


@pytest.mark.asyncio
async def test_role_supervisor_closes_when_drain_deadline_expires() -> None:
    driver = BlockingDrainDriver()
    shutdown = asyncio.Event()
    shutdown.set()

    with pytest.raises(TimeoutError):
        await supervise_role(driver, shutdown, drain_timeout_seconds=0.001)

    assert driver.actions == ["start", "stop_claiming", "drain", "close"]
