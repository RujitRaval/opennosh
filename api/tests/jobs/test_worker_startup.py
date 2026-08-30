from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from opennosh_api.jobs import JobLane, JobMessage
from opennosh_api.jobs.pgqueuer import encode_message
from opennosh_api.jobs.worker import (
    PublicationActivationWakeup,
    PublicationActivationWakeupOutcome,
    _run_publication_worker,
    create_publication_role_driver,
    ensure_publication_activation_wakeup,
    supervise_publication_claims,
    validate_production_adapter_registry,
)
from opennosh_api.publication.state import (
    EffectIntent,
    ExternalObservation,
    PublicationState,
    PublicationStepName,
)

ROOT = Path(__file__).resolve().parents[3]


class _AsyncContext:
    def __init__(self, value: object) -> None:
        self.value = value

    async def __aenter__(self) -> object:
        return self.value

    async def __aexit__(self, *_arguments: object) -> None:
        return None


class _RecoveryConnection:
    def __init__(
        self,
        *,
        intent: dict[str, object],
        active: list[dict[str, object]] | None = None,
        conflict: dict[str, object] | None = None,
    ) -> None:
        self.intent = intent
        self.active = active or []
        self.conflict = conflict
        self.fetchrow_calls = 0

    def transaction(self) -> _AsyncContext:
        return _AsyncContext(self)

    async def fetchrow(self, _query: str, *_arguments: object) -> object:
        self.fetchrow_calls += 1
        return self.intent if self.fetchrow_calls == 1 else self.conflict

    async def fetch(self, _query: str, *_arguments: object) -> object:
        return self.active


class _RecoveryPool:
    def __init__(self, connection: _RecoveryConnection) -> None:
        self.connection = connection

    def acquire(self) -> _AsyncContext:
        return _AsyncContext(self.connection)


def _activation_message(
    publication_id: UUID,
    *,
    revision: int | None,
    key: str = "publication-activation-test",
) -> bytes:
    return encode_message(
        JobMessage(
            lane=JobLane.PUBLICATION,
            job_type="publication.wake",
            subject_id=publication_id,
            idempotency_key=key,
            workflow_revision=revision,
        )
    )


@pytest.mark.asyncio
async def test_activation_recovery_rejects_a_naive_clock_before_database_access() -> None:
    class ForbiddenPool:
        def acquire(self) -> None:
            raise AssertionError("naive clock reached the database")

    with pytest.raises(ValueError, match="timezone"):
        await ensure_publication_activation_wakeup(
            ForbiddenPool(),
            UUID("00000000-0000-4000-8000-000000000001"),
            now=datetime(2026, 8, 28, 22),
        )


@pytest.mark.parametrize("revision", [None, 2])
@pytest.mark.asyncio
async def test_activation_recovery_rejects_unbound_or_future_active_work(
    revision: int | None,
) -> None:
    publication_id = UUID("00000000-0000-4000-8000-000000000001")
    now = datetime(2026, 8, 28, 22, tzinfo=UTC)
    connection = _RecoveryConnection(
        intent={
            "state": "pending",
            "workflow_revision": 1,
            "next_attempt_at": now,
        },
        active=[
            {
                "id": 7,
                "status": "queued",
                "execute_after": now,
                "heartbeat": None,
                "payload": _activation_message(publication_id, revision=revision),
            }
        ],
    )

    with pytest.raises(RuntimeError, match="conflicts with the configured intent"):
        await ensure_publication_activation_wakeup(
            _RecoveryPool(connection), publication_id, now=now
        )


@pytest.mark.parametrize("conflict", ["matching", "missing", "mismatched"])
@pytest.mark.asyncio
async def test_activation_recovery_reconciles_enqueue_conflicts_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    conflict: str,
) -> None:
    publication_id = UUID("00000000-0000-4000-8000-000000000001")
    now = datetime(2026, 8, 28, 22, tzinfo=UTC)
    key = f"publication:{publication_id}:activation-recovery:1"
    conflict_row = {
        "id": 9,
        "payload": _activation_message(
            (
                publication_id
                if conflict != "mismatched"
                else UUID("00000000-0000-4000-8000-000000000002")
            ),
            revision=1,
            key=key,
        ),
    }
    connection = _RecoveryConnection(
        intent={
            "state": "pending",
            "workflow_revision": 1,
            "next_attempt_at": now,
        },
        conflict=None if conflict == "missing" else conflict_row,
    )

    class Queries:
        async def enqueue(self, *_arguments: object, **_keywords: object) -> tuple[None]:
            return (None,)

    monkeypatch.setattr("opennosh_api.jobs.worker.AsyncpgDriver", lambda value: value)
    monkeypatch.setattr("opennosh_api.jobs.worker.build_queries", lambda _driver: Queries())

    if conflict == "matching":
        outcome = await ensure_publication_activation_wakeup(
            _RecoveryPool(connection), publication_id, now=now
        )
        assert outcome.outcome is PublicationActivationWakeupOutcome.EXISTING
        assert outcome.job_id == 9
        return

    message = (
        "neither enqueued nor already present"
        if conflict == "missing"
        else "dedupe key is bound to another message"
    )
    with pytest.raises(RuntimeError, match=message):
        await ensure_publication_activation_wakeup(
            _RecoveryPool(connection), publication_id, now=now
        )


class Adapter:
    def __init__(self, *, identity: str = "test-adapter", version: str = "1.0") -> None:
        self.identity = identity
        self.version = version

    async def apply(self, _intent: EffectIntent) -> None:
        return None

    async def observe(self, _intent: EffectIntent) -> ExternalObservation:
        raise AssertionError("startup validation must not execute adapters")


def complete_registry() -> dict[PublicationStepName, Adapter]:
    return {step: Adapter(identity=f"adapter:{step.value}") for step in PublicationStepName}


def test_production_adapter_registry_requires_every_canonical_step() -> None:
    registry = complete_registry()
    registry.pop(PublicationStepName.COPY_RECEIPT)

    with pytest.raises(RuntimeError, match=r"missing=copy_receipt"):
        validate_production_adapter_registry(registry)


def test_production_adapter_registry_rejects_extra_or_unidentified_adapters() -> None:
    extra_registry = cast(dict[Any, Adapter], complete_registry())
    extra_registry["unexpected"] = Adapter()
    with pytest.raises(RuntimeError, match=r"extra=unexpected"):
        validate_production_adapter_registry(extra_registry)

    broken_contract = complete_registry()
    broken_contract[PublicationStepName.COMMIT_RECORD] = cast(Any, object())
    with pytest.raises(RuntimeError, match=r"violates the adapter contract"):
        validate_production_adapter_registry(broken_contract)

    blank_identity = complete_registry()
    blank_identity[PublicationStepName.COMMIT_RECORD] = Adapter(identity=" ")
    with pytest.raises(RuntimeError, match=r"requires identity and version"):
        validate_production_adapter_registry(blank_identity)


def test_production_adapter_registry_accepts_the_exact_contract() -> None:
    registry = complete_registry()
    assert validate_production_adapter_registry(registry) is registry


@pytest.mark.asyncio
async def test_production_worker_rejects_missing_adapters_before_opening_a_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def forbidden_pool(**_arguments: object) -> None:
        raise AssertionError("database pool opened before adapter validation")

    monkeypatch.setattr("opennosh_api.jobs.worker.asyncpg.create_pool", forbidden_pool)
    settings = SimpleNamespace(app_environment="production")

    with pytest.raises(RuntimeError, match="requires canonical adapters"):
        await create_publication_role_driver(cast(Any, settings))


@pytest.mark.asyncio
async def test_production_claims_build_live_registry_before_pool_and_own_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle: list[str] = []
    registry = complete_registry()

    class Pool:
        def get_max_size(self) -> int:
            return 4

        async def close(self) -> None:
            lifecycle.append("pool:close")

    pool = Pool()

    class Prepared:
        runtime = SimpleNamespace(adapters=registry)

        def bind_pool(self, supplied_pool: object) -> object:
            assert supplied_pool is pool
            lifecycle.append("registry:bind")
            return registry

        async def aclose(self) -> None:
            lifecycle.append("providers:close")

    prepared = Prepared()

    async def prepare(
        _settings: object,
        *,
        clock: object,
    ) -> Prepared:
        assert callable(clock)
        lifecycle.append("registry:prepare")
        return prepared

    async def create_pool(**arguments: object) -> Pool:
        assert arguments["min_size"] == 1
        lifecycle.append("pool:create")
        return pool

    async def ensure_wakeup(
        supplied_pool: object,
        activation_id: UUID,
        *,
        now: object,
    ) -> PublicationActivationWakeup:
        assert supplied_pool is pool
        assert activation_id == UUID("00000000-0000-4000-8000-000000000001")
        assert now is not None
        lifecycle.append("activation:ensure")
        return PublicationActivationWakeup(
            outcome=PublicationActivationWakeupOutcome.EXISTING,
            state=PublicationState.PENDING,
            workflow_revision=0,
            active_jobs=1,
            eligible=True,
            job_id=1,
        )

    monkeypatch.setattr(
        "opennosh_api.jobs.worker.PreparedProductionPublicationRuntime.from_settings",
        prepare,
    )
    monkeypatch.setattr("opennosh_api.jobs.worker.asyncpg.create_pool", create_pool)
    monkeypatch.setattr(
        "opennosh_api.jobs.worker.ensure_publication_activation_wakeup",
        ensure_wakeup,
    )
    settings = SimpleNamespace(
        app_environment="production",
        publication_claims_enabled=True,
        publication_activation_id=UUID("00000000-0000-4000-8000-000000000001"),
        database_capacity_manifest_path=ROOT / "config/database-capacity.v1.json",
        process_database_url=lambda _role: "postgresql+asyncpg://publication:secret@db/opennosh",
    )

    driver = await create_publication_role_driver(cast(Any, settings))

    assert lifecycle == [
        "registry:prepare",
        "pool:create",
        "registry:bind",
        "activation:ensure",
    ]
    await driver.close()
    assert lifecycle[-2:] == ["pool:close", "providers:close"]


@pytest.mark.asyncio
async def test_continuous_claims_skip_single_activation_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = complete_registry()

    class Pool:
        async def close(self) -> None:
            return None

    pool = Pool()

    class Prepared:
        runtime = SimpleNamespace(adapters=registry)

        def bind_pool(self, supplied_pool: object) -> object:
            assert supplied_pool is pool
            return registry

        async def aclose(self) -> None:
            return None

    async def prepare(_settings: object, *, clock: object) -> Prepared:
        assert callable(clock)
        return Prepared()

    async def create_pool(**_arguments: object) -> Pool:
        return pool

    async def forbidden_wakeup(*_arguments: object, **_options: object) -> None:
        raise AssertionError("continuous claims attempted single-activation recovery")

    sentinel = object()

    def assemble(**arguments: object) -> object:
        assert arguments["pool"] is pool
        assert arguments["adapters"] is registry
        return sentinel

    monkeypatch.setattr(
        "opennosh_api.jobs.worker.PreparedProductionPublicationRuntime.from_settings",
        prepare,
    )
    monkeypatch.setattr("opennosh_api.jobs.worker.asyncpg.create_pool", create_pool)
    monkeypatch.setattr(
        "opennosh_api.jobs.worker.ensure_publication_activation_wakeup",
        forbidden_wakeup,
    )
    monkeypatch.setattr(
        "opennosh_api.jobs.worker._assemble_publication_role_driver",
        assemble,
    )
    settings = SimpleNamespace(
        app_environment="production",
        publication_claims_enabled=True,
        publication_continuous_claims_enabled=True,
        publication_claim_concurrency=1,
        publication_activation_id=None,
        database_capacity_manifest_path=ROOT / "config/database-capacity.v1.json",
        process_database_url=lambda _role: "postgresql+asyncpg://publication:secret@db/opennosh",
    )

    driver = await create_publication_role_driver(cast(Any, settings))

    assert driver is sentinel


@pytest.mark.parametrize(
    "failure",
    [
        "manifest",
        "budget",
        "pool",
        "bind",
        "activation",
        "recover",
        "terminal",
        "assemble",
    ],
)
@pytest.mark.asyncio
async def test_production_claim_startup_failure_closes_every_created_resource(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    lifecycle: list[str] = []
    registry = complete_registry()

    class Pool:
        def get_max_size(self) -> int:
            return 4

        async def close(self) -> None:
            lifecycle.append("pool:close")

    pool = Pool()

    class Prepared:
        runtime = SimpleNamespace(adapters=registry)

        def bind_pool(self, _pool: object) -> object:
            if failure == "bind":
                raise RuntimeError("bind failed")
            return registry

        async def aclose(self) -> None:
            lifecycle.append("providers:close")

    prepared = Prepared()

    async def prepare(_settings: object, *, clock: object) -> Prepared:
        assert callable(clock)
        return prepared

    async def create_pool(**_arguments: object) -> Pool:
        if failure == "pool":
            raise RuntimeError("pool failed")
        return pool

    class Manifest:
        def active_role_budget(self, _role: object) -> object:
            if failure == "budget":
                raise RuntimeError("budget failed")
            return SimpleNamespace(
                pool_size=4,
                acquisition_timeout_ms=5000,
                statement_timeout_ms=30000,
            )

        deployment_id = "test"

    def load_manifest(_path: object) -> Manifest:
        if failure == "manifest":
            raise RuntimeError("manifest failed")
        return Manifest()

    async def ensure_wakeup(
        _pool: object,
        _activation_id: UUID,
        *,
        now: object,
    ) -> PublicationActivationWakeup:
        assert now is not None
        if failure == "recover":
            raise RuntimeError("recover failed")
        return PublicationActivationWakeup(
            outcome=(
                PublicationActivationWakeupOutcome.TERMINAL
                if failure == "terminal"
                else PublicationActivationWakeupOutcome.EXISTING
            ),
            state=(
                PublicationState.PUBLISHED if failure == "terminal" else PublicationState.PENDING
            ),
            workflow_revision=0,
            active_jobs=1,
            eligible=True,
            job_id=1,
        )

    def assemble(**_arguments: object) -> None:
        raise RuntimeError("assemble failed")

    monkeypatch.setattr(
        "opennosh_api.jobs.worker.PreparedProductionPublicationRuntime.from_settings",
        prepare,
    )
    monkeypatch.setattr(
        "opennosh_api.jobs.worker.load_capacity_manifest",
        load_manifest,
    )
    monkeypatch.setattr("opennosh_api.jobs.worker.asyncpg.create_pool", create_pool)
    monkeypatch.setattr(
        "opennosh_api.jobs.worker.ensure_publication_activation_wakeup",
        ensure_wakeup,
    )
    if failure == "assemble":
        monkeypatch.setattr(
            "opennosh_api.jobs.worker._assemble_publication_role_driver",
            assemble,
        )
    settings = SimpleNamespace(
        app_environment="production",
        publication_claims_enabled=True,
        publication_activation_id=(
            None if failure == "activation" else UUID("00000000-0000-4000-8000-000000000001")
        ),
        database_capacity_manifest_path=ROOT / "config/database-capacity.v1.json",
        process_database_url=lambda _role: "postgresql+asyncpg://publication:secret@db/opennosh",
    )

    with pytest.raises(RuntimeError, match=failure):
        await create_publication_role_driver(cast(Any, settings))

    if failure in {"manifest", "budget", "pool"}:
        assert lifecycle == ["providers:close"]
    else:
        assert lifecycle == ["pool:close", "providers:close"]


@pytest.mark.asyncio
async def test_refresh_only_worker_never_constructs_the_queue_driver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(
        latest_refresh_enabled=True,
        publication_claims_enabled=False,
        latest_refresh_interval_seconds=3600.0,
    )
    service = cast(Any, object())
    calls: list[tuple[object, object, float]] = []

    async def forbidden_queue_driver(**_arguments: object) -> None:
        raise AssertionError("refresh-only mode constructed the publication queue")

    async def capture_refresh_loop(
        supplied_service: object,
        shutdown: object,
        *,
        interval_seconds: float,
    ) -> None:
        calls.append((supplied_service, shutdown, interval_seconds))

    monkeypatch.setattr(
        "opennosh_api.jobs.worker.create_publication_role_driver",
        forbidden_queue_driver,
    )
    monkeypatch.setattr(
        "opennosh_api.jobs.worker.run_latest_pointer_refresh_loop",
        capture_refresh_loop,
    )

    await _run_publication_worker(
        settings=cast(Any, settings),
        refresh_service=service,
    )

    assert len(calls) == 1
    assert calls[0][0] is service
    assert calls[0][2] == 3600.0


@pytest.mark.asyncio
async def test_preactivation_smoke_runs_before_refresh_without_claim_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(
        latest_refresh_enabled=True,
        publication_claims_enabled=False,
        publication_preactivation_smoke_enabled=True,
        latest_refresh_interval_seconds=3600.0,
    )
    lifecycle: list[str] = []
    warnings: list[str] = []

    def warning(message: str, *arguments: object) -> None:
        warnings.append(message % arguments)

    async def smoke(_settings: object, *, clock: object) -> tuple[str, ...]:
        assert callable(clock)
        lifecycle.append("smoke")
        return tuple(step.value for step in PublicationStepName)

    async def forbidden_queue_driver(**_arguments: object) -> None:
        raise AssertionError("preactivation constructed the publication queue")

    async def refresh(
        _service: object,
        _shutdown: object,
        *,
        interval_seconds: float,
    ) -> None:
        assert interval_seconds == 3600.0
        lifecycle.append("refresh")

    monkeypatch.setattr(
        "opennosh_api.jobs.worker.run_zero_claim_preactivation_smoke",
        smoke,
    )
    monkeypatch.setattr(
        "opennosh_api.jobs.worker.create_publication_role_driver",
        forbidden_queue_driver,
    )
    monkeypatch.setattr(
        "opennosh_api.jobs.worker.run_latest_pointer_refresh_loop",
        refresh,
    )
    monkeypatch.setattr(
        "opennosh_api.jobs.worker.logger.warning",
        warning,
    )

    await _run_publication_worker(
        settings=cast(Any, settings),
        refresh_service=cast(Any, object()),
    )

    assert lifecycle == ["smoke", "refresh"]
    steps = ",".join(step.value for step in PublicationStepName)
    proof = (
        "Zero-claim publication preactivation smoke passed "
        f"adapter_count=10 steps={steps} claims_enabled=false"
    )
    assert warnings == [proof]


@pytest.mark.asyncio
async def test_combined_claims_and_refresh_share_one_shutdown_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(
        latest_refresh_enabled=True,
        publication_claims_enabled=True,
        latest_refresh_interval_seconds=3600.0,
    )
    shutdown = asyncio.Event()
    claims_started = asyncio.Event()
    refresh_started = asyncio.Event()
    claims_stopped = asyncio.Event()
    lifecycle: list[str] = []

    class Driver:
        async def start(self) -> None:
            lifecycle.append("claims:start")
            claims_started.set()

        def stop_claiming(self) -> None:
            lifecycle.append("claims:stop")
            claims_stopped.set()

        async def drain(self) -> None:
            await claims_stopped.wait()
            lifecycle.append("claims:drain")

        async def close(self) -> None:
            lifecycle.append("claims:close")

    driver = Driver()
    service = cast(Any, object())

    async def create_driver(**_arguments: object) -> Driver:
        return driver

    async def run_refresh(
        supplied_service: object,
        supplied_shutdown: asyncio.Event,
        *,
        interval_seconds: float,
    ) -> None:
        assert supplied_service is service
        assert supplied_shutdown is shutdown
        assert interval_seconds == 3600.0
        lifecycle.append("refresh:start")
        refresh_started.set()
        await supplied_shutdown.wait()
        lifecycle.append("refresh:stop")

    monkeypatch.setattr(
        "opennosh_api.jobs.worker.create_publication_role_driver",
        create_driver,
    )
    monkeypatch.setattr(
        "opennosh_api.jobs.worker.run_latest_pointer_refresh_loop",
        run_refresh,
    )

    task = asyncio.create_task(
        _run_publication_worker(
            settings=cast(Any, settings),
            refresh_service=service,
            shutdown_requested=shutdown,
        )
    )
    await asyncio.wait_for(
        asyncio.gather(claims_started.wait(), refresh_started.wait()),
        timeout=1,
    )
    shutdown.set()
    await asyncio.wait_for(task, timeout=1)

    assert lifecycle[:2] == ["claims:start", "refresh:start"]
    assert set(lifecycle[2:]) == {
        "claims:stop",
        "claims:drain",
        "claims:close",
        "refresh:stop",
    }
    assert lifecycle.index("claims:stop") < lifecycle.index("claims:drain")
    assert lifecycle.index("claims:drain") < lifecycle.index("claims:close")


@pytest.mark.asyncio
async def test_claims_supervisor_fails_when_queue_exits_before_shutdown() -> None:
    lifecycle: list[str] = []

    class Driver:
        async def start(self) -> None:
            lifecycle.append("start")

        def stop_claiming(self) -> None:
            lifecycle.append("stop")

        async def drain(self) -> None:
            lifecycle.append("exit")

        async def close(self) -> None:
            lifecycle.append("close")

    with pytest.raises(RuntimeError, match="claims loop exited"):
        await supervise_publication_claims(
            cast(Any, Driver()),
            asyncio.Event(),
            drain_timeout_seconds=1,
        )

    assert lifecycle == ["start", "exit", "close"]


@pytest.mark.asyncio
async def test_claims_construction_failure_closes_prepared_refresh_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(
        latest_refresh_enabled=True,
        publication_claims_enabled=True,
        latest_refresh_interval_seconds=3600.0,
    )

    class Service:
        def __init__(self) -> None:
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True

    service = Service()

    async def fail_driver(**_arguments: object) -> None:
        raise RuntimeError("queue construction failed")

    monkeypatch.setattr(
        "opennosh_api.jobs.worker.create_publication_role_driver",
        fail_driver,
    )

    with pytest.raises(RuntimeError, match="queue construction failed"):
        await _run_publication_worker(
            settings=cast(Any, settings),
            refresh_service=cast(Any, service),
            shutdown_requested=asyncio.Event(),
        )

    assert service.closed is True
