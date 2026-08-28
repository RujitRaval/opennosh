from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from opennosh_api.governance.gate import GovernanceGate
from opennosh_api.governance.policy import GovernanceBinding
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
    DeferredGovernanceGate,
    PreparedProductionPublicationRuntime,
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


@pytest.mark.asyncio
async def test_deferred_governance_gate_binds_once_and_forwards_both_operations() -> None:
    gate = DeferredGovernanceGate()
    publication_id = uuid4()
    binding = cast(GovernanceBinding, SimpleNamespace(publication_id=publication_id))

    with pytest.raises(RuntimeError, match="not bound"):
        await gate.binding_for(publication_id)

    class Delegate:
        async def binding_for(self, requested_id: UUID) -> GovernanceBinding:
            assert requested_id == publication_id
            return binding

        async def authorize_merge(
            self,
            requested_id: UUID,
            *,
            head_commit: str,
            expected_payload_digest: str,
            now: datetime,
        ) -> GovernanceBinding:
            assert requested_id == publication_id
            assert head_commit == "a" * 40
            assert expected_payload_digest == "b" * 64
            assert now == datetime(2026, 8, 28, 1, tzinfo=UTC)
            return binding

    gate.bind(cast(GovernanceGate, Delegate()))
    assert await gate.binding_for(publication_id) is binding
    assert await gate.authorize_merge(
        publication_id,
        head_commit="a" * 40,
        expected_payload_digest="b" * 64,
        now=datetime(2026, 8, 28, 1, tzinfo=UTC),
    ) is binding
    with pytest.raises(RuntimeError, match="already bound"):
        gate.bind(cast(GovernanceGate, Delegate()))


@pytest.mark.asyncio
async def test_prepared_runtime_binds_once_and_closes_owned_clients_once() -> None:
    lifecycle: list[str] = []

    class Closable:
        def __init__(self, name: str) -> None:
            self.name = name

        async def aclose(self) -> None:
            lifecycle.append(self.name)

    prepared = PreparedProductionPublicationRuntime(
        runtime=_runtime(),
        clients=cast(ProductionPublicationClients, Closable("clients")),
        authority=cast(Any, Closable("authority")),
        governance_gate=DeferredGovernanceGate(),
    )

    assert prepared.bind_pool(SimpleNamespace()) == prepared.runtime.adapters
    with pytest.raises(RuntimeError, match="already bound"):
        prepared.bind_pool(SimpleNamespace())
    await prepared.aclose()
    await prepared.aclose()
    assert lifecycle == ["authority", "clients"]
    with pytest.raises(RuntimeError, match="already closed"):
        prepared.bind_pool(SimpleNamespace())


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
            publication_claims_enabled=False,
            publication_activation_id=None,
            publication_artifact_bucket="opennosh-public-commons",
            public_commons_verifying_keys=(
                f"manifest-online:{public_key_text(manifest_key)}"
            ),
            latest_pointer_lifetime_seconds=82_800,
        ),
    )

    runtime = ProductionPublicationRuntime.from_production_providers(
        settings=settings,
        clients=clients,
        governance_gate=cast(GovernanceGate, SimpleNamespace()),
        object_sources=sources,
        clock=lambda: datetime(2026, 8, 28, 1, tzinfo=UTC),
        zero_claim_preflight=True,
    )

    assert runtime.activation_id == UUID(int=0)
    assert tuple(runtime.adapters) == tuple(PublicationStepName)
    assert runtime.adapters[PublicationStepName.COMMIT_RECORD].identity == (
        "opennosh-governed-forge"
    )
    assert runtime.adapters[PublicationStepName.SIGN_RELEASE].identity == (
        "opennosh.r2.sign_release."
        "opennosh.ed25519-release-signer.production-manifest"
    )
    assert runtime.adapters[PublicationStepName.COPY_RECEIPT].identity == (
        "opennosh.receipt-gated-pointer-activation"
    )
    activation_id = uuid4()
    armed = ProductionPublicationRuntime.from_production_providers(
        settings=cast(
            Any,
            SimpleNamespace(
                publication_claims_enabled=True,
                publication_activation_id=activation_id,
                publication_artifact_bucket="opennosh-public-commons",
                public_commons_verifying_keys=(
                    f"manifest-online:{public_key_text(manifest_key)}"
                ),
                latest_pointer_lifetime_seconds=82_800,
            ),
        ),
        clients=clients,
        governance_gate=cast(GovernanceGate, SimpleNamespace()),
        object_sources=sources,
        clock=lambda: datetime(2026, 8, 28, 1, tzinfo=UTC),
    )
    assert armed.activation_id == activation_id
    assert tuple(armed.adapters) == tuple(PublicationStepName)


def test_production_provider_factory_rejects_unarmed_or_mismatched_runtime() -> None:
    clients = cast(
        ProductionPublicationClients,
        SimpleNamespace(identity=SimpleNamespace(artifact_bucket="provider-bucket")),
    )

    def build(settings: object, *, zero_claim_preflight: bool = False) -> None:
        ProductionPublicationRuntime.from_production_providers(
            settings=cast(Any, settings),
            clients=clients,
            governance_gate=cast(GovernanceGate, SimpleNamespace()),
            object_sources=cast(ProductionPublicationObjectSources, SimpleNamespace()),
            clock=lambda: datetime(2026, 8, 28, 1, tzinfo=UTC),
            zero_claim_preflight=zero_claim_preflight,
        )

    with pytest.raises(RuntimeError, match="claims or zero-claim preflight"):
        build(
            SimpleNamespace(
                publication_claims_enabled=False,
                publication_activation_id=uuid4(),
                publication_artifact_bucket="provider-bucket",
            )
        )
    with pytest.raises(RuntimeError, match="one activation ID"):
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
                publication_claims_enabled=False,
                publication_activation_id=None,
                publication_artifact_bucket="configured-bucket",
            ),
            zero_claim_preflight=True,
        )
