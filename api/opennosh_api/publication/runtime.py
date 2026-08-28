from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import TYPE_CHECKING
from uuid import UUID

from opennosh_api.governance.contracts import CANONICAL_FORGE_TARGET
from opennosh_api.governance.gate import GovernanceGate
from opennosh_api.publication.adapters import (
    PublicationAdapterRegistry,
    PublicationEffectAdapter,
)
from opennosh_api.publication.credentials import ProductionPublicationClients
from opennosh_api.publication.forge.adapter import GovernedForgeAdapter
from opennosh_api.publication.object_adapters import (
    Ed25519ReleaseManifestSource,
    PublicationObjectSource,
    R2ImmutablePublicationAdapter,
    R2PublicationReceiptStore,
    ReleaseManifestDraftSource,
)
from opennosh_api.publication.receipt_adapters import (
    ReceiptReplicationAdapter,
    ReceiptSigningAdapter,
)
from opennosh_api.publication.state import PublicationStepName, publication_protocol

if TYPE_CHECKING:
    from opennosh_api.settings import Settings


@dataclass(frozen=True, slots=True)
class ProductionPublicationObjectSources:
    copy_commit: PublicationObjectSource
    copy_evidence: PublicationObjectSource
    sign_release: ReleaseManifestDraftSource
    publish_release: PublicationObjectSource
    copy_release: PublicationObjectSource
    confirm_registry: PublicationObjectSource

    def as_mapping(self) -> Mapping[PublicationStepName, PublicationObjectSource]:
        return MappingProxyType(
            {
                PublicationStepName.COPY_COMMIT: self.copy_commit,
                PublicationStepName.COPY_EVIDENCE: self.copy_evidence,
                PublicationStepName.PUBLISH_RELEASE: self.publish_release,
                PublicationStepName.COPY_RELEASE: self.copy_release,
                PublicationStepName.CONFIRM_REGISTRY: self.confirm_registry,
            }
        )


@dataclass(frozen=True, slots=True)
class ProductionPublicationRuntime:
    """Validated, immutable publication wiring constructed before database access."""

    activation_id: UUID
    adapters: Mapping[PublicationStepName, PublicationEffectAdapter]

    def __post_init__(self) -> None:
        expected = set(PublicationStepName)
        actual = set(self.adapters)
        if actual != expected:
            missing = sorted(step.value for step in expected - actual)
            extra = sorted(str(step) for step in actual - expected)
            details = []
            if missing:
                details.append(f"missing={','.join(missing)}")
            if extra:
                details.append(f"extra={','.join(extra)}")
            raise RuntimeError(
                "Production publication adapter registry is invalid: "
                + "; ".join(details)
            )
        normalized = dict(self.adapters)
        for step, adapter in normalized.items():
            if not isinstance(adapter, PublicationEffectAdapter):
                raise RuntimeError(
                    f"Publication adapter {step.value} violates the adapter contract"
                )
            if (
                not isinstance(adapter.identity, str)
                or not adapter.identity.strip()
                or not isinstance(adapter.version, str)
                or not adapter.version.strip()
            ):
                raise RuntimeError(
                    f"Publication adapter {step.value} requires identity and version"
                )
        object.__setattr__(self, "adapters", MappingProxyType(normalized))

    @classmethod
    def build(
        cls,
        *,
        activation_id: UUID,
        commit_record: PublicationEffectAdapter,
        copy_commit: PublicationEffectAdapter,
        copy_evidence: PublicationEffectAdapter,
        sign_release: PublicationEffectAdapter,
        publish_release: PublicationEffectAdapter,
        copy_release: PublicationEffectAdapter,
        confirm_registry: PublicationEffectAdapter,
        sign_receipt: PublicationEffectAdapter,
        publish_receipt_registry: PublicationEffectAdapter,
        copy_receipt: PublicationEffectAdapter,
    ) -> ProductionPublicationRuntime:
        return cls(
            activation_id=activation_id,
            adapters={
                PublicationStepName.COMMIT_RECORD: commit_record,
                PublicationStepName.COPY_COMMIT: copy_commit,
                PublicationStepName.COPY_EVIDENCE: copy_evidence,
                PublicationStepName.SIGN_RELEASE: sign_release,
                PublicationStepName.PUBLISH_RELEASE: publish_release,
                PublicationStepName.COPY_RELEASE: copy_release,
                PublicationStepName.CONFIRM_REGISTRY: confirm_registry,
                PublicationStepName.SIGN_RECEIPT: sign_receipt,
                PublicationStepName.PUBLISH_RECEIPT_REGISTRY: publish_receipt_registry,
                PublicationStepName.COPY_RECEIPT: copy_receipt,
            },
        )

    @classmethod
    def from_production_providers(
        cls,
        *,
        settings: Settings,
        clients: ProductionPublicationClients,
        governance_gate: GovernanceGate,
        object_sources: ProductionPublicationObjectSources,
        clock: Callable[[], datetime],
    ) -> ProductionPublicationRuntime:
        """Construct all ten live adapters before a publication pool is opened."""

        if not settings.publication_claims_enabled:
            raise RuntimeError("Production publication runtime requires claims enabled")
        if settings.publication_activation_id is None:
            raise RuntimeError("Production publication runtime requires one activation ID")
        if settings.publication_artifact_bucket != clients.identity.artifact_bucket:
            raise RuntimeError("Publication client identity does not match configured R2 bucket")
        destinations = {
            definition.name: definition.destination
            for definition in publication_protocol(CANONICAL_FORGE_TARGET)
        }
        object_adapters = {
            step: R2ImmutablePublicationAdapter(
                step=step,
                destination=destinations[step],
                source=source,
                writer=clients.r2_writer,
                bucket=clients.identity.artifact_bucket,
                clock=clock,
            )
            for step, source in object_sources.as_mapping().items()
        }
        object_adapters[PublicationStepName.SIGN_RELEASE] = R2ImmutablePublicationAdapter(
            step=PublicationStepName.SIGN_RELEASE,
            destination=destinations[PublicationStepName.SIGN_RELEASE],
            source=Ed25519ReleaseManifestSource(
                source=object_sources.sign_release,
                key_id=clients.identity.manifest_key_id,
                signing_key=clients.manifest_signing_key,
            ),
            writer=clients.r2_writer,
            bucket=clients.identity.artifact_bucket,
            clock=clock,
        )
        receipt_store = R2PublicationReceiptStore(
            writer=clients.r2_writer,
            bucket=clients.identity.artifact_bucket,
            destination=destinations[PublicationStepName.SIGN_RECEIPT],
            list_prefix="signatures/receipts/v1",
        )
        registry_receipt_store = R2PublicationReceiptStore(
            writer=clients.r2_writer,
            bucket=clients.identity.artifact_bucket,
            destination=destinations[PublicationStepName.PUBLISH_RECEIPT_REGISTRY],
            list_prefix="receipts/v1",
        )
        durability_receipt_store = R2PublicationReceiptStore(
            writer=clients.r2_writer,
            bucket=clients.identity.artifact_bucket,
            destination=destinations[PublicationStepName.COPY_RECEIPT],
            list_prefix="durability/receipts",
        )
        return cls.build(
            activation_id=settings.publication_activation_id,
            commit_record=GovernedForgeAdapter(
                governance_gate,
                clients.forge,
                clients.attester,
                clock=clock,
            ),
            copy_commit=object_adapters[PublicationStepName.COPY_COMMIT],
            copy_evidence=object_adapters[PublicationStepName.COPY_EVIDENCE],
            sign_release=object_adapters[PublicationStepName.SIGN_RELEASE],
            publish_release=object_adapters[PublicationStepName.PUBLISH_RELEASE],
            copy_release=object_adapters[PublicationStepName.COPY_RELEASE],
            confirm_registry=object_adapters[PublicationStepName.CONFIRM_REGISTRY],
            sign_receipt=ReceiptSigningAdapter(
                signer=clients.receipt_signer,
                store=receipt_store,
                key_ring=clients.receipt_key_ring,
                clock=clock,
                object_key_factory=(
                    lambda publication_id: (
                        f"signatures/receipts/v1/{publication_id}.json"
                    )
                ),
            ),
            publish_receipt_registry=ReceiptReplicationAdapter(
                step=PublicationStepName.PUBLISH_RECEIPT_REGISTRY,
                store=registry_receipt_store,
                key_ring=clients.receipt_key_ring,
                clock=clock,
            ),
            copy_receipt=ReceiptReplicationAdapter(
                step=PublicationStepName.COPY_RECEIPT,
                store=durability_receipt_store,
                key_ring=clients.receipt_key_ring,
                clock=clock,
                object_key_factory=(
                    lambda _publication_id, digest: (
                        f"durability/receipts/{digest}.json"
                    )
                ),
            ),
        )


def validate_production_adapter_registry(
    adapters: PublicationAdapterRegistry | None,
) -> PublicationAdapterRegistry:
    """Validate adapter completeness without needing an activation identifier."""

    if adapters is None:
        raise RuntimeError("Production publication worker requires canonical adapters")
    ProductionPublicationRuntime(
        activation_id=UUID(int=0),
        adapters=adapters,
    )
    return adapters
