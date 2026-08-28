from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from uuid import UUID

from opennosh_api.publication.adapters import (
    PublicationAdapterRegistry,
    PublicationEffectAdapter,
)
from opennosh_api.publication.state import PublicationStepName


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
