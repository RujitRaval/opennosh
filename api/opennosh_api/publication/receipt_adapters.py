from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from opennosh_api.publication.receipts import (
    Ed25519ReceiptSigner,
    PublicationReceiptDraft,
    PublicationReceiptKeyRing,
    PublicationReceiptStore,
    ReceiptVerificationError,
    SignedPublicationReceipt,
    canonical_signed_receipt_bytes,
    parse_signed_receipt,
    receipt_object_key,
    signed_receipt_digest,
)
from opennosh_api.publication.state import (
    EffectIntent,
    ExternalObservation,
    ObservationStatus,
    PublicationStepName,
)


class ReceiptSigningAdapter:
    version = "1.0"

    def __init__(
        self,
        *,
        signer: Ed25519ReceiptSigner,
        store: PublicationReceiptStore,
        key_ring: PublicationReceiptKeyRing,
        clock: Callable[[], datetime],
    ) -> None:
        self._signer = signer
        self._store = store
        self._key_ring = key_ring
        self._clock = clock
        self.identity = signer.adapter_identity

    async def apply(self, intent: EffectIntent) -> None:
        _require_step(intent, PublicationStepName.SIGN_RECEIPT)
        _require_destination(intent, self._store.destination)
        draft = _draft_from_intent(intent)
        envelope = self._signer.sign(draft)
        payload = canonical_signed_receipt_bytes(envelope)
        await self._store.put_immutable(
            receipt_object_key(intent.publication_id),
            payload,
            expected_digest=signed_receipt_digest(envelope),
        )

    async def observe(self, intent: EffectIntent) -> ExternalObservation:
        _require_step(intent, PublicationStepName.SIGN_RECEIPT)
        _require_destination(intent, self._store.destination)
        return await _observe_receipt(
            intent,
            store=self._store,
            key_ring=self._key_ring,
            now=self._clock(),
            adapter_identity=self.identity,
            adapter_version=self.version,
            expected_draft=_draft_from_intent(intent),
            expected_signature_key_id=self._signer.key_id,
            expected_publisher_identity=self._signer.publisher_identity,
            expected_publisher_adapter_identity=self._signer.adapter_identity,
            expected_publisher_adapter_version=self._signer.adapter_version,
            include_envelope=True,
        )


class ReceiptReplicationAdapter:
    version = "1.0"

    def __init__(
        self,
        *,
        step: PublicationStepName,
        store: PublicationReceiptStore,
        key_ring: PublicationReceiptKeyRing,
        clock: Callable[[], datetime],
    ) -> None:
        if step not in {
            PublicationStepName.PUBLISH_RECEIPT_REGISTRY,
            PublicationStepName.COPY_RECEIPT,
        }:
            raise ValueError("Receipt replication adapter requires a receipt destination step")
        self._step = step
        self._store = store
        self._key_ring = key_ring
        self._clock = clock
        self.identity = f"opennosh.receipt-replication.{step.value}"

    async def apply(self, intent: EffectIntent) -> None:
        _require_step(intent, self._step)
        _require_destination(intent, self._store.destination)
        envelope = _envelope_from_intent(intent)
        self._key_ring.verify(envelope)
        payload = canonical_signed_receipt_bytes(envelope)
        await self._store.put_immutable(
            receipt_object_key(intent.publication_id),
            payload,
            expected_digest=signed_receipt_digest(envelope),
        )

    async def observe(self, intent: EffectIntent) -> ExternalObservation:
        _require_step(intent, self._step)
        _require_destination(intent, self._store.destination)
        envelope = _envelope_from_intent(intent)
        return await _observe_receipt(
            intent,
            store=self._store,
            key_ring=self._key_ring,
            now=self._clock(),
            adapter_identity=self.identity,
            adapter_version=self.version,
            expected_envelope=envelope,
        )


async def _observe_receipt(
    intent: EffectIntent,
    *,
    store: PublicationReceiptStore,
    key_ring: PublicationReceiptKeyRing,
    now: datetime,
    adapter_identity: str,
    adapter_version: str,
    expected_draft: PublicationReceiptDraft | None = None,
    expected_envelope: SignedPublicationReceipt | None = None,
    expected_signature_key_id: str | None = None,
    expected_publisher_identity: str | None = None,
    expected_publisher_adapter_identity: str | None = None,
    expected_publisher_adapter_version: str | None = None,
    include_envelope: bool = False,
) -> ExternalObservation:
    observation = await store.observe(receipt_object_key(intent.publication_id))
    if observation is None:
        return _observation(
            intent,
            status=ObservationStatus.ABSENT,
            now=now,
            adapter_identity=adapter_identity,
            adapter_version=adapter_version,
        )
    payload = await store.read(observation.object_key)
    if payload is None:
        return _observation(
            intent,
            status=ObservationStatus.RETRYABLE_FAILURE,
            now=now,
            adapter_identity=adapter_identity,
            adapter_version=adapter_version,
            code="receipt_disappeared_after_observation",
        )
    try:
        envelope = parse_signed_receipt(payload)
        key_ring.verify(envelope)
    except ReceiptVerificationError as error:
        return _observation(
            intent,
            status=ObservationStatus.CONFLICT,
            now=now,
            adapter_identity=adapter_identity,
            adapter_version=adapter_version,
            code=str(error),
            context={"receipt_destination": store.destination},
        )
    actual_digest = signed_receipt_digest(envelope)
    if observation.receipt_digest != actual_digest:
        return _observation(
            intent,
            status=ObservationStatus.CONFLICT,
            now=now,
            adapter_identity=adapter_identity,
            adapter_version=adapter_version,
            code="receipt_observation_digest_conflict",
        )
    if expected_signature_key_id is not None and (
        envelope.signature_key_id != expected_signature_key_id
        or envelope.receipt.publisher_identity != expected_publisher_identity
        or envelope.receipt.publisher_adapter_identity != expected_publisher_adapter_identity
        or envelope.receipt.publisher_adapter_version != expected_publisher_adapter_version
    ):
        return _observation(
            intent,
            status=ObservationStatus.CONFLICT,
            now=now,
            adapter_identity=adapter_identity,
            adapter_version=adapter_version,
            code="receipt_signer_identity_conflict",
        )
    if envelope.receipt.publication_id != intent.publication_id:
        return _observation(
            intent,
            status=ObservationStatus.CONFLICT,
            now=now,
            adapter_identity=adapter_identity,
            adapter_version=adapter_version,
            code="receipt_publication_identity_conflict",
        )
    if expected_draft is not None:
        actual = PublicationReceiptDraft.model_validate(
            envelope.receipt.model_dump(
                mode="python",
                exclude={
                    "publisher_identity",
                    "publisher_adapter_identity",
                    "publisher_adapter_version",
                },
            )
        )
        if actual != expected_draft:
            return _observation(
                intent,
                status=ObservationStatus.CONFLICT,
                now=now,
                adapter_identity=adapter_identity,
                adapter_version=adapter_version,
                code="receipt_signer_state_conflict",
            )
    if expected_envelope is not None and envelope != expected_envelope:
        return _observation(
            intent,
            status=ObservationStatus.CONFLICT,
            now=now,
            adapter_identity=adapter_identity,
            adapter_version=adapter_version,
            code="receipt_destination_state_conflict",
        )
    context: dict[str, object] = {
        "signature_key_id": envelope.signature_key_id,
        "publisher_identity": envelope.receipt.publisher_identity,
    }
    if include_envelope:
        context["signed_receipt"] = envelope.model_dump(mode="json")
    return _observation(
        intent,
        status=ObservationStatus.VERIFIED,
        now=now,
        adapter_identity=adapter_identity,
        adapter_version=adapter_version,
        content_digest=actual_digest,
        external_reference=observation.external_reference,
        context=context,
    )


def _draft_from_intent(intent: EffectIntent) -> PublicationReceiptDraft:
    value = intent.context.get("receipt_draft")
    if not isinstance(value, dict):
        raise ValueError("Receipt signing intent is missing its canonical draft")
    return PublicationReceiptDraft.model_validate(value)


def _envelope_from_intent(intent: EffectIntent) -> SignedPublicationReceipt:
    value = intent.context.get("signed_receipt")
    if not isinstance(value, dict):
        raise ValueError("Receipt replication intent is missing its signed envelope")
    return SignedPublicationReceipt.model_validate(value)


def _require_destination(intent: EffectIntent, destination: str) -> None:
    if intent.destination != destination:
        raise ValueError("Receipt adapter destination does not match the planned effect")


def _require_step(intent: EffectIntent, expected: PublicationStepName) -> None:
    if intent.step is not expected:
        raise ValueError(f"Receipt adapter cannot execute {intent.step.value}")


def _observation(
    intent: EffectIntent,
    *,
    status: ObservationStatus,
    now: datetime,
    adapter_identity: str,
    adapter_version: str,
    content_digest: str | None = None,
    external_reference: str | None = None,
    code: str | None = None,
    context: dict[str, object] | None = None,
) -> ExternalObservation:
    return ExternalObservation(
        step=intent.step,
        status=status,
        observed_at=now,
        destination=intent.destination,
        effect_idempotency_key=intent.idempotency_key,
        adapter_identity=adapter_identity,
        adapter_version=adapter_version,
        content_digest=content_digest,
        external_reference=external_reference,
        code=code,
        context=context or {},
    )
