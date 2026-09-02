"""Fail-closed verification of one federation release artifact bundle."""

from __future__ import annotations

import hashlib
import io
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from opennosh_api.federation.models import FederationRelease
from opennosh_api.foodpacks.loader import CommunityFoodRecord, prepare_food_pack
from opennosh_api.foodpacks.validation import MAX_PACK_BYTES, MAX_REPOSITORY_ENTRIES
from opennosh_api.public.artifacts import MAX_MANIFEST_BYTES, PublicReadReleaseManifest
from opennosh_api.public.artifacts import MAX_PACK_BYTES as MAX_ARCHIVE_BYTES
from opennosh_api.public_commons.manifests import (
    ManifestKeyRing,
    ManifestVerificationError,
    SignedEnvelope,
    canonical_json,
)


class FederationArtifactError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class VerifiedArtifactBundle:
    manifest_key_id: str
    manifest_digest: str
    artifact_object_key: str
    artifact_digest: str
    artifact_size_bytes: int
    pack_version: str
    pack_license: str
    records: tuple[CommunityFoodRecord, ...]
    records_json: tuple[dict[str, object], ...]
    record_set_digest: str


def verify_artifact_bundle(
    release: FederationRelease,
    *,
    manifest_bytes: bytes,
    pack_bytes: bytes,
    manifest_keys: ManifestKeyRing,
) -> VerifiedArtifactBundle:
    """Verify signed metadata, content hashes, schema, identity, and license."""

    if not manifest_bytes or len(manifest_bytes) > MAX_MANIFEST_BYTES:
        raise FederationArtifactError("release_manifest_size_invalid")
    if hashlib.sha256(manifest_bytes).hexdigest() != release.manifest_digest:
        raise FederationArtifactError("release_manifest_digest_mismatch")
    try:
        envelope = SignedEnvelope.model_validate_json(manifest_bytes)
        if canonical_json(envelope.model_dump(mode="json")) != manifest_bytes:
            raise FederationArtifactError("release_manifest_not_canonical")
        manifest_keys.verify(envelope)
        manifest = PublicReadReleaseManifest.model_validate(envelope.payload)
    except FederationArtifactError:
        raise
    except ManifestVerificationError as error:
        raise FederationArtifactError("release_manifest_signature_invalid") from error
    except (ValidationError, ValueError) as error:
        raise FederationArtifactError("release_manifest_invalid") from error
    if manifest.release_version != release.release_version:
        raise FederationArtifactError("release_manifest_version_mismatch")

    candidates = tuple(pack for pack in manifest.packs if pack.pack_id == release.pack_id)
    if len(candidates) != 1:
        raise FederationArtifactError("release_pack_descriptor_missing")
    descriptor = candidates[0].download
    if (
        not pack_bytes
        or len(pack_bytes) > MAX_ARCHIVE_BYTES
        or len(pack_bytes) != descriptor.size_bytes
        or hashlib.sha256(pack_bytes).hexdigest() != descriptor.digest
    ):
        raise FederationArtifactError("release_pack_artifact_mismatch")

    records = _prepare_pack_archive(pack_bytes)
    if not records:
        raise FederationArtifactError("release_pack_empty")
    if records[0].pack_id != release.pack_id:
        raise FederationArtifactError("release_pack_identity_mismatch")
    if records[0].pack_version != candidates[0].pack_version:
        raise FederationArtifactError("release_pack_version_mismatch")
    if any(record.pack_id != release.pack_id for record in records):
        raise FederationArtifactError("release_pack_identity_mismatch")
    if any(record.pack_license != "CC0-1.0" for record in records):
        raise FederationArtifactError("release_pack_license_invalid")

    records_json = tuple(
        record.database_values() for record in sorted(records, key=lambda item: item.slug)
    )
    record_set_digest = hashlib.sha256(canonical_json(records_json)).hexdigest()
    return VerifiedArtifactBundle(
        manifest_key_id=envelope.key_id,
        manifest_digest=release.manifest_digest,
        artifact_object_key=descriptor.object_key,
        artifact_digest=descriptor.digest,
        artifact_size_bytes=descriptor.size_bytes,
        pack_version=candidates[0].pack_version,
        pack_license="CC0-1.0",
        records=records,
        records_json=records_json,
        record_set_digest=record_set_digest,
    )


def _prepare_pack_archive(payload: bytes) -> tuple[CommunityFoodRecord, ...]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except (OSError, zipfile.BadZipFile) as error:
        raise FederationArtifactError("release_pack_archive_invalid") from error
    with archive:
        infos = archive.infolist()
        if not infos or len(infos) > MAX_REPOSITORY_ENTRIES:
            raise FederationArtifactError("release_pack_archive_invalid")
        names: set[str] = set()
        total_bytes = 0
        for info in infos:
            name = info.filename
            path = PurePosixPath(name)
            mode = info.external_attr >> 16
            if (
                not name
                or name in names
                or "\\" in name
                or path.is_absolute()
                or ".." in path.parts
                or info.is_dir()
                or info.flag_bits & 0x1
                or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                or stat.S_ISLNK(mode)
                or not _allowed_archive_path(path)
            ):
                raise FederationArtifactError("release_pack_archive_invalid")
            names.add(name)
            total_bytes += info.file_size
            if total_bytes > MAX_PACK_BYTES:
                raise FederationArtifactError("release_pack_archive_too_large")
        if "pack.yaml" not in names or not any(
            PurePosixPath(name).parent == PurePosixPath("foods")
            and PurePosixPath(name).suffix in {".yaml", ".yml"}
            for name in names
        ):
            raise FederationArtifactError("release_pack_archive_incomplete")

        with tempfile.TemporaryDirectory(prefix="opennosh-federation-pack-") as temporary:
            root = Path(temporary)
            for info in infos:
                destination = root.joinpath(*PurePosixPath(info.filename).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                try:
                    body = archive.read(info)
                except (OSError, RuntimeError, zipfile.BadZipFile) as error:
                    raise FederationArtifactError("release_pack_archive_invalid") from error
                if len(body) != info.file_size:
                    raise FederationArtifactError("release_pack_archive_invalid")
                destination.write_bytes(body)
            try:
                prepared = prepare_food_pack(root)
            except (OSError, ValueError) as error:
                raise FederationArtifactError("release_pack_schema_invalid") from error
    if prepared.pack_rejected or prepared.errors:
        raise FederationArtifactError("release_pack_schema_invalid")
    if prepared.pack_id is None or prepared.pack_version is None:
        raise FederationArtifactError("release_pack_identity_missing")
    return prepared.records


def _allowed_archive_path(path: PurePosixPath) -> bool:
    if len(path.parts) == 1:
        return path.name in {
            "pack.yaml",
            "README.md",
            "CC0-1.0.txt",
            "LICENSE.md",
        }
    return (
        len(path.parts) == 2
        and path.parts[0] == "foods"
        and path.suffix in {".yaml", ".yml"}
    )
