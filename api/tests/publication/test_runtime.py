from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

import pytest
from opennosh_api.publication.runtime import ProductionPublicationRuntime
from opennosh_api.publication.state import (
    EffectIntent,
    ExternalObservation,
    PublicationStepName,
)


class Adapter:
    version = "1.0"

    def __init__(self, identity: str) -> None:
        self.identity = identity

    async def apply(self, _intent: EffectIntent) -> None:
        return None

    async def observe(self, _intent: EffectIntent) -> ExternalObservation:
        raise AssertionError("runtime validation must not execute adapters")


def _runtime() -> ProductionPublicationRuntime:
    adapters = {
        step: Adapter(f"production:{step.value}")
        for step in PublicationStepName
    }
    return ProductionPublicationRuntime.build(
        activation_id=uuid4(),
        commit_record=adapters[PublicationStepName.COMMIT_RECORD],
        copy_commit=adapters[PublicationStepName.COPY_COMMIT],
        copy_evidence=adapters[PublicationStepName.COPY_EVIDENCE],
        sign_release=adapters[PublicationStepName.SIGN_RELEASE],
        publish_release=adapters[PublicationStepName.PUBLISH_RELEASE],
        copy_release=adapters[PublicationStepName.COPY_RELEASE],
        confirm_registry=adapters[PublicationStepName.CONFIRM_REGISTRY],
        sign_receipt=adapters[PublicationStepName.SIGN_RECEIPT],
        publish_receipt_registry=adapters[
            PublicationStepName.PUBLISH_RECEIPT_REGISTRY
        ],
        copy_receipt=adapters[PublicationStepName.COPY_RECEIPT],
    )


def test_production_runtime_builds_exact_canonical_registry() -> None:
    runtime = _runtime()

    assert tuple(runtime.adapters) == tuple(PublicationStepName)
    assert {
        adapter.identity for adapter in runtime.adapters.values()
    } == {
        f"production:{step.value}" for step in PublicationStepName
    }


def test_production_runtime_registry_is_immutable() -> None:
    runtime = _runtime()

    with pytest.raises(TypeError):
        cast(dict[Any, Any], runtime.adapters)[PublicationStepName.COMMIT_RECORD] = Adapter(
            "replacement"
        )


def test_production_runtime_rejects_incomplete_registry() -> None:
    registry = dict(_runtime().adapters)
    registry.pop(PublicationStepName.COPY_RECEIPT)

    with pytest.raises(RuntimeError, match="missing=copy_receipt"):
        ProductionPublicationRuntime(
            activation_id=uuid4(),
            adapters=registry,
        )
