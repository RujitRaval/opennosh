"""Canonical production material sources for governed Commons publication."""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path, PurePosixPath
from uuid import UUID

from opennosh_api.foodpacks.loader import prepare_food_pack
from opennosh_api.foodpacks.validation import FoodPackLoadError
from opennosh_api.foods.schemas import FoodSource
from opennosh_api.governance.gate import GovernanceGate
from opennosh_api.governance.policy import GovernanceBinding
from opennosh_api.public.artifacts import (
    PublicArtifactReadService,
    PublicFoodArtifact,
    PublicPackArtifact,
    PublicReadReleaseManifest,
)
from opennosh_api.public.bootstrap import (
    _content_addressed_descriptor,
    _food_detail,
    _pack_archive,
    _provenance_html,
)
from opennosh_api.public.r2 import R2PublicationError, S3R2ObjectWriter
from opennosh_api.public_commons.manifests import (
    ManifestKeyRing,
    ManifestVerificationError,
    SignedEnvelope,
    canonical_json,
)
from opennosh_api.publication.forge.contracts import (
    ForgeMaterialClient,
    ForgeMutation,
    ForgeObservation,
    ForgePullRequestState,
    MergedPackMaterial,
)
from opennosh_api.publication.object_adapters import (
    PublicationObject,
    PublicationObjectSet,
)
from opennosh_api.publication.receipts import receipt_object_key
from opennosh_api.publication.state import (
    EffectIntent,
    PublicationStepName,
    effect_idempotency_key,
)


@dataclass(frozen=True, slots=True)
class CanonicalReleaseMaterial:
    manifest: PublicReadReleaseManifest
    objects: tuple[PublicationObject, ...]


@dataclass(frozen=True, slots=True)
class CanonicalMergedProof:
    binding: GovernanceBinding
    observation: ForgeObservation
    pack: MergedPackMaterial


class CanonicalPublicationMaterialAuthority:
    """Re-read the governed merge and verified Commons release before materializing."""

    identity = "opennosh.canonical-production-material-authority"
    version = "1.0"

    def __init__(
        self,
        *,
        governance_gate: GovernanceGate,
        forge: ForgeMaterialClient,
        current_release: PublicArtifactReadService,
        writer: S3R2ObjectWriter,
        bucket: str,
        manifest_keys: ManifestKeyRing,
    ) -> None:
        self._governance_gate = governance_gate
        self._forge = forge
        self._current_release = current_release
        self._writer = writer
        self._bucket = bucket
        self._manifest_keys = manifest_keys
        self._proofs: dict[UUID, CanonicalMergedProof] = {}
        self._releases: dict[UUID, CanonicalReleaseMaterial] = {}
        self._lock = asyncio.Lock()

    async def merged_proof(self, intent: EffectIntent) -> CanonicalMergedProof:
        cached = self._proofs.get(intent.publication_id)
        if cached is not None:
            self._validate_binding(intent, cached.binding)
            return cached
        async with self._lock:
            cached = self._proofs.get(intent.publication_id)
            if cached is not None:
                self._validate_binding(intent, cached.binding)
                return cached
            binding = await self._governance_gate.binding_for(intent.publication_id)
            self._validate_binding(intent, binding)
            mutation = ForgeMutation(
                binding=binding,
                idempotency_key=effect_idempotency_key(
                    publication_id=intent.publication_id,
                    workflow_version=intent.workflow_version,
                    step=PublicationStepName.COMMIT_RECORD,
                    destination=binding.forge_target,
                    approved_payload_digest=intent.approved_payload_digest,
                ),
            )
            observation = await self._forge.observe(mutation)
            if (
                observation.state is not ForgePullRequestState.MERGED
                or observation.merged_commit is None
                or observation.merged_tree_digest is None
                or observation.merged_payload_digest != binding.approved_changes.digest
            ):
                raise ValueError("Canonical material requires one verified merged Git tree")
            pack = await self._forge.read_merged_pack(
                mutation,
                expected_commit=observation.merged_commit,
                expected_tree_digest=observation.merged_tree_digest,
            )
            proof = CanonicalMergedProof(
                binding=binding,
                observation=observation,
                pack=pack,
            )
            self._proofs[intent.publication_id] = proof
            return proof

    async def release(self, intent: EffectIntent) -> CanonicalReleaseMaterial:
        cached = self._releases.get(intent.publication_id)
        if cached is not None:
            proof = self._proofs.get(intent.publication_id)
            if proof is None:
                raise RuntimeError("Cached release is missing its canonical merged proof")
            self._validate_binding(intent, proof.binding)
            return cached
        proof = await self.merged_proof(intent)
        current = await self._current_release.resolve_release(release_version=None)
        built = await asyncio.to_thread(
            _build_release_material,
            intent,
            proof,
            current.manifest,
        )
        self._releases[intent.publication_id] = built
        return built

    async def signed_release_bytes(
        self,
        intent: EffectIntent,
        *,
        canonical: bool,
    ) -> bytes:
        release = await self.release(intent)
        prefix = "releases/v1" if canonical else "signatures/releases/v1"
        object_key = f"{prefix}/release-{release.manifest.release_version}.json"
        payload = await self._writer.read_optional_bytes(
            bucket=self._bucket,
            object_key=object_key,
            max_bytes=8 * 1024 * 1024,
        )
        if payload is None:
            raise R2PublicationError(f"Signed release material is absent at {object_key}")
        try:
            envelope = SignedEnvelope.model_validate_json(payload)
            self._manifest_keys.verify(envelope)
            manifest = PublicReadReleaseManifest.model_validate(envelope.payload)
        except (ManifestVerificationError, ValueError) as error:
            raise R2PublicationError("Signed release material is invalid") from error
        if canonical_json(envelope.model_dump(mode="json")) != payload:
            raise R2PublicationError("Signed release material is not canonical JSON")
        if manifest != release.manifest:
            raise R2PublicationError("Signed release material changed canonical identity")
        return payload

    async def aclose(self) -> None:
        await self._current_release.aclose()

    @staticmethod
    def _validate_binding(intent: EffectIntent, binding: GovernanceBinding) -> None:
        if (
            binding.publication_id != intent.publication_id
            or binding.approved_changes.digest != intent.approved_payload_digest
            or binding.forge_target != intent.forge_target
        ):
            raise ValueError("Canonical material does not match the publication intent")


class CanonicalCommitObjectSource:
    identity = "opennosh.canonical-commit-object"
    version = "1.0"

    def __init__(self, authority: CanonicalPublicationMaterialAuthority) -> None:
        self._authority = authority

    async def materialize(self, intent: EffectIntent) -> PublicationObject:
        proof = await self._authority.merged_proof(intent)
        observation = proof.observation
        assert observation.merged_commit is not None
        assert observation.merged_tree_digest is not None
        payload = canonical_json(
            {
                "schema_version": "1",
                "publication_id": str(intent.publication_id),
                "decision_id": str(proof.binding.decision_id),
                "pack_id": proof.binding.pack_id,
                "approved_payload_digest": intent.approved_payload_digest,
                "merged_commit": observation.merged_commit,
                "merged_tree_digest": observation.merged_tree_digest,
            }
        )
        return PublicationObject(
            object_key=f"durability/git/{observation.merged_commit}.json",
            payload=payload,
            media_type="application/vnd.opennosh.git-proof+json",
            context={
                "merged_commit": observation.merged_commit,
                "merged_tree_digest": observation.merged_tree_digest,
            },
        )


class CanonicalEvidenceObjectSource:
    identity = "opennosh.canonical-evidence-object"
    version = "1.0"

    async def materialize(self, intent: EffectIntent) -> PublicationObject:
        digests = intent.context.get("evidence_manifest_digests")
        acknowledgements = intent.context.get("evidence_acknowledgements")
        if not isinstance(digests, list) or not digests:
            raise ValueError("Canonical evidence requires bound manifest digests")
        if not isinstance(acknowledgements, list) or not acknowledgements:
            raise ValueError("Canonical evidence requires durable acknowledgements")
        payload = canonical_json(
            {
                "schema_version": "1",
                "publication_id": str(intent.publication_id),
                "approved_payload_digest": intent.approved_payload_digest,
                "manifest_digests": digests,
                "acknowledgements": acknowledgements,
            }
        )
        digest = hashlib.sha256(payload).hexdigest()
        return PublicationObject(
            object_key=f"durability/evidence/{digest}.json",
            payload=payload,
            media_type="application/vnd.opennosh.evidence-proof+json",
            context={"evidence_manifest_digests": digests},
        )


class CanonicalReleaseManifestSource:
    identity = "opennosh.canonical-release-manifest"
    version = "1.0"

    def __init__(self, authority: CanonicalPublicationMaterialAuthority) -> None:
        self._authority = authority

    async def materialize_manifest(
        self,
        intent: EffectIntent,
    ) -> PublicReadReleaseManifest:
        return (await self._authority.release(intent)).manifest


class CanonicalReleasePublicationSource:
    identity = "opennosh.canonical-release-publication"
    version = "1.0"

    def __init__(self, authority: CanonicalPublicationMaterialAuthority) -> None:
        self._authority = authority

    async def materialize(self, intent: EffectIntent) -> PublicationObjectSet:
        release = await self._authority.release(intent)
        manifest_bytes = await self._authority.signed_release_bytes(
            intent,
            canonical=False,
        )
        manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
        manifest_key = f"releases/v1/release-{release.manifest.release_version}.json"
        objects = (
            *release.objects,
            PublicationObject(
                object_key=manifest_key,
                payload=manifest_bytes,
                media_type="application/vnd.opennosh.release+json",
                context={},
            ),
        )
        return PublicationObjectSet(
            objects=objects,
            context={
                "release_version": release.manifest.release_version,
                "manifest_digest": manifest_digest,
                "manifest_object_key": manifest_key,
            },
        )


class CanonicalReleaseDurabilitySource:
    identity = "opennosh.canonical-release-durability"
    version = "1.0"

    def __init__(self, authority: CanonicalPublicationMaterialAuthority) -> None:
        self._authority = authority

    async def materialize(self, intent: EffectIntent) -> PublicationObject:
        release = await self._authority.release(intent)
        payload = await self._authority.signed_release_bytes(intent, canonical=True)
        digest = hashlib.sha256(payload).hexdigest()
        return PublicationObject(
            object_key=f"durability/releases/{digest}.json",
            payload=payload,
            media_type="application/vnd.opennosh.release+json",
            context={
                "release_version": release.manifest.release_version,
                "manifest_digest": digest,
            },
        )


class CanonicalRegistryCheckpointSource:
    identity = "opennosh.canonical-registry-checkpoint"
    version = "1.0"

    def __init__(self, authority: CanonicalPublicationMaterialAuthority) -> None:
        self._authority = authority

    async def materialize(self, intent: EffectIntent) -> PublicationObject:
        release = await self._authority.release(intent)
        manifest_bytes = await self._authority.signed_release_bytes(
            intent,
            canonical=True,
        )
        manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
        payload = canonical_json(
            {
                "schema_version": "1",
                "publication_id": str(intent.publication_id),
                "pack_id": intent.context.get("pack_id"),
                "release_version": release.manifest.release_version,
                "manifest_digest": manifest_digest,
                "registry_result": "release-material-confirmed",
            }
        )
        return PublicationObject(
            object_key=f"registry/releases/{intent.publication_id}.json",
            payload=payload,
            media_type="application/vnd.opennosh.registry-checkpoint+json",
            context={
                "release_version": release.manifest.release_version,
                "manifest_digest": manifest_digest,
                "registry_result": "release-material-confirmed",
            },
        )


def _build_release_material(
    intent: EffectIntent,
    proof: CanonicalMergedProof,
    current: PublicReadReleaseManifest,
) -> CanonicalReleaseMaterial:
    with tempfile.TemporaryDirectory(prefix="opennosh-release-material-") as temporary:
        root = Path(temporary)
        packs_root = root / "packs"
        _write_pack_files(packs_root, proof.pack.files)
        pack_directory = packs_root / proof.binding.pack_id
        try:
            prepared = prepare_food_pack(pack_directory)
        except FoodPackLoadError as error:
            raise ValueError("Merged food pack is not releaseable") from error
        if (
            prepared.pack_rejected
            or prepared.errors
            or not prepared.records
            or prepared.pack_id != proof.binding.pack_id
            or prepared.pack_version is None
        ):
            details = "; ".join(issue.message for issue in prepared.errors[:5])
            raise ValueError(f"Merged food pack is not releaseable: {details}")
        if any(item.pack_id == prepared.pack_id for item in current.packs):
            raise ValueError(
                "Automatic publication currently requires a new canonical pack ID"
            )

        pack_bytes = _pack_archive(packs_root, pack_directory)
        pack_descriptor = _content_addressed_descriptor(
            "packs/v1",
            pack_bytes,
            "application/zip",
            suffix=".zip",
        )
        objects = [
            PublicationObject(
                object_key=pack_descriptor.object_key,
                payload=pack_bytes,
                media_type=pack_descriptor.media_type,
                context={},
            )
        ]
        foods: list[PublicFoodArtifact] = []
        for record in prepared.records:
            record_bytes = canonical_json(_food_detail(record).model_dump(mode="json"))
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
            objects.extend(
                (
                    PublicationObject(
                        object_key=record_descriptor.object_key,
                        payload=record_bytes,
                        media_type=record_descriptor.media_type,
                        context={},
                    ),
                    PublicationObject(
                        object_key=provenance_descriptor.object_key,
                        payload=provenance_bytes,
                        media_type=provenance_descriptor.media_type,
                        context={},
                    ),
                )
            )
            foods.append(
                PublicFoodArtifact(
                    source=FoodSource.COMMUNITY,
                    source_id=record.slug,
                    record=record_descriptor,
                    provenance=provenance_descriptor,
                )
            )
        updated_slugs = {food.source_id for food in foods}
        existing_slugs = {
            food.source_id
            for food in current.foods
            if food.source is FoodSource.COMMUNITY
        }
        if updated_slugs & existing_slugs:
            raise ValueError(
                "Automatic publication cannot replace an existing community food yet"
            )
        merged_foods = tuple(
            sorted(
                (*current.foods, *foods),
                key=lambda item: (item.source.value, item.source_id),
            )
        )
        pack = PublicPackArtifact(
            pack_id=prepared.pack_id,
            pack_version=prepared.pack_version,
            download=pack_descriptor,
        )
        merged_packs = tuple(
            sorted(
                (*current.packs, pack),
                key=lambda item: (item.pack_id, item.pack_version),
            )
        )
        assert proof.observation.merged_at is not None
        manifest = PublicReadReleaseManifest(
            release_version=_release_version(
                proof.observation.merged_at.timestamp(),
                intent.publication_id,
            ),
            published_at=proof.observation.merged_at.astimezone(UTC),
            publication_receipt_key=receipt_object_key(intent.publication_id),
            foods=merged_foods,
            packs=merged_packs,
        )
        return CanonicalReleaseMaterial(
            manifest=manifest,
            objects=tuple(sorted(objects, key=lambda item: item.object_key)),
        )


def _write_pack_files(root: Path, files: Mapping[str, bytes]) -> None:
    resolved = root.resolve(strict=False)
    for raw_path, payload in files.items():
        path = PurePosixPath(raw_path)
        if path.is_absolute() or ".." in path.parts or path.as_posix() != raw_path:
            raise ValueError("Merged pack file path is invalid")
        target = (resolved / path).resolve(strict=False)
        if not target.is_relative_to(resolved):
            raise ValueError("Merged pack file escapes the release root")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)


def _release_version(merged_timestamp: float, publication_id: UUID) -> str:
    value = publication_id.int
    return (
        f"1.{int(merged_timestamp)}."
        f"{(value >> 96) & 0xFFFFFFFF}.{value & 0xFFFFFFFF}"
    )
