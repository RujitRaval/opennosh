from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from opennosh_api.governance.gate import GovernanceGate
from opennosh_api.public.artifacts import PublicReadReleaseManifest
from opennosh_api.public.r2 import S3R2ObjectWriter
from opennosh_api.public.signing import public_key_text
from opennosh_api.publication.credentials import ProductionPublicationClients
from opennosh_api.publication.forge.github import (
    GitHubForgeClient,
    GitHubGovernanceAttester,
)
from opennosh_api.publication.object_adapters import PublicationObject
from opennosh_api.publication.receipts import (
    Ed25519ReceiptSigner,
    PublicationReceiptKeyRing,
)
from opennosh_api.publication.runtime import (
    ProductionPublicationObjectSources,
    ProductionPublicationRuntime,
)
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


class ObjectSource:
    identity = "production-material"
    version = "1.0"

    async def materialize(self, _intent: EffectIntent) -> PublicationObject:
        raise AssertionError("runtime construction must not call providers")


class ManifestSource:
    identity = "production-manifest"
    version = "1.0"

    async def materialize_manifest(
        self, _intent: EffectIntent
    ) -> PublicReadReleaseManifest:
        raise AssertionError("runtime construction must not call providers")


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


def test_production_provider_factory_constructs_all_ten_without_provider_calls() -> None:
    activation_id = UUID("00000000-0000-4000-8000-000000000001")
    manifest_key = Ed25519PrivateKey.from_private_bytes(b"m" * 32)
    receipt_key = Ed25519PrivateKey.from_private_bytes(b"r" * 32)
    clients = cast(
        ProductionPublicationClients,
        SimpleNamespace(
            identity=SimpleNamespace(
                artifact_bucket="opennosh-public-commons",
                manifest_key_id="manifest-online",
            ),
            forge=cast(GitHubForgeClient, SimpleNamespace()),
            attester=cast(GitHubGovernanceAttester, SimpleNamespace()),
            manifest_signing_key=manifest_key,
            receipt_signer=Ed25519ReceiptSigner(
                key_id="receipt-production",
                publisher_identity="opennosh:production-publication",
                private_key=receipt_key,
            ),
            receipt_key_ring=PublicationReceiptKeyRing.from_json(
                json.dumps({"receipt-production": public_key_text(receipt_key)})
            ),
            r2_writer=cast(S3R2ObjectWriter, SimpleNamespace()),
        ),
    )
    source = ObjectSource()
    sources = ProductionPublicationObjectSources(
        copy_commit=source,
        copy_evidence=source,
        sign_release=ManifestSource(),
        publish_release=source,
        copy_release=source,
        confirm_registry=source,
    )
    settings = cast(
        Any,
        SimpleNamespace(
            publication_claims_enabled=True,
            publication_activation_id=activation_id,
            publication_artifact_bucket="opennosh-public-commons",
        ),
    )

    runtime = ProductionPublicationRuntime.from_production_providers(
        settings=settings,
        clients=clients,
        governance_gate=cast(GovernanceGate, SimpleNamespace()),
        object_sources=sources,
        clock=lambda: datetime(2026, 8, 28, 1, tzinfo=UTC),
    )

    assert runtime.activation_id == activation_id
    assert tuple(runtime.adapters) == tuple(PublicationStepName)
    assert runtime.adapters[PublicationStepName.COMMIT_RECORD].identity == (
        "opennosh-governed-forge"
    )
    assert runtime.adapters[PublicationStepName.SIGN_RELEASE].identity == (
        "opennosh.r2.sign_release."
        "opennosh.ed25519-release-signer.production-manifest"
    )
    assert runtime.adapters[PublicationStepName.COPY_RECEIPT].identity == (
        "opennosh.receipt-replication.copy_receipt"
    )


def test_production_provider_factory_rejects_unarmed_or_mismatched_runtime() -> None:
    clients = cast(
        ProductionPublicationClients,
        SimpleNamespace(identity=SimpleNamespace(artifact_bucket="provider-bucket")),
    )

    def build(settings: object) -> None:
        ProductionPublicationRuntime.from_production_providers(
            settings=cast(Any, settings),
            clients=clients,
            governance_gate=cast(GovernanceGate, SimpleNamespace()),
            object_sources=cast(ProductionPublicationObjectSources, SimpleNamespace()),
            clock=lambda: datetime(2026, 8, 28, 1, tzinfo=UTC),
        )

    with pytest.raises(RuntimeError, match="claims enabled"):
        build(
            SimpleNamespace(
                publication_claims_enabled=False,
                publication_activation_id=uuid4(),
                publication_artifact_bucket="provider-bucket",
            )
        )
    with pytest.raises(RuntimeError, match="activation ID"):
        build(
            SimpleNamespace(
                publication_claims_enabled=True,
                publication_activation_id=None,
                publication_artifact_bucket="provider-bucket",
            )
        )
    with pytest.raises(RuntimeError, match="configured R2 bucket"):
        build(
            SimpleNamespace(
                publication_claims_enabled=True,
                publication_activation_id=uuid4(),
                publication_artifact_bucket="configured-bucket",
            )
        )
