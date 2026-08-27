from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Self, cast

import pytest
from fastapi import Request
from opennosh_api import capacity, database
from opennosh_api.capacity import (
    CapacityManifest,
    ConnectionBudget,
    ProcessRole,
    load_capacity_manifest,
    main,
    parse_deployed_role_counts,
    preflight_report,
    validate_deployed_role_counts,
    validate_live_connection_ceiling,
)
from opennosh_api.database import (
    DatabaseIdentity,
    DatabasePoolMetrics,
    build_engine,
    get_database_session,
)
from opennosh_api.problems.handlers import ProblemException
from opennosh_api.problems.schemas import ProblemCode
from pydantic import ValidationError
from sqlalchemy.exc import TimeoutError as SqlAlchemyTimeoutError
from sqlalchemy.ext.asyncio import AsyncEngine


def manifest_payload() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "config/database-capacity.v1.json"
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def test_canonical_manifest_reserves_recovery_capacity() -> None:
    manifest = load_capacity_manifest()
    report = preflight_report(manifest)

    assert manifest.roles[ProcessRole.WEB].allocated_connections == 12
    assert report["total_committed_connections"] == 38
    assert report["uncommitted_connections"] == 65
    assert manifest.reserved_headroom.recovery == 6
    assert manifest.reserved_headroom.failover == 6


def test_invalid_replica_pool_total_fails_preflight() -> None:
    payload = manifest_payload()
    payload["roles"]["web"]["replicas"] = 10

    with pytest.raises(ValidationError, match="exceed the PostgreSQL ceiling"):
        CapacityManifest.model_validate(payload)


def test_unbudgeted_overflow_and_incomplete_roles_are_rejected() -> None:
    overflow = manifest_payload()
    overflow["roles"]["web"]["max_overflow"] = 1
    with pytest.raises(ValidationError):
        CapacityManifest.model_validate(overflow)

    incomplete = manifest_payload()
    del incomplete["roles"]["scheduler"]
    with pytest.raises(ValidationError, match="Role budgets must be complete"):
        CapacityManifest.model_validate(incomplete)

    no_reserved_evidence_connection = manifest_payload()
    no_reserved_evidence_connection["roles"]["evidence"]["pool_size"] = 1
    no_reserved_evidence_connection["roles"]["evidence"][
        "max_in_flight_database_sections"
    ] = 1
    with pytest.raises(ValidationError, match="at least two connections"):
        CapacityManifest.model_validate(no_reserved_evidence_connection)


def test_inactive_worker_role_fails_closed() -> None:
    manifest = load_capacity_manifest()

    with pytest.raises(ValueError, match="no replica allocation"):
        manifest.active_role_budget(ProcessRole.EVIDENCE)


def test_preflight_command_returns_nonzero_for_invalid_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "invalid.json"
    path.write_text("{", encoding="utf-8")

    assert main(["--manifest", str(path)]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "invalid"


def test_deployment_topology_must_match_every_manifest_replica() -> None:
    manifest = load_capacity_manifest()
    deployed = parse_deployed_role_counts(
        [
            "web=1",
            "publication=1",
            "evidence=0",
            "projection=0",
            "reconciler=0",
            "scheduler=0",
        ]
    )

    validate_deployed_role_counts(manifest, deployed)
    deployed[ProcessRole.WEB] = 2
    with pytest.raises(ValueError, match="replica counts do not match"):
        validate_deployed_role_counts(manifest, deployed)


def test_incomplete_or_duplicate_deployment_topology_is_rejected() -> None:
    manifest = load_capacity_manifest()
    with pytest.raises(ValueError, match="duplicated"):
        parse_deployed_role_counts(["web=1", "web=2"])
    with pytest.raises(ValueError, match="must declare every process role"):
        validate_deployed_role_counts(manifest, parse_deployed_role_counts(["web=1"]))


def test_live_database_ceiling_must_match_manifest() -> None:
    manifest = load_capacity_manifest()

    validate_live_connection_ceiling(manifest, 103)
    with pytest.raises(ValueError, match="live=100, declared=103"):
        validate_live_connection_ceiling(manifest, 100)


def test_preflight_requires_and_checks_live_database(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DATABASE_CAPACITY_URL", raising=False)
    assert main(["--require-live-database"]) == 2
    assert "DATABASE_CAPACITY_URL is required" in capsys.readouterr().out

    async def fake_live_ceiling(_database_url: str) -> int:
        return 103

    monkeypatch.setenv("DATABASE_CAPACITY_URL", "postgresql+asyncpg://unused")
    monkeypatch.setattr(capacity, "live_postgresql_connection_ceiling", fake_live_ceiling)
    assert main(["--require-live-database"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "valid"


def test_database_identity_preserves_unique_role_suffix_when_truncated() -> None:
    deployment_id = "a" * 63
    web = DatabaseIdentity(deployment_id=deployment_id, role="web").application_name
    migration = DatabaseIdentity(deployment_id=deployment_id, role="migration").application_name

    assert len(web) <= 63
    assert len(migration) <= 63
    assert web.endswith(":web")
    assert migration.endswith(":migration")
    assert web != migration


def test_engine_receives_only_explicit_bounded_pool_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    fake_engine = cast(AsyncEngine, object())

    def fake_create_async_engine(url: str, **kwargs: object) -> AsyncEngine:
        captured["url"] = url
        captured.update(kwargs)
        return fake_engine

    monkeypatch.setattr(database, "create_async_engine", fake_create_async_engine)
    budget = ConnectionBudget(
        pool_size=3,
        max_overflow=0,
        acquisition_timeout_ms=250,
        statement_timeout_ms=900,
        max_in_flight_database_sections=3,
    )
    identity = DatabaseIdentity(deployment_id="test-deployment", role="web")

    assert (
        build_engine(
            "postgresql+asyncpg://unused",
            identity=identity,
            budget=budget,
        )
        is fake_engine
    )
    assert captured["pool_size"] == 3
    assert captured["max_overflow"] == 0
    assert captured["pool_timeout"] == 0.25
    assert captured["connect_args"] == {
        "server_settings": {
            "application_name": "opennosh:test-deployment:web",
            "statement_timeout": "900",
        }
    }


class CancelledSession:
    async def __aenter__(self) -> CancelledSession:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def connection(self) -> None:
        raise asyncio.CancelledError


class TimingOutSession:
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def connection(self) -> None:
        raise SqlAlchemyTimeoutError("pool full")


def test_metrics_are_role_attributed() -> None:
    metrics = DatabasePoolMetrics(DatabaseIdentity(deployment_id="test-deployment", role="web"), 2)
    started = metrics.begin_acquisition()
    metrics.checked_out()
    metrics.acquired(started)
    metrics.checked_in()

    snapshot = metrics.snapshot()
    assert snapshot["deployment_id"] == "test-deployment"
    assert snapshot["role"] == "web"
    assert snapshot["active"] == 0
    assert snapshot["idle"] == 1
    assert snapshot["waiting"] == 0
    assert snapshot["acquisition_count"] == 1


@pytest.mark.asyncio
async def test_pool_timeout_becomes_typed_retryable_overload() -> None:
    metrics = DatabasePoolMetrics(DatabaseIdentity(deployment_id="test-deployment", role="web"), 1)
    app = SimpleNamespace(
        state=SimpleNamespace(
            database_pool_metrics=metrics,
            session_factory=lambda: TimingOutSession(),
        )
    )
    request = Request({"type": "http", "app": app})
    dependency = get_database_session(request)

    with pytest.raises(ProblemException) as captured:
        await anext(dependency)

    assert captured.value.code is ProblemCode.DATABASE_CAPACITY_EXHAUSTED
    assert captured.value.status == 503
    assert captured.value.retry_after == 1
    assert metrics.snapshot()["timed_out_total"] == 1
    assert metrics.snapshot()["waiting"] == 0


def test_invalidated_checkin_does_not_report_a_phantom_idle_connection() -> None:
    metrics = DatabasePoolMetrics(DatabaseIdentity(deployment_id="test-deployment", role="web"), 1)
    metrics.checked_out()
    metrics.checked_in(returned_to_pool=False)

    assert metrics.snapshot()["active"] == 0
    assert metrics.snapshot()["idle"] == 0


@pytest.mark.asyncio
async def test_cancelled_acquisition_clears_waiting_gauge() -> None:
    metrics = DatabasePoolMetrics(DatabaseIdentity(deployment_id="test-deployment", role="web"), 1)
    app = SimpleNamespace(
        state=SimpleNamespace(
            database_pool_metrics=metrics,
            session_factory=lambda: CancelledSession(),
        )
    )
    request = Request({"type": "http", "app": app})

    with pytest.raises(asyncio.CancelledError):
        await anext(get_database_session(request))

    assert metrics.snapshot()["waiting"] == 0
