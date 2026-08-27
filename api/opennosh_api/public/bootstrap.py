"""Offline construction and verification of the first production Commons release."""

from __future__ import annotations

import hashlib
import hmac
import html
import io
import json
import os
import re
import stat
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from opennosh_api.evidence.contracts import (
    EvidenceAcknowledgement,
    EvidenceAcknowledgementKind,
    EvidenceClass,
)
from opennosh_api.foodpacks.loader import CommunityFoodRecord, prepare_food_pack
from opennosh_api.foodpacks.validation import discover_pack_directories
from opennosh_api.foods.schemas import FoodAttribution, FoodDetail, FoodSource
from opennosh_api.nutrition import HouseholdPortion
from opennosh_api.public.artifacts import (
    ArtifactDescriptor,
    LocalArtifactStore,
    PublicArtifactReadService,
    PublicFoodArtifact,
    PublicPackArtifact,
    PublicReadLatestPointer,
    PublicReadReleaseManifest,
    artifact_descriptor,
)
from opennosh_api.public.signing import (
    load_production_signing_key,
    public_key_text,
    sign_envelope,
)
from opennosh_api.public_commons.manifests import ManifestKeyRing, canonical_json
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

_COMMIT = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_RELEASE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$")
_FORGE_TARGET = "https://github.com/RujitRaval/opennosh"
_ADAPTER_IDENTITY = "opennosh.bootstrap-release"
_ADAPTER_VERSION = "1"
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


class StarterReleaseObject(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    object_key: str
    digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    size_bytes: Annotated[int, Field(ge=1)]
    media_type: str
    mutable_pointer: bool = False


class StarterReleaseInventory(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1"] = "1"
    release_version: Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$")]
    published_at: datetime
    source_commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")]
    source_inventory_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    manifest_key_id: str
    manifest_verifying_key: str
    receipt_key_id: str
    receipt_verifying_key: str
    food_count: Annotated[int, Field(ge=1)]
    pack_count: Annotated[int, Field(ge=1)]
    total_bytes: Annotated[int, Field(ge=1)]
    objects: tuple[StarterReleaseObject, ...]

    @field_validator("published_at")
    @classmethod
    def require_aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("published_at must include a timezone")
        return value.astimezone(UTC)


def build_starter_release(
    *,
    packs_root: Path,
    output_directory: Path,
    release_version: str,
    published_at: datetime,
    source_commit: str,
    manifest_key_id: str,
    manifest_private_key_path: Path,
    receipt_key_id: str,
    receipt_private_key_path: Path,
    decision_reference: str,
    approving_actor: str,
) -> StarterReleaseInventory:
    """Build a deterministic, receipt-bound public release without exposing private keys."""

    if not _RELEASE.fullmatch(release_version):
        raise ValueError("Release version must contain four numeric parts")
    if not _COMMIT.fullmatch(source_commit):
        raise ValueError("Source commit must be a full lowercase Git commit")
    published_at = _aware_utc(published_at)
    if not decision_reference.startswith("https://github.com/RujitRaval/opennosh/"):
        raise ValueError("Decision reference must be an OpenNosh GitHub URL")
    if not approving_actor.startswith("github:"):
        raise ValueError("Approving actor must use the github:<login> form")

    repository_root = packs_root.resolve(strict=True).parent
    for key_path in (manifest_private_key_path, receipt_private_key_path):
        if key_path.resolve(strict=True).is_relative_to(repository_root):
            raise ValueError("Private signing keys must be stored outside the repository")

    manifest_key = load_production_signing_key(
        SecretStr(_read_production_key(manifest_private_key_path)),
        key_id=manifest_key_id,
    )
    receipt_private_key = load_production_signing_key(
        SecretStr(_read_production_key(receipt_private_key_path)),
        key_id=receipt_key_id,
    )
    if public_key_text(manifest_key) == public_key_text(receipt_private_key):
        raise ValueError("Manifest and receipt signing keys must be independent")

    root = packs_root.resolve(strict=True)
    pack_directories = discover_pack_directories(root)
    if not pack_directories:
        raise ValueError("Starter release requires at least one food pack")
    prepared_packs = []
    source_files: list[dict[str, object]] = []
    for directory in pack_directories:
        prepared = prepare_food_pack(directory)
        if prepared.pack_rejected or prepared.errors or not prepared.records:
            error_detail = "; ".join(issue.message for issue in tuple(prepared.errors)[:5])
            raise ValueError(f"Food pack {directory.name} is not releaseable: {error_detail}")
        if prepared.pack_id is None or prepared.pack_version is None:
            raise ValueError(f"Food pack {directory.name} has no release identity")
        prepared_packs.append((directory, prepared))
        for path in _pack_source_files(root, directory):
            payload = path.read_bytes()
            source_files.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "digest": hashlib.sha256(payload).hexdigest(),
                    "size_bytes": len(payload),
                }
            )

    slugs = [record.slug for _, pack in prepared_packs for record in pack.records]
    if len(slugs) != len(set(slugs)):
        raise ValueError("Starter release food slugs must be globally unique")
    ordered_source_files = sorted(source_files, key=lambda source_file: str(source_file["path"]))
    source_inventory = canonical_json(
        {
            "schema_version": "1",
            "source_commit": source_commit,
            "files": ordered_source_files,
        }
    )
    source_inventory_digest = hashlib.sha256(source_inventory).hexdigest()
    publication_id = uuid5(
        NAMESPACE_URL,
        f"opennosh:starter-release:{release_version}:{source_commit}",
    )
    receipt_object = receipt_object_key(publication_id)

    immutable_objects: dict[str, bytes] = {}
    foods: list[PublicFoodArtifact] = []
    packs: list[PublicPackArtifact] = []
    for directory, prepared in prepared_packs:
        assert prepared.pack_id is not None
        assert prepared.pack_version is not None
        pack_bytes = _pack_archive(root, directory)
        pack_descriptor = _content_addressed_descriptor(
            "packs/v1",
            pack_bytes,
            "application/zip",
            suffix=".zip",
        )
        immutable_objects[pack_descriptor.object_key] = pack_bytes
        packs.append(
            PublicPackArtifact(
                pack_id=prepared.pack_id,
                pack_version=prepared.pack_version,
                download=pack_descriptor,
            )
        )
        for record in prepared.records:
            detail = _food_detail(record)
            record_bytes = canonical_json(detail.model_dump(mode="json"))
            provenance_bytes = _provenance_html(record)
            record_descriptor = _content_addressed_descriptor(
                "records/v1",
                record_bytes,
                "application/json",
                suffix=".json",
            )
            provenance_descriptor = _content_addressed_descriptor(
                "provenance/v1",
                provenance_bytes,
                "text/html",
                suffix=".html",
            )
            immutable_objects[record_descriptor.object_key] = record_bytes
            immutable_objects[provenance_descriptor.object_key] = provenance_bytes
            foods.append(
                PublicFoodArtifact(
                    source=FoodSource.COMMUNITY,
                    source_id=record.slug,
                    record=record_descriptor,
                    provenance=provenance_descriptor,
                )
            )

    manifest = PublicReadReleaseManifest(
        release_version=release_version,
        published_at=published_at,
        publication_receipt_key=receipt_object,
        foods=tuple(sorted(foods, key=lambda item: (item.source.value, item.source_id))),
        packs=tuple(sorted(packs, key=lambda item: (item.pack_id, item.pack_version))),
    )
    manifest_bytes = sign_envelope(
        manifest.model_dump(mode="json"),
        key_id=manifest_key_id,
        private_key=manifest_key,
    )
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    receipt = _bootstrap_receipt(
        release_version=release_version,
        published_at=published_at,
        source_commit=source_commit,
        source_inventory_digest=source_inventory_digest,
        manifest_digest=manifest_digest,
        publication_id=publication_id,
        decision_reference=decision_reference,
        approving_actor=approving_actor,
        receipt_key_id=receipt_key_id,
        receipt_private_key=receipt_private_key,
    )
    receipt_bytes = canonical_signed_receipt_bytes(receipt)
    manifest_descriptor = artifact_descriptor(
        f"releases/v1/release-{release_version}.json",
        manifest_bytes,
        "application/vnd.opennosh.release+json",
    )
    pointer = PublicReadLatestPointer(
        release_version=release_version,
        manifest=manifest_descriptor,
        issued_at=published_at,
        expires_at=published_at + timedelta(hours=23),
    )
    pointer_bytes = sign_envelope(
        pointer.model_dump(mode="json"),
        key_id=manifest_key_id,
        private_key=manifest_key,
    )

    target = output_directory.resolve(strict=False)
    if target.is_relative_to(repository_root):
        raise ValueError("Production release output must be stored outside the repository")
    if target.exists():
        raise FileExistsError("Release output directory already exists")
    target.mkdir(parents=True, mode=0o755)
    for object_key, payload in immutable_objects.items():
        _write_public_object(target, object_key, payload)
    _write_public_object(target, receipt_object, receipt_bytes)
    _write_public_object(target, manifest_descriptor.object_key, manifest_bytes)
    _write_public_object(target, "latest/v1.json", pointer_bytes)

    object_inventory = [
        _inventory_object(key, payload, _media_type_for(key))
        for key, payload in immutable_objects.items()
    ]
    object_inventory.extend(
        (
            _inventory_object(
                receipt_object,
                receipt_bytes,
                "application/vnd.opennosh.receipt+json",
            ),
            _inventory_object(
                manifest_descriptor.object_key,
                manifest_bytes,
                "application/vnd.opennosh.release+json",
            ),
            _inventory_object(
                "latest/v1.json",
                pointer_bytes,
                "application/vnd.opennosh.latest+json",
                mutable_pointer=True,
            ),
        )
    )
    inventory = StarterReleaseInventory(
        release_version=release_version,
        published_at=published_at,
        source_commit=source_commit,
        source_inventory_digest=source_inventory_digest,
        manifest_key_id=manifest_key_id,
        manifest_verifying_key=public_key_text(manifest_key),
        receipt_key_id=receipt_key_id,
        receipt_verifying_key=public_key_text(receipt_private_key),
        food_count=len(foods),
        pack_count=len(packs),
        total_bytes=sum(item.size_bytes for item in object_inventory),
        objects=tuple(
            sorted(object_inventory, key=lambda item: (item.mutable_pointer, item.object_key))
        ),
    )
    _write_public_object(
        target,
        "inventory.json",
        canonical_json(inventory.model_dump(mode="json")),
    )
    return inventory


async def verify_starter_release(
    directory: Path,
    inventory: StarterReleaseInventory,
) -> None:
    """Verify every release object with the same trust code used by production."""

    store = LocalArtifactStore(directory)
    service = PublicArtifactReadService(
        store=store,
        manifest_keys=ManifestKeyRing.from_config(
            f"{inventory.manifest_key_id}:{inventory.manifest_verifying_key}"
        ),
        receipt_keys=PublicationReceiptKeyRing.from_json(
            json.dumps({inventory.receipt_key_id: inventory.receipt_verifying_key})
        ),
        checkpoint_path=directory / ".verification-state" / "checkpoint.json",
    )
    try:
        release = await service.resolve_release(release_version=None, now=inventory.published_at)
        if release.manifest.release_version != inventory.release_version:
            raise ValueError("Starter release version does not match its inventory")
        for food_artifact in release.manifest.foods:
            await service.food(
                food_artifact.source,
                food_artifact.source_id,
                release_version=inventory.release_version,
            )
            await service.provenance(
                food_artifact.source,
                food_artifact.source_id,
                release_version=inventory.release_version,
            )
        for pack_artifact in release.manifest.packs:
            await service.pack(
                pack_artifact.pack_id,
                pack_artifact.pack_version,
                release_version=inventory.release_version,
            )
        await service.signed_manifest(inventory.release_version)
    finally:
        await service.aclose()

    for object_entry in inventory.objects:
        payload = (directory / object_entry.object_key).read_bytes()
        if hashlib.sha256(payload).hexdigest() != object_entry.digest:
            raise ValueError(f"Inventory digest mismatch for {object_entry.object_key}")
        if len(payload) != object_entry.size_bytes:
            raise ValueError(f"Inventory size mismatch for {object_entry.object_key}")


def inventory_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_inventory(path: Path) -> StarterReleaseInventory:
    return StarterReleaseInventory.model_validate_json(path.read_bytes())


def load_verified_inventory(
    path: Path,
    *,
    expected_sha256: str,
) -> StarterReleaseInventory:
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError("Expected inventory SHA-256 must be 64 lowercase hexadecimal characters")
    payload = path.read_bytes()
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if not hmac.compare_digest(actual_sha256, expected_sha256):
        raise ValueError("Release inventory SHA-256 does not match the operator trust anchor")
    return StarterReleaseInventory.model_validate_json(payload)


def _food_detail(record: CommunityFoodRecord) -> FoodDetail:
    return FoodDetail(
        id=f"community:{record.slug}",
        source=FoodSource.COMMUNITY,
        source_id=record.slug,
        name=record.name,
        name_local=record.name_local,
        category=record.category,
        attribution=FoodAttribution(
            source=FoodSource.COMMUNITY,
            license=record.pack_license,
            source_uri=record.source_uri,
            source_license=record.source_license,
            contributed_by=record.contributed_by,
            pack_id=record.pack_id,
            pack_version=record.pack_version,
            provenance=record.provenance,
        ),
        nutrients=record.nutrients_json,
        portions=[HouseholdPortion.model_validate(portion) for portion in record.portions_json],
    )


def _provenance_html(record: CommunityFoodRecord) -> bytes:
    source = (
        f'<p><a href="{html.escape(record.source_uri, quote=True)}">Open source reference</a></p>'
        if record.source_uri is not None
        else "<p>No external source URI is attached to this contributor-authored record.</p>"
    )
    note = (
        f"<h2>Source note</h2><p>{html.escape(record.source_note)}</p>"
        if record.source_note is not None
        else ""
    )
    document = (
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        f"<title>{html.escape(record.name)} provenance</title>"
        f"<h1>{html.escape(record.name)}</h1>"
        f"<p>Published from {html.escape(record.pack_id)} {html.escape(record.pack_version)}.</p>"
        f"<p>{html.escape(record.provenance)}</p>{source}{note}"
        f"<p>Data license: {html.escape(record.pack_license)}. "
        f"Source license: {html.escape(record.source_license)}.</p></html>"
    )
    return document.encode()


def _pack_source_files(root: Path, directory: Path) -> tuple[Path, ...]:
    candidates = [path for path in directory.rglob("*") if path.is_file()]
    for shared in ("CC0-1.0.txt", "LICENSE.md"):
        path = root / shared
        if path.is_file():
            candidates.append(path)
    if any(path.is_symlink() for path in candidates):
        raise ValueError("Starter release pack inputs cannot contain symbolic links")
    return tuple(sorted(set(candidates)))


def _pack_archive(root: Path, directory: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(
        buffer,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        strict_timestamps=True,
    ) as archive:
        for path in _pack_source_files(root, directory):
            if path.is_relative_to(directory):
                archive_name = path.relative_to(directory).as_posix()
            else:
                archive_name = path.name
            info = zipfile.ZipInfo(archive_name, date_time=_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(
                info,
                path.read_bytes(),
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            )
    return buffer.getvalue()


def _content_addressed_descriptor(
    prefix: str,
    payload: bytes,
    media_type: str,
    *,
    suffix: str,
) -> ArtifactDescriptor:
    digest = hashlib.sha256(payload).hexdigest()
    return artifact_descriptor(f"{prefix}/{digest}{suffix}", payload, media_type)


def _bootstrap_receipt(
    *,
    release_version: str,
    published_at: datetime,
    source_commit: str,
    source_inventory_digest: str,
    manifest_digest: str,
    publication_id: UUID,
    decision_reference: str,
    approving_actor: str,
    receipt_key_id: str,
    receipt_private_key: Ed25519PrivateKey,
) -> SignedPublicationReceipt:
    registry_digest = hashlib.sha256(
        canonical_json(
            {
                "release_version": release_version,
                "source_commit": source_commit,
                "manifest_digest": manifest_digest,
            }
        )
    ).hexdigest()
    digest_by_step = {
        PublicationStepName.COMMIT_RECORD: source_inventory_digest,
        PublicationStepName.COPY_COMMIT: source_inventory_digest,
        PublicationStepName.COPY_EVIDENCE: source_inventory_digest,
        PublicationStepName.SIGN_RELEASE: manifest_digest,
        PublicationStepName.PUBLISH_RELEASE: manifest_digest,
        PublicationStepName.COPY_RELEASE: manifest_digest,
        PublicationStepName.CONFIRM_REGISTRY: registry_digest,
    }
    definitions = publication_protocol(_FORGE_TARGET)[:7]
    proofs = tuple(
        ReceiptStepProof(
            step=definition.name,
            destination=definition.destination,
            content_digest=digest_by_step[definition.name],
            external_reference=(
                source_commit
                if definition.name is PublicationStepName.COMMIT_RECORD
                else decision_reference
            ),
            verified_at=published_at,
            adapter_identity=_ADAPTER_IDENTITY,
            adapter_version=_ADAPTER_VERSION,
        )
        for definition in definitions
    )
    evidence_id = uuid5(NAMESPACE_URL, f"{decision_reference}:starter-source")
    evidence = EvidenceAcknowledgement(
        evidence_id=evidence_id,
        evidence_class=EvidenceClass.VERSIONED_PUBLIC_DATASET,
        manifest_digest=source_inventory_digest,
        kind=EvidenceAcknowledgementKind.DATASET_SNAPSHOT,
        destination="urn:opennosh:durability:git",
        content_digest=source_inventory_digest,
        external_reference=f"{_FORGE_TARGET}/commit/{source_commit}",
        verified_at=published_at,
        adapter_identity=_ADAPTER_IDENTITY,
        adapter_version=_ADAPTER_VERSION,
    )
    draft = PublicationReceiptDraft(
        publication_id=publication_id,
        pack_id="opennosh-starter-commons",
        record_id=f"release-{release_version}",
        reviewed_decision_id=uuid5(NAMESPACE_URL, decision_reference),
        approving_actor_id=uuid5(NAMESPACE_URL, approving_actor),
        approving_actor_scope="repository:opennosh:owner",
        approved_payload_digest=source_inventory_digest,
        expected_base_commit=source_commit,
        merged_commit=source_commit,
        merged_tree_digest=source_inventory_digest,
        evidence_manifest_digests=(source_inventory_digest,),
        evidence_acknowledgements=(evidence,),
        signed_release_metadata_digest=manifest_digest,
        release_version=release_version,
        registry_acknowledgement_digest=registry_digest,
        registry_result="bootstrap-approved",
        artifact_snapshot_digests=tuple(sorted({source_inventory_digest, manifest_digest})),
        verified_steps=proofs,
        published_at=published_at,
        idempotency_key_hash=hashlib.sha256(
            f"opennosh:{release_version}:{source_commit}".encode()
        ).hexdigest(),
    )
    return Ed25519ReceiptSigner(
        key_id=receipt_key_id,
        publisher_identity="opennosh:bootstrap-operator",
        private_key=receipt_private_key,
        adapter_identity=_ADAPTER_IDENTITY,
    ).sign(draft)


def _read_production_key(path: Path) -> str:
    if path.is_symlink():
        raise ValueError("Private signing key path cannot be a symbolic link")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise PermissionError("Private signing key must not be accessible by group or others")
    return path.read_text(encoding="ascii").strip()


def _inventory_object(
    object_key: str,
    payload: bytes,
    media_type: str,
    *,
    mutable_pointer: bool = False,
) -> StarterReleaseObject:
    return StarterReleaseObject(
        object_key=object_key,
        digest=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        media_type=media_type,
        mutable_pointer=mutable_pointer,
    )


def _media_type_for(object_key: str) -> str:
    if object_key.endswith(".json"):
        return "application/json"
    if object_key.endswith(".html"):
        return "text/html"
    if object_key.endswith(".zip"):
        return "application/zip"
    raise ValueError(f"Unknown artifact media type for {object_key}")


def _write_public_object(root: Path, object_key: str, payload: bytes) -> None:
    path = root / object_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    os.chmod(path, 0o644)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Publication time must include a timezone")
    return value.astimezone(UTC).replace(microsecond=0)
