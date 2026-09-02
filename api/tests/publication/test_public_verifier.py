from __future__ import annotations

import base64
import hashlib
from dataclasses import replace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from opennosh_api.foods.schemas import FoodSource
from opennosh_api.public.artifacts import (
    MemoryArtifactStore,
    PublicFoodArtifact,
    PublicFoodRecord,
    PublicFoodRecordResponse,
    PublicReadReleaseManifest,
    PublicReleaseMetadata,
    ResolvedRelease,
    artifact_descriptor,
)
from opennosh_api.public_commons.manifests import SignedEnvelope, canonical_json
from opennosh_api.publication.public_verifier import (
    NaturalPublicVerificationError,
    verify_natural_publication_artifacts,
)
from opennosh_api.publication.receipts import (
    Ed25519ReceiptSigner,
    PublicationReceiptDraft,
    PublicationReceiptKeyRing,
    canonical_signed_receipt_bytes,
    parse_signed_receipt,
    receipt_draft_from_snapshot,
)
from opennosh_api.publication.state import PublicationStepName
from tests.publication.test_planner import NOW, snapshot

RELEASE = "0.71.0.0"
PACK_ID = "north-india-home-foods"
RECORD_ID = "rajma-masala"
PROVENANCE = b"<!doctype html><title>Rajma provenance</title><p>Verified evidence.</p>"
RECORD = {
    "id": f"community:{RECORD_ID}",
    "source": "community",
    "source_id": RECORD_ID,
    "name": "Rajma masala",
    "name_local": None,
    "category": "Home-style preparation",
    "attribution": {
        "source": "community",
        "license": "CC0-1.0",
        "source_uri": "https://example.org/source",
        "source_license": "CC BY 4.0",
        "contributed_by": "Community contributor",
        "pack_id": PACK_ID,
        "pack_version": "2.4.0",
        "provenance": "Verified evidence",
    },
    "nutrients": {
        "basis": "per_100g",
        "nutrients": {"energy_kcal": "127", "protein_g": "6.2"},
    },
    "portions": [{"grams": "180", "name": "1 katori"}],
}
RECEIPT_PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(b"n" * 32)
RECEIPT_KEYS = PublicationReceiptKeyRing({"natural-test": RECEIPT_PRIVATE_KEY.public_key()})
RECEIPT_SIGNER = Ed25519ReceiptSigner(
    key_id="natural-test",
    publisher_identity="opennosh:natural-test",
    private_key=RECEIPT_PRIVATE_KEY,
)


class FakeReader:
    def __init__(
        self,
        food: PublicFoodRecordResponse,
        provenance: bytes,
        manifest_bytes: bytes,
        release: ResolvedRelease,
    ) -> None:
        self.food_response = food
        self.latest_response: PublicFoodRecordResponse | None = None
        self.provenance_bytes = provenance
        self.manifest_bytes = manifest_bytes
        self.release = release

    async def food(
        self,
        source: FoodSource,
        source_id: str,
        *,
        release_version: str | None = None,
    ) -> PublicFoodRecordResponse:
        assert source is FoodSource.COMMUNITY
        assert source_id == RECORD_ID
        assert release_version in {None, RELEASE}
        if release_version is None and self.latest_response is not None:
            return self.latest_response
        return self.food_response

    async def provenance(
        self,
        source: FoodSource,
        source_id: str,
        *,
        release_version: str,
    ) -> tuple[bytes, ResolvedRelease]:
        assert source is FoodSource.COMMUNITY
        assert source_id == RECORD_ID
        assert release_version == RELEASE
        return self.provenance_bytes, self.release

    async def signed_manifest(self, release_version: str) -> tuple[bytes, ResolvedRelease]:
        assert release_version == RELEASE
        return self.manifest_bytes, self.release


def fixture() -> tuple[FakeReader, MemoryArtifactStore, str, str]:
    record = PublicFoodRecord.model_validate(RECORD)
    record_bytes = canonical_json(record.model_dump(mode="json"))
    record_descriptor = artifact_descriptor(
        f"records/v1/{hashlib.sha256(record_bytes).hexdigest()}",
        record_bytes,
        "application/json",
    )
    provenance_descriptor = artifact_descriptor(
        f"provenance/v1/{hashlib.sha256(PROVENANCE).hexdigest()}",
        PROVENANCE,
        "text/html",
    )
    receipt_key = "receipts/v1/11111111-1111-4111-8111-111111111111.json"
    manifest = PublicReadReleaseManifest(
        release_version=RELEASE,
        published_at=NOW,
        publication_receipt_key=receipt_key,
        foods=(
            PublicFoodArtifact(
                source=FoodSource.COMMUNITY,
                source_id=RECORD_ID,
                record=record_descriptor,
                provenance=provenance_descriptor,
            ),
        ),
    )
    manifest_payload = manifest.model_dump(mode="json")
    manifest_envelope = SignedEnvelope(
        key_id="manifest-natural-test",
        payload=manifest_payload,
        signature=base64.urlsafe_b64encode(b"m" * 64).decode().rstrip("="),
    )
    manifest_bytes = canonical_json(manifest_envelope.model_dump(mode="json"))
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    source = snapshot(current=7)
    acknowledgements = tuple(
        replace(
            acknowledgement,
            content_digest=(
                manifest_digest
                if acknowledgement.step
                in {PublicationStepName.SIGN_RELEASE, PublicationStepName.COPY_RELEASE}
                else acknowledgement.content_digest
            ),
            context={
                **dict(acknowledgement.context),
                **(
                    {"release_version": RELEASE}
                    if acknowledgement.step is PublicationStepName.SIGN_RELEASE
                    else {}
                ),
            },
        )
        for acknowledgement in source.acknowledgements
    )
    receipt = RECEIPT_SIGNER.sign(
        receipt_draft_from_snapshot(
            replace(
                source,
                pack_id=PACK_ID,
                record_id=RECORD_ID,
                acknowledgements=acknowledgements,
            )
        )
    )
    receipt_bytes = canonical_signed_receipt_bytes(receipt)
    receipt_digest = hashlib.sha256(receipt_bytes).hexdigest()
    store = MemoryArtifactStore()
    store.objects[receipt_key] = receipt_bytes
    metadata = PublicReleaseMetadata(
        release_version=RELEASE,
        published_at=NOW,
        state="verified",
        stale_age_seconds=0,
    )
    release = ResolvedRelease(manifest, manifest_envelope, manifest_bytes, metadata)
    food = PublicFoodRecordResponse(
        record=record,
        release=metadata,
        immutable_url=f"/api/v1/public/releases/{RELEASE}/foods/community/{RECORD_ID}",
        provenance_url=(
            f"/api/v1/public/releases/{RELEASE}/foods/community/{RECORD_ID}/provenance"
        ),
    )
    return (
        FakeReader(food, PROVENANCE, manifest_bytes, release),
        store,
        manifest_digest,
        receipt_digest,
    )


@pytest.mark.asyncio
async def test_verifies_arbitrary_record_against_manifest_receipt_and_latest() -> None:
    reader, store, manifest_digest, receipt_digest = fixture()

    result = await verify_natural_publication_artifacts(
        reader=reader,
        store=store,
        receipt_keys=RECEIPT_KEYS,
        pack_id=PACK_ID,
        record_id=RECORD_ID,
        expected_release_version=RELEASE,
        expected_manifest_sha256=manifest_digest,
        expected_receipt_sha256=receipt_digest,
    )

    assert result.release_version == RELEASE
    assert result.manifest_sha256 == manifest_digest
    assert result.receipt_sha256 == receipt_digest
    assert (
        result.record_sha256
        == hashlib.sha256(
            canonical_json(PublicFoodRecord.model_validate(RECORD).model_dump(mode="json"))
        ).hexdigest()
    )
    assert result.provenance_sha256 == hashlib.sha256(PROVENANCE).hexdigest()
    assert result.to_dict()["release_version"] == RELEASE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("pack_id", "wrong-pack", "public_record_pack_mismatch"),
        ("expected_release_version", "0.70.0.0", "public_artifact_read_failed"),
        ("expected_manifest_sha256", "0" * 64, "public_manifest_digest_mismatch"),
        ("expected_receipt_sha256", "0" * 64, "public_receipt_digest_mismatch"),
    ],
)
async def test_public_verifier_fails_closed_at_each_expected_identity(
    field: str,
    value: str,
    code: str,
) -> None:
    reader, store, manifest_digest, receipt_digest = fixture()
    arguments = {
        "reader": reader,
        "store": store,
        "receipt_keys": RECEIPT_KEYS,
        "pack_id": PACK_ID,
        "record_id": RECORD_ID,
        "expected_release_version": RELEASE,
        "expected_manifest_sha256": manifest_digest,
        "expected_receipt_sha256": receipt_digest,
    }
    arguments[field] = value

    with pytest.raises(NaturalPublicVerificationError, match=code):
        await verify_natural_publication_artifacts(**arguments)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_public_verifier_rejects_missing_receipt() -> None:
    reader, store, manifest_digest, receipt_digest = fixture()
    store.objects.clear()

    with pytest.raises(NaturalPublicVerificationError, match="public_receipt_missing"):
        await verify_natural_publication_artifacts(
            reader=reader,
            store=store,
            receipt_keys=RECEIPT_KEYS,
            pack_id=PACK_ID,
            record_id=RECORD_ID,
            expected_release_version=RELEASE,
            expected_manifest_sha256=manifest_digest,
            expected_receipt_sha256=receipt_digest,
        )


def arguments() -> dict[str, object]:
    reader, store, manifest_digest, receipt_digest = fixture()
    return {
        "reader": reader,
        "store": store,
        "receipt_keys": RECEIPT_KEYS,
        "pack_id": PACK_ID,
        "record_id": RECORD_ID,
        "expected_release_version": RELEASE,
        "expected_manifest_sha256": manifest_digest,
        "expected_receipt_sha256": receipt_digest,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("immutable_release", "immutable_release_version_mismatch"),
        ("latest_release", "latest_release_version_mismatch"),
        ("unverified", "public_release_not_freshly_verified"),
        ("latest_record", "latest_immutable_record_mismatch"),
        ("record_identity", "public_record_identity_mismatch"),
        ("provenance_release", "provenance_release_mismatch"),
        ("receipt_signature", "public_receipt_signature_invalid"),
        ("receipt_binding", "public_receipt_binding_mismatch"),
        ("manifest_record", "public_manifest_record_missing"),
        ("artifact_digest", "public_artifact_digest_mismatch"),
    ],
)
async def test_public_verifier_rejects_every_artifact_boundary(
    mutation: str,
    code: str,
) -> None:
    values = arguments()
    reader = values["reader"]
    store = values["store"]
    assert isinstance(reader, FakeReader)
    assert isinstance(store, MemoryArtifactStore)
    wrong_metadata = reader.food_response.release.model_copy(
        update={"release_version": "9.9.9.9"}
    )
    if mutation == "immutable_release":
        reader.food_response = reader.food_response.model_copy(update={"release": wrong_metadata})
        reader.latest_response = reader.food_response.model_copy(
            update={"release": reader.release.metadata}
        )
    elif mutation == "latest_release":
        reader.latest_response = reader.food_response.model_copy(update={"release": wrong_metadata})
    elif mutation == "unverified":
        stale = reader.food_response.release.model_copy(update={"state": "stale"})
        reader.food_response = reader.food_response.model_copy(update={"release": stale})
    elif mutation == "latest_record":
        changed = reader.food_response.record.model_copy(update={"name": "Different"})
        reader.latest_response = reader.food_response.model_copy(update={"record": changed})
    elif mutation == "record_identity":
        changed = reader.food_response.record.model_copy(update={"source_id": "different"})
        reader.food_response = reader.food_response.model_copy(update={"record": changed})
    elif mutation == "provenance_release":
        wrong_manifest = reader.release.manifest.model_copy(
            update={"release_version": "9.9.9.9"}
        )
        reader.release = replace(reader.release, manifest=wrong_manifest)
    elif mutation in {"receipt_signature", "receipt_binding"}:
        key = reader.release.manifest.publication_receipt_key
        receipt_bytes = store.objects[key]
        if mutation == "receipt_signature":
            replacement = receipt_bytes[:-1] + bytes([receipt_bytes[-1] ^ 1])
        else:
            envelope = parse_signed_receipt(receipt_bytes)
            draft = PublicationReceiptDraft.model_validate(
                envelope.receipt.model_dump(
                    exclude={
                        "publisher_identity",
                        "publisher_adapter_identity",
                        "publisher_adapter_version",
                    }
                )
            ).model_copy(update={"pack_id": "wrong"})
            replacement = canonical_signed_receipt_bytes(RECEIPT_SIGNER.sign(draft))
        store.objects[key] = replacement
        values["expected_receipt_sha256"] = hashlib.sha256(replacement).hexdigest()
    elif mutation == "manifest_record":
        reader.release = replace(
            reader.release,
            manifest=reader.release.manifest.model_copy(update={"foods": ()}),
        )
    else:
        food = reader.release.manifest.foods[0]
        wrong_record = food.record.model_copy(update={"digest": "0" * 64})
        reader.release = replace(
            reader.release,
            manifest=reader.release.manifest.model_copy(
                update={"foods": (food.model_copy(update={"record": wrong_record}),)}
            ),
        )

    with pytest.raises(NaturalPublicVerificationError, match=code):
        await verify_natural_publication_artifacts(**values)  # type: ignore[arg-type]
