from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import BaseModel, ConfigDict

from opennosh_api.evidence.contracts import (
    EvidenceAcknowledgement,
    EvidenceAcknowledgementKind,
    EvidenceClass,
)
from opennosh_api.foods.schemas import FoodSource
from opennosh_api.nonproduction_keys import (
    ACCEPTANCE_MANIFEST_KEY_ID,
    ACCEPTANCE_MANIFEST_VERIFYING_KEY,
    ACCEPTANCE_RECEIPT_KEY_ID,
    ACCEPTANCE_RECEIPT_VERIFYING_KEY,
)
from opennosh_api.public.artifacts import (
    LocalArtifactStore,
    PublicArtifactReadService,
    PublicFoodArtifact,
    PublicReadLatestPointer,
    PublicReadReleaseManifest,
    activate_verified_release,
    artifact_descriptor,
)
from opennosh_api.public_commons.manifests import ManifestKeyRing, SignedEnvelope, canonical_json
from opennosh_api.publication.receipts import (
    Ed25519ReceiptSigner,
    PublicationReceiptDraft,
    PublicationReceiptKeyRing,
    ReceiptStepProof,
    SignedPublicationReceipt,
    canonical_signed_receipt_bytes,
    receipt_object_key,
)
from opennosh_api.publication.state import PublicationStepName, publication_protocol

ACCEPTANCE_RELEASE_VERSION = "1.0.0.0"
ACCEPTANCE_SOURCE = FoodSource.COMMUNITY
ACCEPTANCE_SOURCE_ID = "rajma-masala"
ACCEPTANCE_PUBLICATION_ID = UUID("11111111-1111-4111-8111-111111111111")
_MANIFEST_SIGNING_KEY = Ed25519PrivateKey.from_private_bytes(
    hashlib.sha256(b"opennosh-browser-acceptance-signing-key-v1").digest()
)
_RECEIPT_SIGNING_KEY = Ed25519PrivateKey.from_private_bytes(
    hashlib.sha256(b"opennosh-browser-acceptance-receipt-signing-key-v1").digest()
)
_FORGE_TARGET = "https://forge.example/opennosh/acceptance-pack"


class AcceptanceFixtureMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1"] = "1"
    release_version: str
    source: str
    source_id: str
    record_name: str
    immutable_url: str
    provenance_url: str
    manifest_url: str
    receipt_object_key: str
    receipt_digest: str
    published_at: datetime
    manifest_key_id: str
    manifest_verifying_key: str
    receipt_key_id: str
    receipt_verifying_key: str


def default_published_at() -> datetime:
    return datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=1)


async def materialize_browser_acceptance_fixture(
    artifact_directory: Path,
    state_directory: Path,
    *,
    published_at: datetime | None = None,
) -> AcceptanceFixtureMetadata:
    await asyncio.to_thread(artifact_directory.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(state_directory.mkdir, parents=True, exist_ok=True)
    published_at = await asyncio.to_thread(
        _resolve_published_at,
        state_directory,
        published_at,
    )

    record_bytes = canonical_json(_record())
    provenance_bytes = (
        b'<!doctype html><html lang="en"><meta charset="utf-8">'
        b"<title>Rajma masala provenance</title>"
        b"<h1>Verified evidence</h1>"
        b"<p>Recipe analysis checked against two household preparations.</p></html>"
    )
    record = artifact_descriptor(
        f"records/v1/{hashlib.sha256(record_bytes).hexdigest()}.json",
        record_bytes,
        "application/json",
    )
    provenance = artifact_descriptor(
        f"provenance/v1/{hashlib.sha256(provenance_bytes).hexdigest()}.html",
        provenance_bytes,
        "text/html",
    )
    receipt_key = receipt_object_key(ACCEPTANCE_PUBLICATION_ID)
    manifest = PublicReadReleaseManifest(
        release_version=ACCEPTANCE_RELEASE_VERSION,
        published_at=published_at,
        publication_receipt_key=receipt_key,
        foods=(
            PublicFoodArtifact(
                source=ACCEPTANCE_SOURCE,
                source_id=ACCEPTANCE_SOURCE_ID,
                record=record,
                provenance=provenance,
            ),
        ),
    )
    manifest_bytes = _signed_envelope(manifest.model_dump(mode="json"))
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    receipt = _receipt(manifest_digest, record.digest, published_at)
    receipt_bytes = canonical_signed_receipt_bytes(receipt)
    manifest_descriptor = artifact_descriptor(
        f"releases/v1/release-{ACCEPTANCE_RELEASE_VERSION}.json",
        manifest_bytes,
        "application/vnd.opennosh.release+json",
    )
    pointer = PublicReadLatestPointer(
        release_version=ACCEPTANCE_RELEASE_VERSION,
        manifest=manifest_descriptor,
        expires_at=published_at + timedelta(hours=23),
    )
    pointer_bytes = _signed_envelope(pointer.model_dump(mode="json"))

    store = LocalArtifactStore(artifact_directory)
    service = PublicArtifactReadService(
        store=store,
        manifest_keys=ManifestKeyRing.from_config(
            f"{ACCEPTANCE_MANIFEST_KEY_ID}:{ACCEPTANCE_MANIFEST_VERIFYING_KEY}"
        ),
        receipt_keys=PublicationReceiptKeyRing.from_json(
            json.dumps({ACCEPTANCE_RECEIPT_KEY_ID: ACCEPTANCE_RECEIPT_VERIFYING_KEY})
        ),
    )
    try:
        await activate_verified_release(
            service=service,
            store=store,
            immutable_objects={
                record.object_key: record_bytes,
                provenance.object_key: provenance_bytes,
            },
            manifest_bytes=manifest_bytes,
            receipt_bytes=receipt_bytes,
            pointer_bytes=pointer_bytes,
        )
        verified = await service.food(ACCEPTANCE_SOURCE, ACCEPTANCE_SOURCE_ID)
    finally:
        await service.aclose()

    metadata = AcceptanceFixtureMetadata(
        release_version=ACCEPTANCE_RELEASE_VERSION,
        source=ACCEPTANCE_SOURCE.value,
        source_id=ACCEPTANCE_SOURCE_ID,
        record_name=verified.record.name,
        immutable_url=verified.immutable_url,
        provenance_url=verified.provenance_url,
        manifest_url=f"/api/v1/public/releases/{ACCEPTANCE_RELEASE_VERSION}/manifest",
        receipt_object_key=receipt_key,
        receipt_digest=hashlib.sha256(receipt_bytes).hexdigest(),
        published_at=published_at,
        manifest_key_id=ACCEPTANCE_MANIFEST_KEY_ID,
        manifest_verifying_key=ACCEPTANCE_MANIFEST_VERIFYING_KEY,
        receipt_key_id=ACCEPTANCE_RECEIPT_KEY_ID,
        receipt_verifying_key=ACCEPTANCE_RECEIPT_VERIFYING_KEY,
    )
    _atomic_write(
        state_directory / "fixture.json",
        canonical_json(metadata.model_dump(mode="json")),
    )
    return metadata


def _resolve_published_at(state_directory: Path, requested: datetime | None) -> datetime:
    clock_path = state_directory / "published-at.txt"
    if clock_path.exists():
        try:
            existing = datetime.fromisoformat(clock_path.read_text().strip().replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("Acceptance fixture publication time is invalid") from error
        existing = _aware_utc(existing)
        if requested is not None and _aware_utc(requested) != existing:
            raise ValueError("Acceptance fixture publication time conflicts with existing state")
        return existing
    selected = _aware_utc(requested or default_published_at())
    _atomic_write(clock_path, selected.isoformat().replace("+00:00", "Z").encode())
    return selected


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Acceptance publication time must include a timezone")
    return value.astimezone(UTC).replace(microsecond=0)


def hand_fixture_to_runtime(
    artifact_directory: Path,
    state_directory: Path,
    *,
    uid: int,
    gid: int,
) -> None:
    get_effective_uid = getattr(os, "geteuid", None)
    change_owner = getattr(os, "chown", None)
    if get_effective_uid is None or change_owner is None or get_effective_uid() != 0:
        return
    for root in (artifact_directory, state_directory):
        for path in (root, *root.rglob("*")):
            change_owner(path, uid, gid, follow_symlinks=False)


def _record() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "id": "community:rajma-masala",
        "source": ACCEPTANCE_SOURCE.value,
        "source_id": ACCEPTANCE_SOURCE_ID,
        "name": "Rajma masala",
        "name_local": "राजमा मसाला",
        "category": "Punjabi home-style preparation",
        "attribution": {
            "source": ACCEPTANCE_SOURCE.value,
            "license": "CC0-1.0",
            "source_uri": "https://example.org/evidence/rajma-masala",
            "source_license": "CC BY 4.0",
            "contributed_by": "Punjab Foods Collective",
            "pack_id": "north-india-home-foods",
            "pack_version": "2.4.0",
            "provenance": "Recipe analysis checked against two household preparations",
        },
        "nutrients": {
            "basis": "per_100g",
            "nutrients": {
                "energy_kcal": "127",
                "protein_g": "6.2",
                "carbohydrate_g": "19.6",
                "fat_g": "2.6",
                "fibre_g": "5.8",
                "sodium_mg": "238",
            },
        },
        "portions": [
            {"name": "1 katori", "grams": "180"},
            {"name": "1 cup", "grams": "240"},
        ],
    }


def _signed_envelope(payload: dict[str, object]) -> bytes:
    signature = base64.urlsafe_b64encode(
        _MANIFEST_SIGNING_KEY.sign(canonical_json(payload))
    ).decode()
    envelope = SignedEnvelope(
        key_id=ACCEPTANCE_MANIFEST_KEY_ID,
        payload=payload,
        signature=signature.rstrip("="),
    )
    return canonical_json(envelope.model_dump(mode="json"))


def _receipt(
    manifest_digest: str,
    record_digest: str,
    published_at: datetime,
) -> SignedPublicationReceipt:
    definitions = publication_protocol(_FORGE_TARGET)[:7]
    copy_commit_digest = "1" * 64
    copy_evidence_digest = "2" * 64
    registry_digest = "3" * 64
    digest_by_step = {
        PublicationStepName.COMMIT_RECORD: record_digest,
        PublicationStepName.COPY_COMMIT: copy_commit_digest,
        PublicationStepName.COPY_EVIDENCE: copy_evidence_digest,
        PublicationStepName.SIGN_RELEASE: manifest_digest,
        PublicationStepName.PUBLISH_RELEASE: manifest_digest,
        PublicationStepName.COPY_RELEASE: manifest_digest,
        PublicationStepName.CONFIRM_REGISTRY: registry_digest,
    }
    proofs = tuple(
        ReceiptStepProof(
            step=definition.name,
            destination=definition.destination,
            content_digest=digest_by_step[definition.name],
            external_reference=(
                "b" * 40
                if definition.name is PublicationStepName.COMMIT_RECORD
                else f"acceptance:{definition.name.value}"
            ),
            verified_at=published_at,
            adapter_identity="opennosh.acceptance.fixture",
            adapter_version="1",
        )
        for definition in definitions
    )
    evidence_manifest_digest = "e" * 64
    evidence = EvidenceAcknowledgement(
        evidence_id=UUID("66666666-6666-4666-8666-666666666666"),
        evidence_class=EvidenceClass.SANITIZED_MEDIA,
        manifest_digest=evidence_manifest_digest,
        kind=EvidenceAcknowledgementKind.IMMUTABLE_SANITIZED_COPY,
        destination="urn:opennosh:durability:evidence",
        content_digest=copy_evidence_digest,
        external_reference="acceptance:evidence",
        verified_at=published_at,
        adapter_identity="opennosh.acceptance.fixture",
        adapter_version="1",
    )
    draft = PublicationReceiptDraft(
        publication_id=ACCEPTANCE_PUBLICATION_ID,
        pack_id="north-india-home-foods",
        record_id=ACCEPTANCE_SOURCE_ID,
        reviewed_decision_id=UUID("44444444-4444-4444-8444-444444444444"),
        approving_actor_id=UUID("55555555-5555-4555-8555-555555555555"),
        approving_actor_scope="pack:north-india-home-foods:steward",
        approved_payload_digest=record_digest,
        expected_base_commit="a" * 40,
        merged_commit="b" * 40,
        merged_tree_digest="c" * 64,
        evidence_manifest_digests=(evidence_manifest_digest,),
        evidence_acknowledgements=(evidence,),
        signed_release_metadata_digest=manifest_digest,
        release_version=ACCEPTANCE_RELEASE_VERSION,
        registry_acknowledgement_digest=registry_digest,
        registry_result="accepted",
        artifact_snapshot_digests=tuple(
            sorted({copy_commit_digest, copy_evidence_digest, manifest_digest})
        ),
        verified_steps=proofs,
        published_at=published_at,
        idempotency_key_hash="d" * 64,
    )
    return Ed25519ReceiptSigner(
        key_id=ACCEPTANCE_RECEIPT_KEY_ID,
        publisher_identity="opennosh:browser-acceptance",
        private_key=_RECEIPT_SIGNING_KEY,
        adapter_identity="opennosh.acceptance.fixture",
    ).sign(draft)


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
