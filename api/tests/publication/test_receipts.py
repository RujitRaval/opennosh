from __future__ import annotations

import base64
import hashlib
from dataclasses import replace
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from opennosh_api.publication.receipt_adapters import (
    ReceiptReplicationAdapter,
    ReceiptSigningAdapter,
)
from opennosh_api.publication.receipts import (
    Ed25519ReceiptSigner,
    ImmutableReceiptConflictError,
    LocalImmutablePublicationReceiptStore,
    MemoryPublicationReceiptStore,
    PublicationReceiptDraft,
    PublicationReceiptKeyRing,
    ReceiptEventType,
    ReceiptVerificationError,
    canonical_signed_receipt_bytes,
    parse_signed_receipt,
    receipt_draft_from_snapshot,
    receipt_object_key,
    signed_receipt_digest,
    validate_receipt_binding,
)
from opennosh_api.publication.state import (
    EffectIntent,
    ObservationStatus,
    PublicationStepName,
)
from tests.publication.test_planner import NOW, PUBLICATION_ID, snapshot

PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(b"t" * 32)
SIGNER = Ed25519ReceiptSigner(
    key_id="publication-test-2026",
    publisher_identity="opennosh:test-publication",
    private_key=PRIVATE_KEY,
)
KEY_RING = PublicationReceiptKeyRing({"publication-test-2026": PRIVATE_KEY.public_key()})
OTHER_PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(b"u" * 32)
OTHER_SIGNER = Ed25519ReceiptSigner(
    key_id="publication-other-2026",
    publisher_identity="opennosh:other-publisher",
    private_key=OTHER_PRIVATE_KEY,
)
MULTI_KEY_RING = PublicationReceiptKeyRing(
    {
        "publication-test-2026": PRIVATE_KEY.public_key(),
        "publication-other-2026": OTHER_PRIVATE_KEY.public_key(),
    }
)


def _draft() -> PublicationReceiptDraft:
    return receipt_draft_from_snapshot(snapshot(current=7))


def _intent(
    step: PublicationStepName,
    destination: str,
    context: dict[str, object],
) -> EffectIntent:
    return EffectIntent(
        publication_id=PUBLICATION_ID,
        workflow_version="1.0",
        workflow_revision=7,
        step=step,
        destination=destination,
        approved_payload_digest="a" * 64,
        idempotency_key="9" * 64,
        forge_target="https://forge.example/opennosh/packs",
        context=context,
    )


def test_receipt_signature_is_canonical_and_detects_tampering() -> None:
    envelope = SIGNER.sign(_draft())
    payload = canonical_signed_receipt_bytes(envelope)

    assert parse_signed_receipt(payload) == envelope
    KEY_RING.verify(envelope)
    assert len(signed_receipt_digest(envelope)) == 64

    tampered = envelope.model_copy(
        update={"receipt": envelope.receipt.model_copy(update={"record_id": "tampered-record"})}
    )
    with pytest.raises(ReceiptVerificationError, match="receipt_signature_invalid"):
        KEY_RING.verify(tampered)


def test_receipt_rejects_noncanonical_proof_destination() -> None:
    payload = _draft().model_dump(mode="python")
    proofs = list(payload["verified_steps"])
    proofs[1]["destination"] = "urn:opennosh:foreign-destination"
    payload["verified_steps"] = proofs

    with pytest.raises(ValueError, match="canonical protocol"):
        PublicationReceiptDraft.model_validate(payload)


def test_receipt_parser_rejects_oversized_payload_before_json_decoding() -> None:
    with pytest.raises(ReceiptVerificationError, match="receipt_payload_too_large"):
        parse_signed_receipt(b"{" + b" " * (256 * 1024))


@pytest.mark.asyncio
async def test_signing_adapter_rejects_another_trusted_publishers_receipt() -> None:
    store = MemoryPublicationReceiptStore(destination="urn:opennosh:receipt:signer")
    envelope = OTHER_SIGNER.sign(_draft())
    payload = canonical_signed_receipt_bytes(envelope)
    await store.put_immutable(
        receipt_object_key(PUBLICATION_ID),
        payload,
        expected_digest=signed_receipt_digest(envelope),
    )
    adapter = ReceiptSigningAdapter(
        signer=SIGNER,
        store=store,
        key_ring=MULTI_KEY_RING,
        clock=lambda: NOW,
    )
    intent = _intent(
        PublicationStepName.SIGN_RECEIPT,
        store.destination,
        {"receipt_draft": _draft().model_dump(mode="json")},
    )

    observation = await adapter.observe(intent)

    assert observation.status is ObservationStatus.CONFLICT
    assert observation.code == "receipt_signer_identity_conflict"


@pytest.mark.asyncio
async def test_signing_adapter_can_use_a_distinct_staging_key() -> None:
    store = MemoryPublicationReceiptStore(destination="urn:opennosh:receipt:signer")
    staging_key = f"signatures/receipts/v1/{PUBLICATION_ID}.json"
    adapter = ReceiptSigningAdapter(
        signer=SIGNER,
        store=store,
        key_ring=KEY_RING,
        clock=lambda: NOW,
        object_key_factory=lambda _publication_id: staging_key,
    )
    intent = _intent(
        PublicationStepName.SIGN_RECEIPT,
        store.destination,
        {"receipt_draft": _draft().model_dump(mode="json")},
    )

    await adapter.apply(intent)
    observation = await adapter.observe(intent)

    assert observation.status is ObservationStatus.VERIFIED
    assert observation.external_reference is not None
    assert staging_key in observation.external_reference
    assert await store.read(receipt_object_key(PUBLICATION_ID)) is None


def test_receipt_binding_rejects_a_different_approved_payload() -> None:
    source = snapshot(current=7)
    envelope = SIGNER.sign(receipt_draft_from_snapshot(source))

    validate_receipt_binding(envelope, source)
    with pytest.raises(ReceiptVerificationError, match="receipt_does_not_match_publication"):
        validate_receipt_binding(
            envelope,
            replace(source, approved_payload_digest="0" * 64),
        )


@pytest.mark.parametrize(
    "event_type",
    [ReceiptEventType.CORRECTION, ReceiptEventType.REVOCATION],
)
def test_correction_and_revocation_require_prior_receipt_lineage(
    event_type: ReceiptEventType,
) -> None:
    payload = {
        **_draft().model_dump(mode="json"),
        "event_type": event_type.value,
    }
    with pytest.raises(ValueError, match="require a prior receipt"):
        PublicationReceiptDraft.model_validate(payload)


@pytest.mark.asyncio
async def test_receipt_adapters_sign_and_replicate_identical_verified_bytes() -> None:
    signer_store = MemoryPublicationReceiptStore(destination="urn:opennosh:receipt:signer")
    registry_store = MemoryPublicationReceiptStore(destination="urn:opennosh:registry:receipt")
    artifact_store = MemoryPublicationReceiptStore(destination="urn:opennosh:durability:receipt")
    signing = ReceiptSigningAdapter(
        signer=SIGNER,
        store=signer_store,
        key_ring=KEY_RING,
        clock=lambda: NOW,
    )
    sign_intent = _intent(
        PublicationStepName.SIGN_RECEIPT,
        signer_store.destination,
        {"receipt_draft": _draft().model_dump(mode="json")},
    )
    assert (await signing.observe(sign_intent)).status is ObservationStatus.ABSENT
    await signing.apply(sign_intent)
    signed = await signing.observe(sign_intent)

    assert signed.status is ObservationStatus.VERIFIED
    envelope_value = signed.context["signed_receipt"]
    assert isinstance(envelope_value, dict)
    for step, store in (
        (PublicationStepName.PUBLISH_RECEIPT_REGISTRY, registry_store),
        (PublicationStepName.COPY_RECEIPT, artifact_store),
    ):
        intent = _intent(step, store.destination, {"signed_receipt": envelope_value})
        adapter = ReceiptReplicationAdapter(
            step=step,
            store=store,
            key_ring=KEY_RING,
            clock=lambda: NOW,
        )
        await adapter.apply(intent)
        observed = await adapter.observe(intent)
        assert observed.status is ObservationStatus.VERIFIED
        assert observed.content_digest == signed.content_digest


@pytest.mark.asyncio
async def test_local_receipt_store_is_restart_safe_and_immutable(tmp_path: Path) -> None:
    envelope = SIGNER.sign(_draft())
    payload = canonical_signed_receipt_bytes(envelope)
    digest = signed_receipt_digest(envelope)
    key = receipt_object_key(PUBLICATION_ID)
    first = LocalImmutablePublicationReceiptStore(
        tmp_path, destination="urn:opennosh:durability:receipt"
    )
    await first.put_immutable(key, payload, expected_digest=digest)
    restarted = LocalImmutablePublicationReceiptStore(
        tmp_path, destination="urn:opennosh:durability:receipt"
    )

    assert await restarted.read(key) == payload
    assert (await restarted.list_keys()) == (key,)
    with pytest.raises(ImmutableReceiptConflictError):
        await restarted.put_immutable(
            key,
            b"different",
            expected_digest=hashlib.sha256(b"different").hexdigest(),
        )


def test_key_ring_can_be_loaded_from_versioned_public_key_json() -> None:
    public_key = PRIVATE_KEY.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    encoded = base64.urlsafe_b64encode(public_key).decode().rstrip("=")
    ring = PublicationReceiptKeyRing.from_json('{"publication-test-2026":"' + encoded + '"}')

    ring.verify(SIGNER.sign(_draft()))
