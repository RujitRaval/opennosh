from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, Any
from uuid import UUID

from opennosh_api.governance.contracts import CANONICAL_FORGE_TARGET
from opennosh_api.governance.gate import GovernanceGate, PostgresGovernanceGate
from opennosh_api.governance.policy import GovernanceBinding
from opennosh_api.public.artifacts import HttpArtifactStore, PublicArtifactReadService
from opennosh_api.public_commons.manifests import ManifestKeyRing
from opennosh_api.publication.activation import (
    ReceiptGatedPointerActivationAdapter,
)
from opennosh_api.publication.adapters import (
    PublicationAdapterRegistry,
    PublicationEffectAdapter,
)
from opennosh_api.publication.credentials import ProductionPublicationClients
from opennosh_api.publication.forge.adapter import GovernedForgeAdapter
from opennosh_api.publication.materials import (
    CanonicalCommitObjectSource,
    CanonicalEvidenceObjectSource,
    CanonicalPublicationMaterialAuthority,
    CanonicalRegistryCheckpointSource,
    CanonicalReleaseDurabilitySource,
    CanonicalReleaseManifestSource,
    CanonicalReleasePublicationSource,
)
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


class DeferredGovernanceGate:
    """One-time binding lets the complete registry exist before its pool opens."""

    def __init__(self) -> None:
        self._delegate: GovernanceGate | None = None

    def bind(self, delegate: GovernanceGate) -> None:
        if self._delegate is not None:
            raise RuntimeError("Production governance gate is already bound")
        self._delegate = delegate

    async def binding_for(self, publication_id: UUID) -> GovernanceBinding:
        return await self._required().binding_for(publication_id)

    async def authorize_merge(
        self,
        publication_id: UUID,
        *,
        head_commit: str,
        expected_payload_digest: str,
        now: datetime,
    ) -> GovernanceBinding:
        return await self._required().authorize_merge(
            publication_id,
            head_commit=head_commit,
            expected_payload_digest=expected_payload_digest,
            now=now,
        )

    def _required(self) -> GovernanceGate:
        if self._delegate is None:
            raise RuntimeError("Production governance gate is not bound")
        return self._delegate


@dataclass(frozen=True, slots=True)
class ProductionPublicationObjectSources:
    copy_commit: PublicationObjectSource
    copy_evidence: PublicationObjectSource
    sign_release: ReleaseManifestDraftSource
    publish_release: PublicationObjectSource
    copy_release: PublicationObjectSource
    confirm_registry: PublicationObjectSource

    @classmethod
    def from_authority(
        cls,
        authority: CanonicalPublicationMaterialAuthority,
    ) -> ProductionPublicationObjectSources:
        return cls(
            copy_commit=CanonicalCommitObjectSource(authority),
            copy_evidence=CanonicalEvidenceObjectSource(),
            sign_release=CanonicalReleaseManifestSource(authority),
            publish_release=CanonicalReleasePublicationSource(authority),
            copy_release=CanonicalReleaseDurabilitySource(authority),
            confirm_registry=CanonicalRegistryCheckpointSource(authority),
        )

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
        zero_claim_preflight: bool = False,
    ) -> ProductionPublicationRuntime:
        """Construct all ten live adapters before a publication pool is opened."""

        if not zero_claim_preflight and not settings.publication_claims_enabled:
            raise RuntimeError(
                "Production publication runtime requires claims or zero-claim preflight"
            )
        activation_id = settings.publication_activation_id
        if zero_claim_preflight:
            activation_id = UUID(int=0)
        if activation_id is None:
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
        receipt_copy = ReceiptReplicationAdapter(
            step=PublicationStepName.COPY_RECEIPT,
            store=durability_receipt_store,
            key_ring=clients.receipt_key_ring,
            clock=clock,
            object_key_factory=(
                lambda _publication_id, digest: (
                    f"durability/receipts/{digest}.json"
                )
            ),
        )
        return cls.build(
            activation_id=activation_id,
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
            copy_receipt=ReceiptGatedPointerActivationAdapter(
                receipt_copy=receipt_copy,
                writer=clients.r2_writer,
                bucket=clients.identity.artifact_bucket,
                manifest_keys=ManifestKeyRing.from_config(
                    settings.public_commons_verifying_keys
                ),
                receipt_keys=clients.receipt_key_ring,
                signing_key_id=clients.identity.manifest_key_id,
                signing_key=clients.manifest_signing_key,
                pointer_lifetime_seconds=settings.latest_pointer_lifetime_seconds,
                clock=clock,
            ),
        )


@dataclass(slots=True)
class PreparedProductionPublicationRuntime:
    """Complete no-I/O registry that binds its governance gate to one pool once."""

    runtime: ProductionPublicationRuntime
    clients: ProductionPublicationClients
    authority: CanonicalPublicationMaterialAuthority
    governance_gate: DeferredGovernanceGate
    _bound: bool = False
    _closed: bool = False

    @classmethod
    async def from_settings(
        cls,
        settings: Settings,
        *,
        clock: Callable[[], datetime],
        zero_claim_preflight: bool = False,
    ) -> PreparedProductionPublicationRuntime:
        clients = ProductionPublicationClients.from_settings(settings)
        reader: PublicArtifactReadService | None = None
        try:
            if settings.public_artifact_base_url is None:
                raise ValueError("Canonical publication requires the public artifact origin")
            reader = PublicArtifactReadService(
                store=HttpArtifactStore(
                    settings.public_artifact_base_url,
                    timeout_seconds=settings.public_artifact_timeout_seconds,
                ),
                manifest_keys=ManifestKeyRing.from_config(
                    settings.public_commons_verifying_keys
                ),
                receipt_keys=clients.receipt_key_ring,
                max_cached_releases=2,
            )
            gate = DeferredGovernanceGate()
            authority = CanonicalPublicationMaterialAuthority(
                governance_gate=gate,
                forge=clients.forge,
                current_release=reader,
                writer=clients.r2_writer,
                bucket=clients.identity.artifact_bucket,
                manifest_keys=ManifestKeyRing.from_config(
                    settings.public_commons_verifying_keys
                ),
            )
            runtime = ProductionPublicationRuntime.from_production_providers(
                settings=settings,
                clients=clients,
                governance_gate=gate,
                object_sources=ProductionPublicationObjectSources.from_authority(
                    authority
                ),
                clock=clock,
                zero_claim_preflight=zero_claim_preflight,
            )
            return cls(
                runtime=runtime,
                clients=clients,
                authority=authority,
                governance_gate=gate,
            )
        except BaseException:
            closures = [clients.aclose()]
            if reader is not None:
                closures.append(reader.aclose())
            await asyncio.gather(*closures, return_exceptions=True)
            raise

    def bind_pool(self, pool: Any) -> PublicationAdapterRegistry:
        if self._closed:
            raise RuntimeError("Prepared production runtime is already closed")
        if self._bound:
            raise RuntimeError("Prepared production runtime is already bound")
        self.governance_gate.bind(PostgresGovernanceGate(pool))
        self._bound = True
        return validate_production_adapter_registry(self.runtime.adapters)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        await asyncio.gather(
            self.authority.aclose(),
            self.clients.aclose(),
        )


async def run_zero_claim_preactivation_smoke(
    settings: Settings,
    *,
    clock: Callable[[], datetime],
) -> tuple[str, ...]:
    """Construct the complete live registry without opening pools or calling providers."""

    prepared = await PreparedProductionPublicationRuntime.from_settings(
        settings,
        clock=clock,
        zero_claim_preflight=True,
    )
    try:
        registry = validate_production_adapter_registry(prepared.runtime.adapters)
        return tuple(step.value for step in registry)
    finally:
        await prepared.aclose()


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
