from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from opennosh_api.jobs.worker import (
    create_publication_role_driver,
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
