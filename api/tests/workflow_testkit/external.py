from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from types import MappingProxyType

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from opennosh_api.publication.adapters import PublicationAdapterRegistry
from opennosh_api.publication.receipts import (
    Ed25519ReceiptSigner,
    PublicationReceiptDraft,
    SignedPublicationReceipt,
    signed_receipt_digest,
)
from opennosh_api.publication.state import (
    EffectIntent,
    ExternalObservation,
    ObservationStatus,
    PublicationStepName,
)

_RECEIPT_SIGNER = Ed25519ReceiptSigner(
    key_id="workflow-testkit-2026",
    publisher_identity="opennosh:workflow-testkit",
    private_key=Ed25519PrivateKey.from_private_bytes(b"w" * 32),
)


class ExternalSystemKind(StrEnum):
    FORGE = "forge"
    OBJECT_STORAGE = "object_storage"
    OCR = "ocr"
    SIGNER = "signer"
    REGISTRY = "registry"
    SEARCH = "search"
    QUEUE = "queue"


@dataclass(frozen=True, slots=True)
class ExternalEffect:
    system: ExternalSystemKind
    idempotency_key: str
    content_digest: str
    external_reference: str | None
    context: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ExternalStateSnapshot:
    effects: tuple[ExternalEffect, ...]
    apply_counts: Mapping[tuple[ExternalSystemKind, str], int]
    scripted_observations: Mapping[tuple[ExternalSystemKind, str], tuple[ObservationStatus, ...]]


class PersistentExternalState:
    """Inspectable external state whose lifetime is independent of worker objects."""

    def __init__(self) -> None:
        self._effects: dict[tuple[ExternalSystemKind, str], ExternalEffect] = {}
        self._apply_counts: dict[tuple[ExternalSystemKind, str], int] = {}
        self._scripted_observations: dict[
            tuple[ExternalSystemKind, str], list[ObservationStatus]
        ] = {}

    def apply(
        self,
        system: ExternalSystemKind,
        idempotency_key: str,
        *,
        content_digest: str,
        external_reference: str | None = None,
        context: Mapping[str, object] | None = None,
    ) -> ExternalEffect:
        identity = (system, idempotency_key)
        candidate = ExternalEffect(
            system=system,
            idempotency_key=idempotency_key,
            content_digest=content_digest,
            external_reference=external_reference,
            context=MappingProxyType(dict(context or {})),
        )
        existing = self._effects.get(identity)
        if existing is not None and existing != candidate:
            raise RuntimeError("Idempotency key is bound to different external state")
        self._apply_counts[identity] = self._apply_counts.get(identity, 0) + 1
        self._effects.setdefault(identity, candidate)
        return self._effects[identity]

    def observe(self, system: ExternalSystemKind, idempotency_key: str) -> ExternalEffect | None:
        return self._effects.get((system, idempotency_key))

    def script_observations(
        self,
        system: ExternalSystemKind,
        idempotency_key: str,
        statuses: Sequence[ObservationStatus],
    ) -> None:
        if not statuses:
            raise ValueError("Scripted observations cannot be empty")
        self._scripted_observations[(system, idempotency_key)] = list(statuses)

    def next_scripted_observation(
        self, system: ExternalSystemKind, idempotency_key: str
    ) -> ObservationStatus | None:
        identity = (system, idempotency_key)
        statuses = self._scripted_observations.get(identity)
        if not statuses:
            return None
        status = statuses.pop(0)
        if not statuses:
            self._scripted_observations.pop(identity)
        return status

    def apply_count(self, system: ExternalSystemKind, idempotency_key: str) -> int:
        return self._apply_counts.get((system, idempotency_key), 0)

    def snapshot(self) -> ExternalStateSnapshot:
        return ExternalStateSnapshot(
            effects=tuple(self._effects.values()),
            apply_counts=MappingProxyType(dict(self._apply_counts)),
            scripted_observations=MappingProxyType(
                {
                    identity: tuple(statuses)
                    for identity, statuses in self._scripted_observations.items()
                }
            ),
        )

    def restore(self, snapshot: ExternalStateSnapshot) -> None:
        self._effects = {
            (effect.system, effect.idempotency_key): effect for effect in snapshot.effects
        }
        self._apply_counts = dict(snapshot.apply_counts)
        self._scripted_observations = {
            identity: list(statuses)
            for identity, statuses in snapshot.scripted_observations.items()
        }

    @property
    def effects(self) -> tuple[ExternalEffect, ...]:
        return tuple(self._effects.values())


class PersistentPublicationAdapter:
    version = "1"

    def __init__(
        self,
        system: ExternalSystemKind,
        state: PersistentExternalState,
        clock: Callable[[], datetime],
        release_version: str,
    ) -> None:
        self.system = system
        self.state = state
        self._clock = clock
        self._release_version = release_version
        self.identity = f"testkit-{system.value}"

    async def apply(self, intent: EffectIntent) -> None:
        content_digest = intent.approved_payload_digest
        external_reference = (
            "b" * 40 if intent.step is PublicationStepName.COMMIT_RECORD else None
        )
        context: dict[str, object] = {
            "step": intent.step.value,
            "destination": intent.destination,
        }
        if intent.step is PublicationStepName.COMMIT_RECORD:
            context["merged_tree_digest"] = "c" * 64
        elif intent.step is PublicationStepName.SIGN_RELEASE:
            context["release_version"] = self._release_version
        elif intent.step is PublicationStepName.CONFIRM_REGISTRY:
            context["registry_result"] = "accepted"
        elif intent.step is PublicationStepName.SIGN_RECEIPT:
            draft_value = intent.context.get("receipt_draft")
            if not isinstance(draft_value, dict):
                raise ValueError("Receipt test effect requires a canonical draft")
            envelope = _RECEIPT_SIGNER.sign(
                PublicationReceiptDraft.model_validate(draft_value)
            )
            content_digest = signed_receipt_digest(envelope)
            external_reference = f"key:{envelope.signature_key_id}"
            context["signed_receipt"] = envelope.model_dump(mode="json")
        elif intent.step in {
            PublicationStepName.PUBLISH_RECEIPT_REGISTRY,
            PublicationStepName.COPY_RECEIPT,
        }:
            envelope_value = intent.context.get("signed_receipt")
            if not isinstance(envelope_value, dict):
                raise ValueError("Receipt test copy requires a signed envelope")
            envelope = SignedPublicationReceipt.model_validate(envelope_value)
            content_digest = signed_receipt_digest(envelope)
            external_reference = f"memory:{intent.step.value}:{intent.publication_id}"
        self.state.apply(
            self.system,
            intent.idempotency_key,
            content_digest=content_digest,
            external_reference=external_reference,
            context=context,
        )

    async def observe(self, intent: EffectIntent) -> ExternalObservation:
        effect = self.state.observe(self.system, intent.idempotency_key)
        status = self.state.next_scripted_observation(self.system, intent.idempotency_key)
        if status is None:
            status = ObservationStatus.VERIFIED if effect else ObservationStatus.ABSENT
        if status is ObservationStatus.VERIFIED and effect is None:
            raise ValueError("Scripted verification requires persistent external state")
        now = self._clock()
        return ExternalObservation(
            step=intent.step,
            status=status,
            observed_at=now,
            destination=intent.destination,
            effect_idempotency_key=intent.idempotency_key,
            adapter_identity=self.identity,
            adapter_version=self.version,
            content_digest=(
                effect.content_digest
                if effect is not None and status is ObservationStatus.VERIFIED
                else None
            ),
            external_reference=(
                effect.external_reference
                if effect is not None and status is ObservationStatus.VERIFIED
                else None
            ),
            retry_at=(
                now + timedelta(seconds=5)
                if status is ObservationStatus.RETRYABLE_FAILURE
                else None
            ),
            code=(f"scripted_{status.value}" if status is not ObservationStatus.VERIFIED else None),
            context=effect.context if effect else {},
        )


_STEP_SYSTEMS: Mapping[PublicationStepName, ExternalSystemKind] = MappingProxyType(
    {
        PublicationStepName.COMMIT_RECORD: ExternalSystemKind.FORGE,
        PublicationStepName.COPY_COMMIT: ExternalSystemKind.OBJECT_STORAGE,
        PublicationStepName.COPY_EVIDENCE: ExternalSystemKind.OBJECT_STORAGE,
        PublicationStepName.SIGN_RELEASE: ExternalSystemKind.SIGNER,
        PublicationStepName.PUBLISH_RELEASE: ExternalSystemKind.FORGE,
        PublicationStepName.COPY_RELEASE: ExternalSystemKind.OBJECT_STORAGE,
        PublicationStepName.CONFIRM_REGISTRY: ExternalSystemKind.REGISTRY,
        PublicationStepName.SIGN_RECEIPT: ExternalSystemKind.SIGNER,
        PublicationStepName.PUBLISH_RECEIPT_REGISTRY: ExternalSystemKind.REGISTRY,
        PublicationStepName.COPY_RECEIPT: ExternalSystemKind.OBJECT_STORAGE,
    }
)


def publication_adapter_registry(
    state: PersistentExternalState,
    clock: Callable[[], datetime],
    *,
    release_version: str = "2026.08.26-testkit",
) -> PublicationAdapterRegistry:
    adapters = {
        system: PersistentPublicationAdapter(system, state, clock, release_version)
        for system in set(_STEP_SYSTEMS.values())
    }
    return {step: adapters[system] for step, system in _STEP_SYSTEMS.items()}


def system_for_step(step: PublicationStepName) -> ExternalSystemKind:
    return _STEP_SYSTEMS[step]
