from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

from opennosh_api.foods.schemas import FoodSource
from opennosh_api.public.artifacts import (
    ArtifactStore,
    PublicFoodRecordResponse,
    ResolvedRelease,
)
from opennosh_api.public_commons.manifests import canonical_json
from opennosh_api.publication.receipts import (
    PublicationReceiptKeyRing,
    parse_signed_receipt,
)

_MAX_RECEIPT_BYTES = 256 * 1024


class NaturalPublicVerificationError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class NaturalPublicReader(Protocol):
    async def food(
        self,
        source: FoodSource,
        source_id: str,
        *,
        release_version: str | None = None,
    ) -> PublicFoodRecordResponse: ...

    async def provenance(
        self,
        source: FoodSource,
        source_id: str,
        *,
        release_version: str,
    ) -> tuple[bytes, ResolvedRelease]: ...

    async def signed_manifest(self, release_version: str) -> tuple[bytes, ResolvedRelease]: ...


@dataclass(frozen=True, slots=True)
class NaturalPublicVerification:
    release_version: str
    manifest_sha256: str
    receipt_sha256: str
    record_sha256: str
    provenance_sha256: str

    def to_dict(self) -> dict[str, str]:
        return {
            "release_version": self.release_version,
            "manifest_sha256": self.manifest_sha256,
            "receipt_sha256": self.receipt_sha256,
            "record_sha256": self.record_sha256,
            "provenance_sha256": self.provenance_sha256,
        }


async def verify_natural_publication_artifacts(
    *,
    reader: NaturalPublicReader,
    store: ArtifactStore,
    receipt_keys: PublicationReceiptKeyRing,
    pack_id: str,
    record_id: str,
    expected_release_version: str,
    expected_manifest_sha256: str,
    expected_receipt_sha256: str,
) -> NaturalPublicVerification:
    """Verify one latest and immutable record against signed release artifacts."""

    try:
        exact = await reader.food(
            FoodSource.COMMUNITY,
            record_id,
            release_version=expected_release_version,
        )
        latest = await reader.food(FoodSource.COMMUNITY, record_id)
        provenance, provenance_release = await reader.provenance(
            FoodSource.COMMUNITY,
            record_id,
            release_version=expected_release_version,
        )
        manifest_bytes, release = await reader.signed_manifest(expected_release_version)
    except Exception as error:
        raise NaturalPublicVerificationError("public_artifact_read_failed") from error

    if exact.release.release_version != expected_release_version:
        raise NaturalPublicVerificationError("immutable_release_version_mismatch")
    if latest.release.release_version != expected_release_version:
        raise NaturalPublicVerificationError("latest_release_version_mismatch")
    if latest.release.state != "verified" or exact.release.state != "verified":
        raise NaturalPublicVerificationError("public_release_not_freshly_verified")
    if canonical_json(exact.model_dump(mode="json")) != canonical_json(
        latest.model_dump(mode="json")
    ):
        raise NaturalPublicVerificationError("latest_immutable_record_mismatch")
    if exact.record.source is not FoodSource.COMMUNITY or exact.record.source_id != record_id:
        raise NaturalPublicVerificationError("public_record_identity_mismatch")
    if exact.record.attribution.pack_id != pack_id:
        raise NaturalPublicVerificationError("public_record_pack_mismatch")
    if provenance_release.manifest.release_version != expected_release_version:
        raise NaturalPublicVerificationError("provenance_release_mismatch")

    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    if manifest_sha256 != expected_manifest_sha256:
        raise NaturalPublicVerificationError("public_manifest_digest_mismatch")
    receipt_bytes = await store.read(
        release.manifest.publication_receipt_key,
        max_bytes=_MAX_RECEIPT_BYTES,
    )
    if receipt_bytes is None:
        raise NaturalPublicVerificationError("public_receipt_missing")
    receipt_sha256 = hashlib.sha256(receipt_bytes).hexdigest()
    if receipt_sha256 != expected_receipt_sha256:
        raise NaturalPublicVerificationError("public_receipt_digest_mismatch")
    try:
        receipt = parse_signed_receipt(receipt_bytes)
        receipt_keys.verify(receipt)
    except Exception as error:
        raise NaturalPublicVerificationError("public_receipt_signature_invalid") from error
    bound = receipt.receipt
    if (
        bound.pack_id != pack_id
        or bound.record_id != record_id
        or bound.release_version != expected_release_version
        or bound.signed_release_metadata_digest != manifest_sha256
    ):
        raise NaturalPublicVerificationError("public_receipt_binding_mismatch")

    record_sha256 = hashlib.sha256(canonical_json(exact.record.model_dump(mode="json"))).hexdigest()
    provenance_sha256 = hashlib.sha256(provenance).hexdigest()
    food = next(
        (
            item
            for item in release.manifest.foods
            if item.source is FoodSource.COMMUNITY and item.source_id == record_id
        ),
        None,
    )
    if food is None:
        raise NaturalPublicVerificationError("public_manifest_record_missing")
    if food.record.digest != record_sha256 or food.provenance.digest != provenance_sha256:
        raise NaturalPublicVerificationError("public_artifact_digest_mismatch")
    return NaturalPublicVerification(
        release_version=expected_release_version,
        manifest_sha256=manifest_sha256,
        receipt_sha256=receipt_sha256,
        record_sha256=record_sha256,
        provenance_sha256=provenance_sha256,
    )


__all__ = [
    "NaturalPublicVerification",
    "NaturalPublicVerificationError",
    "verify_natural_publication_artifacts",
]
