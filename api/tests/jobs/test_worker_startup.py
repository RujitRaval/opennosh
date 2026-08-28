from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest
from opennosh_api.jobs.worker import (
    _run_publication_worker,
    create_publication_role_driver,
    supervise_publication_claims,
    validate_production_adapter_registry,
)
from opennosh_api.publication.state import (
    EffectIntent,
    ExternalObservation,
    PublicationStepName,
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
