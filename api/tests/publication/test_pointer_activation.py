from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timedelta
from typing import cast

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from opennosh_api.public.artifacts import (
    ArtifactDescriptor,
    PublicReadLatestPointer,
    PublicReadReleaseManifest,
)
from opennosh_api.public.r2 import R2ImmutableConflictError, R2PublicationError
from opennosh_api.public.signing import public_key_text, sign_envelope
from opennosh_api.public_commons.manifests import ManifestKeyRing, SignedEnvelope
from opennosh_api.publication.activation import (
    ReceiptGatedPointerActivationAdapter,
)
from opennosh_api.publication.adapters import (
    PublicationEffectAdapter,
    PublicationEffectError,
)
from opennosh_api.publication.receipt_adapters import ReceiptReplicationAdapter
from opennosh_api.publication.receipts import (
    Ed25519ReceiptSigner,
    MemoryPublicationReceiptStore,
    PublicationReceiptKeyRing,
    SignedPublicationReceipt,
    canonical_signed_receipt_bytes,
    receipt_draft_from_snapshot,
    signed_receipt_digest,
)
from opennosh_api.publication.state import (
    EffectIntent,
    ExternalObservation,
    ObservationStatus,
    PublicationStepName,
)
from tests.publication.test_planner import FORGE, NOW, PUBLICATION_ID, snapshot

RELEASE_VERSION = "0.62.0.0"
MANIFEST_KEY = Ed25519PrivateKey.from_private_bytes(b"m" * 32)
PREVIOUS_MANIFEST_KEY = Ed25519PrivateKey.from_private_bytes(b"p" * 32)
RECEIPT_KEY = Ed25519PrivateKey.from_private_bytes(b"r" * 32)
MANIFEST_KEYS = ManifestKeyRing.from_config(
    f"manifest-online:{public_key_text(MANIFEST_KEY)}"
)
ROTATION_MANIFEST_KEYS = ManifestKeyRing.from_config(
    ",".join(
        (
            f"manifest-online:{public_key_text(MANIFEST_KEY)}",
            f"manifest-previous:{public_key_text(PREVIOUS_MANIFEST_KEY)}",
        )
    )
)
RECEIPT_KEYS = PublicationReceiptKeyRing({"receipt-online": RECEIPT_KEY.public_key()})
RECEIPT_SIGNER = Ed25519ReceiptSigner(
    key_id="receipt-online",
    publisher_identity="opennosh:production-publication",
    private_key=RECEIPT_KEY,
)
DESTINATION = "urn:opennosh:durability:receipt"


class RecordingReceiptStore(MemoryPublicationReceiptStore):
    def __init__(self, events: list[str]) -> None:
        super().__init__(destination=DESTINATION)
        self.events = events

    async def put_immutable(
        self,
        object_key: str,
        payload: bytes,
        *,
        expected_digest: str,
    ) -> None:
        self.events.append("receipt-put")
        await super().put_immutable(
            object_key,
            payload,
            expected_digest=expected_digest,
        )


class ConflictingReceiptAdapter:
    identity = "test.conflicting-receipt"
    version = "1.0"

    async def apply(self, _intent: EffectIntent) -> None:
        return None

    async def observe(self, intent: EffectIntent) -> ExternalObservation:
        return ExternalObservation(
            step=intent.step,
            status=ObservationStatus.CONFLICT,
            observed_at=NOW,
            destination=intent.destination,
            effect_idempotency_key=intent.idempotency_key,
            adapter_identity=self.identity,
            adapter_version=self.version,
            code="durable_receipt_conflict",
        )


class MemoryPointerWriter:
    def __init__(self, objects: dict[str, bytes], events: list[str]) -> None:
        self.objects = dict(objects)
        self.events = events
        self.etag = "revision-1"
        self.conflict_payload: bytes | None = None
        self.use_candidate_on_conflict = False
        self.lose_write_response = False
        self.fail_readback = False
        self.next_readback_payload: bytes | None = None
        self.pointer_written = False
        self.fail_revision_reads = 0

    async def put_bytes(
        self,
        *,
        bucket: str,
        object_key: str,
        payload: bytes,
        media_type: str,
        cache_control: str,
        if_match: str | None = None,
        if_none_match: str | None = None,
    ) -> None:
        del bucket, media_type, cache_control, if_none_match
        self.events.append("pointer-put")
        if if_match != self.etag:
            raise R2ImmutableConflictError("stale ETag")
        if self.use_candidate_on_conflict or self.conflict_payload is not None:
            self.objects[object_key] = (
                payload if self.use_candidate_on_conflict else self.conflict_payload or b""
            )
            self.etag = "revision-conflict"
            raise R2ImmutableConflictError("concurrent pointer write")
        self.objects[object_key] = payload
        self.etag = "revision-2"
        self.pointer_written = True
        if self.lose_write_response:
            raise R2PublicationError("response lost after write")

    async def read_optional_bytes(
        self,
        *,
        bucket: str,
        object_key: str,
        max_bytes: int,
    ) -> bytes | None:
        del bucket
        self.events.append(f"pointer-read:{object_key}")
        if object_key == "latest/v1.json" and self.pointer_written:
            if self.fail_readback:
                self.fail_readback = False
                raise R2PublicationError("readback unavailable")
            if self.next_readback_payload is not None:
                payload = self.next_readback_payload
                self.next_readback_payload = None
                return payload
        payload = self.objects.get(object_key)
        if payload is not None and len(payload) > max_bytes:
            raise R2PublicationError("object too large")
        return payload

    async def read_revision(
        self,
        *,
        bucket: str,
        object_key: str,
        max_bytes: int,
    ) -> tuple[bytes, str]:
        if self.fail_revision_reads:
            self.fail_revision_reads -= 1
            raise R2PublicationError("revision unavailable")
        payload = await self.read_optional_bytes(
            bucket=bucket,
            object_key=object_key,
            max_bytes=max_bytes,
        )
        if payload is None:
            raise R2PublicationError("object absent")
        return payload, self.etag


def _release_material(
    *,
    manifest_key: Ed25519PrivateKey = MANIFEST_KEY,
    manifest_key_id: str = "manifest-online",
) -> tuple[bytes, SignedPublicationReceipt, bytes]:
    manifest = PublicReadReleaseManifest(
        release_version=RELEASE_VERSION,
        published_at=NOW,
        publication_receipt_key=f"receipts/v1/{PUBLICATION_ID}.json",
    )
    manifest_bytes = sign_envelope(
        manifest.model_dump(mode="json"),
        key_id=manifest_key_id,
        private_key=manifest_key,
    )
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    source = snapshot(current=7)
    acknowledgements = tuple(
        replace(
            item,
            content_digest=(
                manifest_digest
                if item.step
                in {
                    PublicationStepName.SIGN_RELEASE,
                    PublicationStepName.COPY_RELEASE,
                }
                else item.content_digest
            ),
            context={
                **dict(item.context),
                **(
                    {"release_version": RELEASE_VERSION}
                    if item.step is PublicationStepName.SIGN_RELEASE
                    else {}
                ),
            },
        )
        for item in source.acknowledgements
    )
    receipt = RECEIPT_SIGNER.sign(
        receipt_draft_from_snapshot(replace(source, acknowledgements=acknowledgements))
    )
    return manifest_bytes, receipt, canonical_signed_receipt_bytes(receipt)


def _old_pointer() -> bytes:
    pointer = PublicReadLatestPointer(
        release_version="0.56.0.0",
        manifest=ArtifactDescriptor(
            object_key="releases/v1/release-0.56.0.0.json",
            digest="0" * 64,
            size_bytes=1,
            media_type="application/vnd.opennosh.release+json",
        ),
        issued_at=NOW - timedelta(hours=2),
        expires_at=NOW + timedelta(hours=20),
    )
    return sign_envelope(
        pointer.model_dump(mode="json"),
        key_id="manifest-online",
        private_key=MANIFEST_KEY,
    )


def _newer_pointer() -> bytes:
    pointer = PublicReadLatestPointer(
        release_version="0.63.0.0",
        manifest=ArtifactDescriptor(
            object_key="releases/v1/release-0.63.0.0.json",
            digest="3" * 64,
            size_bytes=3,
            media_type="application/vnd.opennosh.release+json",
        ),
        issued_at=NOW,
        expires_at=NOW + timedelta(hours=23),
    )
    return sign_envelope(
        pointer.model_dump(mode="json"),
        key_id="manifest-online",
        private_key=MANIFEST_KEY,
    )


def _same_version_conflicting_pointer() -> bytes:
    pointer = PublicReadLatestPointer(
        release_version=RELEASE_VERSION,
        manifest=ArtifactDescriptor(
            object_key=f"releases/v1/release-{RELEASE_VERSION}.json",
            digest="4" * 64,
            size_bytes=4,
            media_type="application/vnd.opennosh.release+json",
        ),
        issued_at=NOW,
        expires_at=NOW + timedelta(hours=23),
    )
    return sign_envelope(
        pointer.model_dump(mode="json"),
        key_id="manifest-online",
        private_key=MANIFEST_KEY,
    )


async def _store_durable_receipt(
    store: RecordingReceiptStore,
    intent: EffectIntent,
) -> None:
    receipt = SignedPublicationReceipt.model_validate(intent.context["signed_receipt"])
    payload = canonical_signed_receipt_bytes(receipt)
    digest = signed_receipt_digest(receipt)
    await store.put_immutable(
        f"durability/receipts/{digest}.json",
        payload,
        expected_digest=digest,
    )


def _fixture(
    *,
    release_manifest_key: Ed25519PrivateKey = MANIFEST_KEY,
    release_manifest_key_id: str = "manifest-online",
    manifest_keys: ManifestKeyRing = MANIFEST_KEYS,
    clock: datetime = NOW,
) -> tuple[
    ReceiptGatedPointerActivationAdapter,
    EffectIntent,
    RecordingReceiptStore,
    MemoryPointerWriter,
    list[str],
]:
    manifest_bytes, receipt, receipt_bytes = _release_material(
        manifest_key=release_manifest_key,
        manifest_key_id=release_manifest_key_id,
    )
    events: list[str] = []
    store = RecordingReceiptStore(events)
    receipt_copy = ReceiptReplicationAdapter(
        step=PublicationStepName.COPY_RECEIPT,
        store=store,
        key_ring=RECEIPT_KEYS,
        clock=lambda: clock,
        object_key_factory=(
            lambda _publication_id, digest: f"durability/receipts/{digest}.json"
        ),
    )
    writer = MemoryPointerWriter(
        {
            f"releases/v1/release-{RELEASE_VERSION}.json": manifest_bytes,
            f"receipts/v1/{PUBLICATION_ID}.json": receipt_bytes,
            "latest/v1.json": _old_pointer(),
        },
        events,
    )
    adapter = ReceiptGatedPointerActivationAdapter(
        receipt_copy=receipt_copy,
        writer=writer,
        bucket="opennosh-public-commons",
        manifest_keys=manifest_keys,
        receipt_keys=RECEIPT_KEYS,
        signing_key_id="manifest-online",
        signing_key=MANIFEST_KEY,
        pointer_lifetime_seconds=82_800,
        clock=lambda: clock,
    )
    intent = EffectIntent(
        publication_id=PUBLICATION_ID,
        workflow_version="1.0",
        workflow_revision=9,
        step=PublicationStepName.COPY_RECEIPT,
        destination=DESTINATION,
        approved_payload_digest="a" * 64,
        idempotency_key="9" * 64,
        forge_target=FORGE,
        context={"signed_receipt": receipt.model_dump(mode="json")},
    )
    return adapter, intent, store, writer, events


@pytest.mark.asyncio
async def test_pointer_moves_only_after_the_durable_receipt_is_verified() -> None:
    adapter, intent, _store, writer, events = _fixture()
    previous = writer.objects["latest/v1.json"]

    before = await adapter.observe(intent)

    assert before.status is ObservationStatus.ABSENT
    assert writer.objects["latest/v1.json"] == previous
    await adapter.apply(intent)
    assert events.index("receipt-put") < events.index("pointer-put")


def test_constructor_rejects_invalid_adapter_and_empty_bucket() -> None:
    _adapter, _intent, _store, writer, _events = _fixture()
    arguments = {
        "writer": writer,
        "bucket": "opennosh-public-commons",
        "manifest_keys": MANIFEST_KEYS,
        "receipt_keys": RECEIPT_KEYS,
        "signing_key_id": "manifest-online",
        "signing_key": MANIFEST_KEY,
        "pointer_lifetime_seconds": 82_800,
        "clock": lambda: NOW,
    }
    with pytest.raises(ValueError, match="receipt-copy adapter"):
        ReceiptGatedPointerActivationAdapter(
            receipt_copy=cast(PublicationEffectAdapter, object()),
            **arguments,
        )
    with pytest.raises(ValueError, match="artifact bucket"):
        ReceiptGatedPointerActivationAdapter(
            receipt_copy=ConflictingReceiptAdapter(),
            **{**arguments, "bucket": ""},
        )


@pytest.mark.asyncio
async def test_conflicting_durable_receipt_causes_zero_pointer_io() -> None:
    _adapter, intent, _store, _writer, _events = _fixture()
    events: list[str] = []
    writer = MemoryPointerWriter({}, events)
    adapter = ReceiptGatedPointerActivationAdapter(
        receipt_copy=ConflictingReceiptAdapter(),
        writer=writer,
        bucket="opennosh-public-commons",
        manifest_keys=MANIFEST_KEYS,
        receipt_keys=RECEIPT_KEYS,
        signing_key_id="manifest-online",
        signing_key=MANIFEST_KEY,
        pointer_lifetime_seconds=82_800,
        clock=lambda: NOW,
    )

    with pytest.raises(PublicationEffectError) as raised:
        await adapter.apply(intent)

    assert raised.value.status is ObservationStatus.CONFLICT
    assert raised.value.code == "durable_receipt_conflict"
    assert events == []


@pytest.mark.asyncio
async def test_activation_is_observable_with_receipt_and_pointer_proof() -> None:
    adapter, intent, _store, writer, _events = _fixture()
    await adapter.apply(intent)

    observed = await adapter.observe(intent)
    pointer = PublicReadLatestPointer.model_validate(
        SignedEnvelope.model_validate_json(writer.objects["latest/v1.json"]).payload
    )
    receipt = SignedPublicationReceipt.model_validate(intent.context["signed_receipt"])

    assert observed.status is ObservationStatus.VERIFIED
    assert observed.content_digest == signed_receipt_digest(receipt)
    assert observed.context["pointer_release_version"] == RELEASE_VERSION
    assert observed.context["pointer_manifest_digest"] == pointer.manifest.digest
    assert pointer.release_version == RELEASE_VERSION


@pytest.mark.asyncio
async def test_verified_receipt_observes_missing_before_and_old_pointer_states() -> None:
    missing_adapter, missing_intent, missing_store, missing_writer, _events = _fixture()
    await _store_durable_receipt(missing_store, missing_intent)
    del missing_writer.objects[f"releases/v1/release-{RELEASE_VERSION}.json"]
    missing = await missing_adapter.observe(missing_intent)
    assert missing.status is ObservationStatus.RETRYABLE_FAILURE
    assert missing.code == "activation_material_absent"

    absent_adapter, absent_intent, absent_store, absent_writer, _events = _fixture()
    await _store_durable_receipt(absent_store, absent_intent)
    del absent_writer.objects["latest/v1.json"]
    assert (await absent_adapter.observe(absent_intent)).status is ObservationStatus.ABSENT

    old_adapter, old_intent, old_store, _writer, _events = _fixture()
    await _store_durable_receipt(old_store, old_intent)
    assert (await old_adapter.observe(old_intent)).status is ObservationStatus.ABSENT


@pytest.mark.asyncio
async def test_trusted_previous_manifest_key_can_finish_during_rotation() -> None:
    adapter, intent, _store, writer, _events = _fixture(
        release_manifest_key=PREVIOUS_MANIFEST_KEY,
        release_manifest_key_id="manifest-previous",
        manifest_keys=ROTATION_MANIFEST_KEYS,
    )

    await adapter.apply(intent)

    observed = await adapter.observe(intent)
    pointer_envelope = SignedEnvelope.model_validate_json(
        writer.objects["latest/v1.json"]
    )
    assert observed.status is ObservationStatus.VERIFIED
    assert pointer_envelope.key_id == "manifest-online"


@pytest.mark.asyncio
async def test_same_release_cas_winner_is_accepted_as_an_idempotent_replay() -> None:
    adapter, intent, _store, writer, _events = _fixture()
    writer.use_candidate_on_conflict = True

    await adapter.apply(intent)

    assert (await adapter.observe(intent)).status is ObservationStatus.VERIFIED


@pytest.mark.asyncio
async def test_newer_cas_winner_terminally_supersedes_the_release() -> None:
    adapter, intent, _store, writer, _events = _fixture()
    previous = writer.objects["latest/v1.json"]
    writer.conflict_payload = _newer_pointer()

    await adapter.apply(intent)
    assert writer.objects["latest/v1.json"] != previous
    observed = await adapter.observe(intent)
    assert observed.status is ObservationStatus.VERIFIED
    assert observed.code == "latest_pointer_superseded"
    assert observed.context["pointer_release_version"] == "0.63.0.0"


@pytest.mark.asyncio
async def test_lost_write_response_is_reconciled_by_observation() -> None:
    adapter, intent, _store, writer, _events = _fixture()
    writer.lose_write_response = True

    with pytest.raises(PublicationEffectError) as raised:
        await adapter.apply(intent)

    assert raised.value.code == "latest_pointer_write_failed"
    observed = await adapter.observe(intent)
    assert observed.status is ObservationStatus.VERIFIED
    assert observed.context["pointer_release_version"] == RELEASE_VERSION


@pytest.mark.asyncio
async def test_post_write_readback_failure_is_retryable_and_reconciles() -> None:
    adapter, intent, _store, writer, _events = _fixture()
    writer.fail_readback = True

    with pytest.raises(PublicationEffectError) as raised:
        await adapter.apply(intent)

    assert raised.value.code == "latest_pointer_readback_failed"
    assert (await adapter.observe(intent)).status is ObservationStatus.VERIFIED


@pytest.mark.asyncio
async def test_initial_pointer_read_failure_is_retryable() -> None:
    adapter, intent, _store, writer, _events = _fixture()
    writer.fail_revision_reads = 1

    with pytest.raises(PublicationEffectError) as raised:
        await adapter.apply(intent)

    assert raised.value.code == "latest_pointer_read_failed"


@pytest.mark.asyncio
async def test_transient_readback_mismatch_reconciles_the_cas_winner() -> None:
    adapter, intent, _store, writer, _events = _fixture()
    writer.next_readback_payload = _newer_pointer()

    await adapter.apply(intent)

    assert (await adapter.observe(intent)).status is ObservationStatus.VERIFIED


@pytest.mark.asyncio
async def test_mismatched_registry_receipt_never_moves_latest() -> None:
    adapter, intent, _store, writer, _events = _fixture()
    previous = writer.objects["latest/v1.json"]
    writer.objects[f"receipts/v1/{PUBLICATION_ID}.json"] += b"tampered"

    with pytest.raises(PublicationEffectError) as raised:
        await adapter.apply(intent)

    assert raised.value.status is ObservationStatus.CONFLICT
    assert writer.objects["latest/v1.json"] == previous


@pytest.mark.asyncio
async def test_validly_signed_but_incorrectly_bound_manifest_never_moves_latest() -> None:
    adapter, intent, _store, writer, events = _fixture()
    wrong_manifest = PublicReadReleaseManifest(
        release_version=RELEASE_VERSION,
        published_at=NOW,
        publication_receipt_key=(
            "receipts/v1/00000000-0000-0000-0000-000000000001.json"
        ),
    )
    writer.objects[f"releases/v1/release-{RELEASE_VERSION}.json"] = sign_envelope(
        wrong_manifest.model_dump(mode="json"),
        key_id="manifest-online",
        private_key=MANIFEST_KEY,
    )

    with pytest.raises(PublicationEffectError) as raised:
        await adapter.apply(intent)

    assert raised.value.status is ObservationStatus.CONFLICT
    assert raised.value.code == "activation_material_binding_conflict"
    assert "pointer-put" not in events


@pytest.mark.asyncio
async def test_missing_activation_material_is_retryable_without_pointer_write() -> None:
    adapter, intent, _store, writer, events = _fixture()
    del writer.objects[f"releases/v1/release-{RELEASE_VERSION}.json"]

    with pytest.raises(PublicationEffectError) as raised:
        await adapter.apply(intent)

    assert raised.value.status is ObservationStatus.RETRYABLE_FAILURE
    assert raised.value.code == "activation_material_absent"
    assert "pointer-put" not in events


@pytest.mark.asyncio
async def test_clock_behind_release_is_retryable_without_pointer_write() -> None:
    adapter, intent, _store, _writer, events = _fixture(
        clock=NOW - timedelta(seconds=1)
    )

    with pytest.raises(PublicationEffectError) as raised:
        await adapter.apply(intent)

    assert raised.value.status is ObservationStatus.RETRYABLE_FAILURE
    assert raised.value.code == "pointer_activation_clock_behind_release"
    assert "pointer-put" not in events


@pytest.mark.asyncio
async def test_same_version_different_manifest_is_quarantined() -> None:
    adapter, intent, store, writer, _events = _fixture()
    await _store_durable_receipt(store, intent)
    writer.objects["latest/v1.json"] = _same_version_conflicting_pointer()

    observed = await adapter.observe(intent)

    assert observed.status is ObservationStatus.CONFLICT
    assert observed.code == "latest_pointer_identity_conflict"
    with pytest.raises(PublicationEffectError) as raised:
        await adapter.apply(intent)
    assert raised.value.code == "latest_pointer_identity_conflict"


@pytest.mark.asyncio
async def test_untrusted_latest_pointer_is_quarantined() -> None:
    adapter, intent, store, writer, _events = _fixture()
    await _store_durable_receipt(store, intent)
    rogue_key = Ed25519PrivateKey.from_private_bytes(b"x" * 32)
    pointer = PublicReadLatestPointer(
        release_version=RELEASE_VERSION,
        manifest=ArtifactDescriptor(
            object_key=f"releases/v1/release-{RELEASE_VERSION}.json",
            digest="4" * 64,
            size_bytes=4,
            media_type="application/vnd.opennosh.release+json",
        ),
        issued_at=NOW,
        expires_at=NOW + timedelta(hours=23),
    )
    writer.objects["latest/v1.json"] = sign_envelope(
        pointer.model_dump(mode="json"),
        key_id="rogue",
        private_key=rogue_key,
    )

    observed = await adapter.observe(intent)

    assert observed.status is ObservationStatus.CONFLICT
    assert observed.code == "latest_pointer_untrusted"


@pytest.mark.asyncio
async def test_pointer_lifetime_over_24_hours_is_quarantined() -> None:
    adapter, intent, _store, writer, _events = _fixture()
    await adapter.apply(intent)
    envelope = SignedEnvelope.model_validate_json(writer.objects["latest/v1.json"])
    pointer = PublicReadLatestPointer.model_validate(envelope.payload)
    issued_at = pointer.issued_at or NOW
    invalid = pointer.model_copy(
        update={"expires_at": issued_at + timedelta(hours=25)}
    )
    writer.objects["latest/v1.json"] = sign_envelope(
        invalid.model_dump(mode="json"),
        key_id="manifest-online",
        private_key=MANIFEST_KEY,
    )

    observed = await adapter.observe(intent)

    assert observed.status is ObservationStatus.CONFLICT
    assert observed.code == "latest_pointer_lifetime_invalid"
    with pytest.raises(PublicationEffectError) as raised:
        await adapter.apply(intent)
    assert raised.value.code == "latest_pointer_lifetime_invalid"


@pytest.mark.asyncio
async def test_invalid_newer_pointer_cannot_supersede_a_release() -> None:
    adapter, intent, store, writer, _events = _fixture()
    await _store_durable_receipt(store, intent)
    envelope = SignedEnvelope.model_validate_json(_newer_pointer())
    pointer = PublicReadLatestPointer.model_validate(envelope.payload)
    issued_at = pointer.issued_at or NOW
    invalid = pointer.model_copy(
        update={"expires_at": issued_at + timedelta(hours=25)}
    )
    writer.objects["latest/v1.json"] = sign_envelope(
        invalid.model_dump(mode="json"),
        key_id="manifest-online",
        private_key=MANIFEST_KEY,
    )

    observed = await adapter.observe(intent)
    assert observed.status is ObservationStatus.CONFLICT
    assert observed.code == "latest_pointer_lifetime_invalid"
    with pytest.raises(PublicationEffectError) as raised:
        await adapter.apply(intent)
    assert raised.value.code == "latest_pointer_lifetime_invalid"


@pytest.mark.asyncio
async def test_replay_of_an_active_release_does_not_rewrite_latest() -> None:
    adapter, intent, _store, writer, events = _fixture()
    await adapter.apply(intent)
    events.clear()

    await adapter.apply(intent)

    assert "pointer-put" not in events
    assert (await adapter.observe(intent)).status is ObservationStatus.VERIFIED


def test_pointer_activation_rejects_a_lifetime_over_24_hours() -> None:
    adapter, _intent, _store, writer, _events = _fixture()

    with pytest.raises(ValueError, match="24 hours"):
        ReceiptGatedPointerActivationAdapter(
            receipt_copy=adapter._receipt_copy,  # type: ignore[attr-defined]
            writer=writer,
            bucket="opennosh-public-commons",
            manifest_keys=MANIFEST_KEYS,
            receipt_keys=RECEIPT_KEYS,
            signing_key_id="manifest-online",
            signing_key=MANIFEST_KEY,
            pointer_lifetime_seconds=86_401,
            clock=lambda: NOW,
        )
