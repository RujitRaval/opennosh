from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from opennosh_api.evidence.contracts import (
    DocumentRightsState,
    EvidenceAcknowledgementKind,
    EvidencePublicState,
    MaintainerAttestationManifest,
    PublicDocumentManifest,
    SanitizedMediaManifest,
    VersionedPublicDatasetManifest,
)
from opennosh_api.evidence.policy import verify_durability
from opennosh_api.evidence.signing import (
    EvidenceSignatureError,
    EvidenceVerificationKeyRing,
    signature_material,
)
from opennosh_api.evidence.storage import (
    ImmutableObjectConflictError,
    LocalImmutableEvidenceStore,
    LocalPrivateEvidenceSource,
    MemoryEvidenceStore,
)
from opennosh_api.evidence.worker import (
    EvidencePreservationWorker,
    EvidenceSourceUnavailableError,
)

NOW = datetime(2026, 8, 26, 12, tzinfo=UTC)
MEDIA = b"safe rewritten image"
DATASET = b'{"record":"verified"}'


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _media() -> SanitizedMediaManifest:
    return SanitizedMediaManifest(
        evidence_id=uuid4(),
        content_digest=_digest(MEDIA),
        safe_format="image/webp",
        source_description="Sanitized label",
        rights_acknowledged=True,
        redaction_state="reviewed",
        storage_reference="private:media/source.webp",
    )


def _key_ring_and_sign(manifest: object) -> tuple[EvidenceVerificationKeyRing, object]:
    private_key = Ed25519PrivateKey.generate()
    signed = manifest.model_copy(  # type: ignore[attr-defined]
        update={
            "signature": base64.urlsafe_b64encode(
                private_key.sign(signature_material(manifest))  # type: ignore[arg-type]
            ).rstrip(b"=").decode()
        }
    )
    public_key = base64.urlsafe_b64encode(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).rstrip(b"=").decode()
    principal = (
        signed.publisher
        if isinstance(signed, VersionedPublicDatasetManifest)
        else signed.authority_id
    )
    return EvidenceVerificationKeyRing.from_config(
        f'{{"{principal}":{{"{signed.signature_key_id}":"{public_key}"}}}}'
    ), signed


@pytest.mark.asyncio
async def test_worker_preserves_media_and_reuses_duplicate_delivery() -> None:
    manifest = _media()
    store = MemoryEvidenceStore(destination="urn:fixture:independent")
    worker = EvidencePreservationWorker(store)
    payloads = {EvidenceAcknowledgementKind.IMMUTABLE_SANITIZED_COPY: MEDIA}

    first = await worker.preserve(manifest, payloads=payloads, now=NOW)
    second = await worker.preserve(manifest, payloads=payloads, now=NOW)

    assert first == second
    assert len(store.objects) == 1
    assert verify_durability(manifest, first) is EvidencePublicState.EVIDENCE_PRESERVED


@pytest.mark.asyncio
async def test_worker_preserves_reference_manifest_without_source_bytes() -> None:
    manifest = PublicDocumentManifest(
        evidence_id=uuid4(),
        canonical_uri="https://example.test/reference",
        publisher="Publisher",
        license="CC-BY-4.0",
        title="Reference only",
        observed_at=NOW,
        observed_digest=_digest(b"observed but not retained"),
        rights_state=DocumentRightsState.REFERENCE_ONLY,
    )
    worker = EvidencePreservationWorker(MemoryEvidenceStore())

    acknowledgements = await worker.preserve(manifest, payloads={}, now=NOW)

    assert verify_durability(manifest, acknowledgements) is EvidencePublicState.REFERENCE_ONLY


@pytest.mark.asyncio
async def test_signed_dataset_and_attestation_require_trusted_valid_signatures() -> None:
    dataset = VersionedPublicDatasetManifest(
        evidence_id=uuid4(),
        dataset_id="usda-fdc",
        release_version="2026-08",
        record_id="12345",
        publisher="USDA",
        license="public-domain",
        source_uri="https://fdc.nal.usda.gov/fdc-app.html#/food-details/12345",
        canonical_record_digest=_digest(DATASET),
        signature_key_id="usda-2026",
        signature="A" * 86,
        archival_permitted=False,
    )
    dataset_keys, signed_dataset = _key_ring_and_sign(dataset)
    dataset_acknowledgements = await EvidencePreservationWorker(
        MemoryEvidenceStore(), key_ring=dataset_keys
    ).preserve(signed_dataset, payloads={}, now=NOW)
    assert (
        verify_durability(signed_dataset, dataset_acknowledgements)
        is EvidencePublicState.SOURCE_VERIFIED
    )

    attestation = MaintainerAttestationManifest(
        evidence_id=uuid4(),
        authority_id="maintainer:global-core:alice",
        scope="record:global-core/test-dal",
        signed_statement="The reviewed values match the named source.",
        signature_key_id="alice-2026",
        signature="A" * 86,
        attested_at=NOW,
        license="CC0-1.0",
        supporting_reference="https://example.test/attestation/source",
    )
    attestation_keys, signed_attestation = _key_ring_and_sign(attestation)
    attestation_acknowledgements = await EvidencePreservationWorker(
        MemoryEvidenceStore(), key_ring=attestation_keys
    ).preserve(signed_attestation, payloads={}, now=NOW)
    assert (
        verify_durability(signed_attestation, attestation_acknowledgements)
        is EvidencePublicState.ATTESTED
    )

    with pytest.raises(EvidenceSignatureError, match="not trusted"):
        await EvidencePreservationWorker(MemoryEvidenceStore()).preserve(
            signed_dataset, payloads={}, now=NOW
        )
    with pytest.raises(EvidenceSignatureError, match="verification failed"):
        await EvidencePreservationWorker(
            MemoryEvidenceStore(), key_ring=dataset_keys
        ).preserve(
            signed_dataset.model_copy(update={"record_id": "forged"}),
            payloads={},
            now=NOW,
        )


@pytest.mark.asyncio
async def test_missing_source_and_digest_mismatch_never_acknowledge() -> None:
    manifest = _media()
    worker = EvidencePreservationWorker(MemoryEvidenceStore())

    with pytest.raises(EvidenceSourceUnavailableError, match="payload is unavailable"):
        await worker.preserve(manifest, payloads={}, now=NOW)
    with pytest.raises(ValueError, match="does not match"):
        await worker.preserve(
            manifest,
            payloads={EvidenceAcknowledgementKind.IMMUTABLE_SANITIZED_COPY: b"tampered"},
            now=NOW,
        )


@pytest.mark.asyncio
async def test_local_store_is_content_addressed_and_rejects_conflict(tmp_path: Path) -> None:
    store = LocalImmutableEvidenceStore(tmp_path)
    key = f"evidence/{uuid4()}/object/{_digest(MEDIA)}"

    await store.put_immutable(key, MEDIA, expected_digest=_digest(MEDIA))
    await store.put_immutable(key, MEDIA, expected_digest=_digest(MEDIA))
    observation = await store.observe(key)

    assert observation is not None
    assert observation.content_digest == _digest(MEDIA)
    target = tmp_path / key
    target.chmod(0o640)
    target.write_bytes(b"different")
    with pytest.raises(ImmutableObjectConflictError, match="different bytes"):
        await store.put_immutable(key, MEDIA, expected_digest=_digest(MEDIA))


@pytest.mark.asyncio
async def test_local_store_rejects_path_escape(tmp_path: Path) -> None:
    store = LocalImmutableEvidenceStore(tmp_path)

    with pytest.raises(ValueError, match="object key is invalid"):
        await store.put_immutable("../escape", MEDIA, expected_digest=_digest(MEDIA))


@pytest.mark.asyncio
async def test_local_store_ignores_orphaned_pending_write_after_crash(tmp_path: Path) -> None:
    store = LocalImmutableEvidenceStore(tmp_path)
    key = f"evidence/{uuid4()}/object/{_digest(MEDIA)}"
    target = tmp_path / key
    target.parent.mkdir(parents=True)
    (target.parent / f".{target.name}.crashed.pending").write_bytes(b"partial")

    await store.put_immutable(key, MEDIA, expected_digest=_digest(MEDIA))

    observation = await store.observe(key)
    assert target.read_bytes() == MEDIA
    assert observation is not None
    assert observation.content_digest == _digest(MEDIA)


@pytest.mark.asyncio
async def test_local_private_source_reads_only_manifest_bound_paths(tmp_path: Path) -> None:
    source_path = tmp_path / "media" / "source.webp"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(MEDIA)
    manifest = _media().model_copy(update={"storage_reference": "private:media/source.webp"})

    payloads = await LocalPrivateEvidenceSource(tmp_path).payloads_for(manifest)

    assert payloads == {EvidenceAcknowledgementKind.IMMUTABLE_SANITIZED_COPY: MEDIA}
    escaped = manifest.model_copy(update={"storage_reference": "private:../outside"})
    with pytest.raises(ValueError, match="object key is invalid"):
        await LocalPrivateEvidenceSource(tmp_path).payloads_for(escaped)
