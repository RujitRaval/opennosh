from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from opennosh_api.federation.ingestion import (
    FederationArtifactError,
    VerifiedArtifactBundle,
    verify_artifact_bundle,
)
from opennosh_api.federation.models import FederationRelease
from opennosh_api.foodpacks.loader import prepare_food_pack
from opennosh_api.public.artifacts import (
    PublicPackArtifact,
    PublicReadReleaseManifest,
    artifact_descriptor,
)
from opennosh_api.public.bootstrap import _pack_archive
from opennosh_api.public.signing import public_key_text, sign_envelope
from opennosh_api.public_commons.manifests import ManifestKeyRing, canonical_json

ROOT = Path(__file__).resolve().parents[3]
PACK_ROOT = ROOT / "packs"
PACK_DIRECTORY = PACK_ROOT / "indian-staples-north"
PUBLISHED_AT = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
RELEASE_VERSION = "1.2.3.4"
RECEIPT_KEY = "receipts/v1/00000000-0000-0000-0000-000000000001.json"


def _release_bundle(pack_bytes: bytes) -> tuple[FederationRelease, bytes, ManifestKeyRing]:
    prepared = prepare_food_pack(PACK_DIRECTORY)
    assert prepared.pack_id is not None
    assert prepared.pack_version is not None
    descriptor = artifact_descriptor(
        f"packs/v1/{hashlib.sha256(pack_bytes).hexdigest()}.zip",
        pack_bytes,
        "application/zip",
    )
    manifest = PublicReadReleaseManifest(
        release_version=RELEASE_VERSION,
        published_at=PUBLISHED_AT,
        publication_receipt_key=RECEIPT_KEY,
        packs=(
            PublicPackArtifact(
                pack_id=prepared.pack_id,
                pack_version=prepared.pack_version,
                download=descriptor,
            ),
        ),
    )
    signing_key = Ed25519PrivateKey.generate()
    manifest_bytes = sign_envelope(
        manifest.model_dump(mode="json"),
        key_id="federation-test-v1",
        private_key=signing_key,
    )
    release = FederationRelease(
        id=uuid4(),
        maintainer_id=uuid4(),
        role_key_id=uuid4(),
        accepted_event_id=uuid4(),
        repository_id=1,
        repository="OpenNosh/opennosh",
        pack_id=prepared.pack_id,
        publication_id=UUID("00000000-0000-0000-0000-000000000001"),
        release_version=RELEASE_VERSION,
        statement_json={},
        statement_digest="1" * 64,
        manifest_digest=hashlib.sha256(manifest_bytes).hexdigest(),
        receipt_digest="2" * 64,
        public_url="https://opennosh.org/example",
        key_id="role-v1",
        signature="a" * 86,
        issued_at=PUBLISHED_AT,
        receipt_published_at=PUBLISHED_AT,
        verified_at=PUBLISHED_AT,
        created_at=PUBLISHED_AT,
    )
    keys = ManifestKeyRing.from_config(
        f"federation-test-v1:{public_key_text(signing_key)}"
    )
    return release, manifest_bytes, keys


def _verify(pack_bytes: bytes) -> VerifiedArtifactBundle:
    release, manifest_bytes, keys = _release_bundle(pack_bytes)
    return verify_artifact_bundle(
        release,
        manifest_bytes=manifest_bytes,
        pack_bytes=pack_bytes,
        manifest_keys=keys,
    )


def test_release_artifact_bundle_verifies_hash_schema_identity_and_license() -> None:
    pack_bytes = _pack_archive(PACK_ROOT, PACK_DIRECTORY)

    result = _verify(pack_bytes)

    prepared = prepare_food_pack(PACK_DIRECTORY)
    assert result.pack_version == prepared.pack_version
    assert result.pack_license == "CC0-1.0"
    assert len(result.records) == len(prepared.records)
    assert result.artifact_digest == hashlib.sha256(pack_bytes).hexdigest()
    assert result.record_set_digest == hashlib.sha256(
        canonical_json(result.records_json)
    ).hexdigest()


def test_release_artifact_bundle_rejects_manifest_digest_drift() -> None:
    pack_bytes = _pack_archive(PACK_ROOT, PACK_DIRECTORY)
    release, manifest_bytes, keys = _release_bundle(pack_bytes)
    release.manifest_digest = "0" * 64

    with pytest.raises(FederationArtifactError, match="release_manifest_digest_mismatch"):
        verify_artifact_bundle(
            release,
            manifest_bytes=manifest_bytes,
            pack_bytes=pack_bytes,
            manifest_keys=keys,
        )


def test_release_artifact_bundle_rejects_noncanonical_manifest() -> None:
    pack_bytes = _pack_archive(PACK_ROOT, PACK_DIRECTORY)
    release, manifest_bytes, keys = _release_bundle(pack_bytes)
    noncanonical = json.dumps(json.loads(manifest_bytes), indent=2).encode("utf-8")
    release.manifest_digest = hashlib.sha256(noncanonical).hexdigest()

    with pytest.raises(FederationArtifactError, match="release_manifest_not_canonical"):
        verify_artifact_bundle(
            release,
            manifest_bytes=noncanonical,
            pack_bytes=pack_bytes,
            manifest_keys=keys,
        )


def test_release_artifact_bundle_rejects_untrusted_manifest_signature() -> None:
    pack_bytes = _pack_archive(PACK_ROOT, PACK_DIRECTORY)
    release, manifest_bytes, _keys = _release_bundle(pack_bytes)
    untrusted_key = Ed25519PrivateKey.generate()
    untrusted_keys = ManifestKeyRing.from_config(
        f"federation-test-v1:{public_key_text(untrusted_key)}"
    )

    with pytest.raises(FederationArtifactError, match="release_manifest_signature_invalid"):
        verify_artifact_bundle(
            release,
            manifest_bytes=manifest_bytes,
            pack_bytes=pack_bytes,
            manifest_keys=untrusted_keys,
        )


def test_release_artifact_bundle_rejects_release_version_mismatch() -> None:
    pack_bytes = _pack_archive(PACK_ROOT, PACK_DIRECTORY)
    release, manifest_bytes, keys = _release_bundle(pack_bytes)
    release.release_version = "1.2.3.5"

    with pytest.raises(FederationArtifactError, match="release_manifest_version_mismatch"):
        verify_artifact_bundle(
            release,
            manifest_bytes=manifest_bytes,
            pack_bytes=pack_bytes,
            manifest_keys=keys,
        )


def test_release_artifact_bundle_rejects_missing_pack_descriptor() -> None:
    pack_bytes = _pack_archive(PACK_ROOT, PACK_DIRECTORY)
    release, manifest_bytes, keys = _release_bundle(pack_bytes)
    release.pack_id = "different-pack"

    with pytest.raises(FederationArtifactError, match="release_pack_descriptor_missing"):
        verify_artifact_bundle(
            release,
            manifest_bytes=manifest_bytes,
            pack_bytes=pack_bytes,
            manifest_keys=keys,
        )


def test_release_artifact_bundle_rejects_pack_content_drift() -> None:
    pack_bytes = _pack_archive(PACK_ROOT, PACK_DIRECTORY)
    release, manifest_bytes, keys = _release_bundle(pack_bytes)

    with pytest.raises(FederationArtifactError, match="release_pack_artifact_mismatch"):
        verify_artifact_bundle(
            release,
            manifest_bytes=manifest_bytes,
            pack_bytes=pack_bytes + b"drift",
            manifest_keys=keys,
        )


def test_release_artifact_bundle_rejects_traversal_archive() -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("pack.yaml", "pack: {}")
        archive.writestr("foods/food.yaml", "[]")
        archive.writestr("../escape", "blocked")

    with pytest.raises(FederationArtifactError, match="release_pack_archive_invalid"):
        _verify(output.getvalue())


def test_release_artifact_bundle_rejects_invalid_archive() -> None:
    with pytest.raises(FederationArtifactError, match="release_pack_archive_invalid"):
        _verify(b"not-a-zip")


def test_release_artifact_bundle_rejects_incomplete_archive() -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("pack.yaml", "pack: {}")

    with pytest.raises(FederationArtifactError, match="release_pack_archive_incomplete"):
        _verify(output.getvalue())


def test_release_artifact_bundle_rejects_invalid_pack_schema() -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("pack.yaml", "not: a-valid-pack")
        archive.writestr("foods/food.yaml", "not: a-valid-food")

    with pytest.raises(FederationArtifactError, match="release_pack_schema_invalid"):
        _verify(output.getvalue())
