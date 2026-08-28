from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from opennosh_api.first_contribution.prepare import _build_package
from opennosh_api.governance.contracts import (
    CANONICAL_FORGE_TARGET,
    PROTECTED_STATUS_CHECKS,
    ApprovedChangeSet,
    ApprovedFileChange,
)
from opennosh_api.governance.gate import GovernanceGate
from opennosh_api.governance.policy import GovernanceBinding
from opennosh_api.public.artifacts import PublicReadReleaseManifest
from opennosh_api.public.r2 import R2PublicationError, S3R2ObjectWriter
from opennosh_api.public.signing import public_key_text, sign_envelope
from opennosh_api.public_commons.manifests import ManifestKeyRing
from opennosh_api.publication.forge.contracts import (
    ForgeMaterialClient,
    ForgeMutation,
    ForgeObservation,
    ForgePullRequestState,
    MergedPackMaterial,
)
from opennosh_api.publication.materials import (
    CanonicalCommitObjectSource,
    CanonicalEvidenceObjectSource,
    CanonicalMergedProof,
    CanonicalPublicationMaterialAuthority,
    CanonicalRegistryCheckpointSource,
    CanonicalReleaseDurabilitySource,
    CanonicalReleaseManifestSource,
    CanonicalReleaseMaterial,
    CanonicalReleasePublicationSource,
    _build_release_material,
    _write_pack_files,
)
from opennosh_api.publication.state import (
    EffectIntent,
    PublicationStepName,
    effect_idempotency_key,
)

NOW = datetime(2026, 8, 28, 1, tzinfo=UTC)
PUBLICATION_ID = UUID("00000000-0000-4000-8000-000000000001")
DECISION_ID = UUID("00000000-0000-4000-8000-000000000002")
PACK_ID = "balanced-pack"
ROOT = Path(__file__).resolve().parents[3]


def _pack_files() -> dict[str, bytes]:
    fixture = ROOT / "api/tests/foodpacks/fixtures/valid/balanced-pack"
    return {
        f"{PACK_ID}/pack.yaml": (fixture / "pack.yaml").read_bytes(),
        f"{PACK_ID}/foods/foods.yaml": (fixture / "foods/foods.yaml").read_bytes(),
        "CC0-1.0.txt": (ROOT / "packs/CC0-1.0.txt").read_bytes(),
    }


def _binding() -> GovernanceBinding:
    files = _pack_files()
    approved = ApprovedChangeSet.build(
        pack_id=PACK_ID,
        files=tuple(
            ApprovedFileChange(
                path=f"packs/{path}",
                content=payload.decode("utf-8"),
            )
            for path, payload in files.items()
            if path.startswith(f"{PACK_ID}/")
        ),
    )
    return GovernanceBinding(
        publication_id=PUBLICATION_ID,
        decision_id=DECISION_ID,
        pack_id=PACK_ID,
        contributor_actor_id=UUID("00000000-0000-4000-8000-000000000003"),
        approving_actor_id=UUID("00000000-0000-4000-8000-000000000004"),
        approved_at=NOW - timedelta(hours=1),
        approved_changes=approved,
        expected_base_commit="a" * 40,
        required_checks=PROTECTED_STATUS_CHECKS,
        forge_target=CANONICAL_FORGE_TARGET,
        role_granted_at=NOW - timedelta(days=1),
    )


def _observation(binding: GovernanceBinding) -> ForgeObservation:
    return ForgeObservation(
        state=ForgePullRequestState.MERGED,
        merged_at=NOW,
        merged_commit="c" * 40,
        merged_tree_digest="d" * 64,
        merged_payload_digest=binding.approved_changes.digest,
    )


def _intent(
    step: PublicationStepName = PublicationStepName.COPY_RELEASE,
    *,
    context: dict[str, object] | None = None,
) -> EffectIntent:
    binding = _binding()
    return EffectIntent(
        publication_id=PUBLICATION_ID,
        workflow_version="1.0",
        workflow_revision=1,
        step=step,
        destination="urn:opennosh:test",
        approved_payload_digest=binding.approved_changes.digest,
        idempotency_key="f" * 64,
        forge_target=CANONICAL_FORGE_TARGET,
        context=context or {},
    )


class Gate:
    def __init__(self, binding: GovernanceBinding) -> None:
        self.binding = binding

    async def binding_for(self, _publication_id: UUID) -> GovernanceBinding:
        return self.binding


class Forge:
    identity = "test-forge"
    version = "1.0"

    def __init__(self, binding: GovernanceBinding) -> None:
        self.binding = binding
        self.mutations: list[ForgeMutation] = []

    async def observe(self, mutation: ForgeMutation) -> ForgeObservation:
        self.mutations.append(mutation)
        return _observation(self.binding)

    async def read_merged_pack(
        self,
        mutation: ForgeMutation,
        *,
        expected_commit: str,
        expected_tree_digest: str,
    ) -> MergedPackMaterial:
        assert mutation == self.mutations[-1]
        assert expected_commit == "c" * 40
        assert expected_tree_digest == "d" * 64
        return MergedPackMaterial(
            commit_sha=expected_commit,
            tree_digest=expected_tree_digest,
            files=_pack_files(),
        )


class Reader:
    def __init__(self, manifest: PublicReadReleaseManifest) -> None:
        self.manifest = manifest
        self.closed = False

    async def resolve_release(self, *, release_version: str | None) -> object:
        assert release_version is None
        return SimpleNamespace(manifest=self.manifest)

    async def aclose(self) -> None:
        self.closed = True


class Writer:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def read_optional_bytes(
        self,
        *,
        bucket: str,
        object_key: str,
        max_bytes: int,
    ) -> bytes | None:
        assert bucket == "opennosh-public-commons"
        payload = self.objects.get(object_key)
        assert payload is None or len(payload) <= max_bytes
        return payload


@pytest.mark.asyncio
async def test_material_authority_reopens_the_commit_step_branch_identity() -> None:
    binding = _binding()
    forge = Forge(binding)
    authority = CanonicalPublicationMaterialAuthority(
        governance_gate=cast(GovernanceGate, Gate(binding)),
        forge=cast(ForgeMaterialClient, forge),
        current_release=cast(Any, SimpleNamespace()),
        writer=cast(S3R2ObjectWriter, SimpleNamespace()),
        bucket="opennosh-public-commons",
        manifest_keys=cast(ManifestKeyRing, SimpleNamespace()),
    )
    intent = _intent()

    proof = await authority.merged_proof(intent)

    assert proof.pack.files == _pack_files()
    assert forge.mutations[0].idempotency_key == effect_idempotency_key(
        publication_id=PUBLICATION_ID,
        workflow_version="1.0",
        step=PublicationStepName.COMMIT_RECORD,
        destination=CANONICAL_FORGE_TARGET,
        approved_payload_digest=intent.approved_payload_digest,
    )
    assert forge.mutations[0].idempotency_key != intent.idempotency_key

    assert await authority.merged_proof(intent) is proof
    with pytest.raises(ValueError, match="does not match"):
        await authority.merged_proof(
            replace(intent, approved_payload_digest="0" * 64)
        )


@pytest.mark.asyncio
async def test_authority_builds_caches_and_verifies_staged_and_canonical_release() -> None:
    binding = _binding()
    current = PublicReadReleaseManifest(
        release_version="0.60.0.0",
        published_at=NOW - timedelta(days=1),
        publication_receipt_key=(
            "receipts/v1/00000000-0000-4000-8000-000000000099.json"
        ),
    )
    reader = Reader(current)
    writer = Writer()
    signing_key = Ed25519PrivateKey.from_private_bytes(b"m" * 32)
    key_ring = ManifestKeyRing.from_config(
        f"manifest-online:{public_key_text(signing_key)}"
    )
    authority = CanonicalPublicationMaterialAuthority(
        governance_gate=cast(GovernanceGate, Gate(binding)),
        forge=cast(ForgeMaterialClient, Forge(binding)),
        current_release=cast(Any, reader),
        writer=cast(S3R2ObjectWriter, writer),
        bucket="opennosh-public-commons",
        manifest_keys=key_ring,
    )
    intent = _intent()
    release = await authority.release(intent)
    assert await authority.release(intent) is release
    with pytest.raises(ValueError, match="does not match"):
        await authority.release(
            replace(intent, approved_payload_digest="0" * 64)
        )
    signed = sign_envelope(
        release.manifest.model_dump(mode="json"),
        key_id="manifest-online",
        private_key=signing_key,
    )
    version = release.manifest.release_version
    writer.objects[f"signatures/releases/v1/release-{version}.json"] = signed
    writer.objects[f"releases/v1/release-{version}.json"] = signed

    assert await authority.signed_release_bytes(intent, canonical=False) == signed
    assert await authority.signed_release_bytes(intent, canonical=True) == signed

    writer.objects[f"releases/v1/release-{version}.json"] = b"{}"
    with pytest.raises(R2PublicationError, match="invalid"):
        await authority.signed_release_bytes(intent, canonical=True)
    writer.objects.pop(f"releases/v1/release-{version}.json")
    with pytest.raises(R2PublicationError, match="absent"):
        await authority.signed_release_bytes(intent, canonical=True)

    await authority.aclose()
    assert reader.closed is True


class SourceAuthority:
    def __init__(
        self,
        *,
        proof: CanonicalMergedProof,
        release: CanonicalReleaseMaterial,
        signed: bytes,
    ) -> None:
        self.proof = proof
        self.release_material = release
        self.signed = signed
        self.canonical_reads: list[bool] = []

    async def merged_proof(self, _intent: EffectIntent) -> CanonicalMergedProof:
        return self.proof

    async def release(self, _intent: EffectIntent) -> CanonicalReleaseMaterial:
        return self.release_material

    async def signed_release_bytes(
        self,
        _intent: EffectIntent,
        *,
        canonical: bool,
    ) -> bytes:
        self.canonical_reads.append(canonical)
        return self.signed


@pytest.mark.asyncio
async def test_all_canonical_sources_materialize_the_bound_immutable_objects() -> None:
    binding = _binding()
    proof = CanonicalMergedProof(
        binding=binding,
        observation=_observation(binding),
        pack=MergedPackMaterial(
            commit_sha="c" * 40,
            tree_digest="d" * 64,
            files=_pack_files(),
        ),
    )
    current = PublicReadReleaseManifest(
        release_version="0.60.0.0",
        published_at=NOW - timedelta(days=1),
        publication_receipt_key=(
            "receipts/v1/00000000-0000-4000-8000-000000000099.json"
        ),
    )
    release = _build_release_material(_intent(), proof, current)
    signed = b'{"signed":"release"}'
    authority = cast(
        CanonicalPublicationMaterialAuthority,
        SourceAuthority(proof=proof, release=release, signed=signed),
    )
    context = {
        "pack_id": PACK_ID,
        "evidence_manifest_digests": ["e" * 64],
        "evidence_acknowledgements": [{"durability": "verified"}],
    }
    intent = _intent(context=context)

    commit = await CanonicalCommitObjectSource(authority).materialize(intent)
    evidence = await CanonicalEvidenceObjectSource().materialize(intent)
    manifest = await CanonicalReleaseManifestSource(authority).materialize_manifest(
        intent
    )
    published = await CanonicalReleasePublicationSource(authority).materialize(intent)
    durable = await CanonicalReleaseDurabilitySource(authority).materialize(intent)
    registry = await CanonicalRegistryCheckpointSource(authority).materialize(intent)

    assert commit.object_key == f"durability/git/{'c' * 40}.json"
    assert json.loads(commit.payload)["merged_tree_digest"] == "d" * 64
    assert evidence.object_key.startswith("durability/evidence/")
    assert json.loads(evidence.payload)["manifest_digests"] == ["e" * 64]
    assert manifest == release.manifest
    assert published.objects[:-1] == release.objects
    assert published.objects[-1].object_key == (
        f"releases/v1/release-{release.manifest.release_version}.json"
    )
    assert durable.object_key.startswith("durability/releases/")
    assert json.loads(registry.payload)["registry_result"] == (
        "release-material-confirmed"
    )
    assert cast(SourceAuthority, authority).canonical_reads == [False, True, True]

    with pytest.raises(ValueError, match="manifest digests"):
        await CanonicalEvidenceObjectSource().materialize(_intent())
    with pytest.raises(ValueError, match="durable acknowledgements"):
        await CanonicalEvidenceObjectSource().materialize(
            _intent(context={"evidence_manifest_digests": ["e" * 64]})
        )


def test_release_material_is_additive_content_addressed_and_deterministic() -> None:
    binding = _binding()
    proof = CanonicalMergedProof(
        binding=binding,
        observation=_observation(binding),
        pack=MergedPackMaterial(
            commit_sha="c" * 40,
            tree_digest="d" * 64,
            files=_pack_files(),
        ),
    )
    current = PublicReadReleaseManifest(
        release_version="0.60.0.0",
        published_at=NOW - timedelta(days=1),
        publication_receipt_key=(
            "receipts/v1/00000000-0000-4000-8000-000000000099.json"
        ),
    )

    first = _build_release_material(_intent(), proof, current)
    second = _build_release_material(_intent(), proof, current)

    assert first == second
    assert [item.source_id for item in first.manifest.foods] == [
        "balanced-thepla",
        "public-domain-lassi",
    ]
    assert [(item.pack_id, item.pack_version) for item in first.manifest.packs] == [
        (PACK_ID, "1.0.0")
    ]
    assert tuple(item.object_key for item in first.objects) == tuple(
        sorted(item.object_key for item in first.objects)
    )
    assert len(first.objects) == 5
    for descriptor in (
        *(food.record for food in first.manifest.foods),
        *(food.provenance for food in first.manifest.foods),
        *(pack.download for pack in first.manifest.packs),
    ):
        assert descriptor.digest in descriptor.object_key

    with pytest.raises(ValueError, match="new canonical pack ID"):
        _build_release_material(_intent(), proof, first.manifest)

    food_only_current = current.model_copy(update={"foods": first.manifest.foods})
    with pytest.raises(ValueError, match="replace an existing community food"):
        _build_release_material(_intent(), proof, food_only_current)

    invalid_proof = replace(
        proof,
        pack=MergedPackMaterial(
            commit_sha="c" * 40,
            tree_digest="d" * 64,
            files={f"{PACK_ID}/pack.yaml": b"not: a: valid: pack"},
        ),
    )
    with pytest.raises(ValueError, match="not releaseable"):
        _build_release_material(_intent(), invalid_proof, current)


def test_first_contribution_pack_passes_governed_release_material_path() -> None:
    package = _build_package("a" * 64)
    changes = ApprovedChangeSet.from_json(package.approved_changes)
    binding = replace(
        _binding(),
        publication_id=package.publication_intent_id,
        decision_id=package.decision_id,
        pack_id="common-fruits",
        contributor_actor_id=package.source_actor_id,
        approved_changes=changes,
    )
    files = {
        change.path.removeprefix("packs/"): change.content.encode("utf-8")
        for change in changes.files
    }
    files["CC0-1.0.txt"] = (ROOT / "packs/CC0-1.0.txt").read_bytes()
    proof = CanonicalMergedProof(
        binding=binding,
        observation=_observation(binding),
        pack=MergedPackMaterial(
            commit_sha="c" * 40,
            tree_digest="d" * 64,
            files=files,
        ),
    )
    intent = replace(
        _intent(),
        publication_id=package.publication_intent_id,
        approved_payload_digest=changes.digest,
    )
    current = PublicReadReleaseManifest(
        release_version="0.62.0.0",
        published_at=NOW - timedelta(days=1),
        publication_receipt_key=(
            "receipts/v1/00000000-0000-4000-8000-000000000099.json"
        ),
    )

    release = _build_release_material(intent, proof, current)

    assert [food.source_id for food in release.manifest.foods] == [package.record_id]
    assert [(pack.pack_id, pack.pack_version) for pack in release.manifest.packs] == [
        ("common-fruits", "1.0.0")
    ]
    assert len(release.objects) == 3


def test_merged_pack_writer_rejects_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="path is invalid"):
        _write_pack_files(tmp_path, {"../outside": b"payload"})
