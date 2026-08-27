from __future__ import annotations

import asyncio
import base64
import hashlib
import subprocess
import sys
import textwrap
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from opennosh_api.database import get_database_session
from opennosh_api.foods.schemas import FoodSource
from opennosh_api.main import create_app
from opennosh_api.public.artifacts import (
    ArtifactUnavailableError,
    LocalArtifactStore,
    MemoryArtifactStore,
    PublicArtifactReadService,
    PublicFoodArtifact,
    PublicPackArtifact,
    PublicReadLatestPointer,
    PublicReadReleaseManifest,
    activate_verified_release,
    artifact_descriptor,
)
from opennosh_api.public_commons.manifests import (
    ManifestKeyRing,
    SignedEnvelope,
    canonical_json,
)
from opennosh_api.publication.receipts import (
    Ed25519ReceiptSigner,
    PublicationReceiptKeyRing,
    canonical_signed_receipt_bytes,
    receipt_draft_from_snapshot,
)
from opennosh_api.publication.state import PublicationStepName
from opennosh_api.settings import Settings
from tests.publication.test_planner import NOW, snapshot

RELEASE = "0.52.0.0"
MANIFEST_PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(b"m" * 32)
RECEIPT_PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(b"r" * 32)
MANIFEST_KEYS = ManifestKeyRing({"manifest-test": MANIFEST_PRIVATE_KEY.public_key()})
RECEIPT_KEYS = PublicationReceiptKeyRing({"receipt-test": RECEIPT_PRIVATE_KEY.public_key()})
RECEIPT_SIGNER = Ed25519ReceiptSigner(
    key_id="receipt-test",
    publisher_identity="opennosh:artifact-test",
    private_key=RECEIPT_PRIVATE_KEY,
)

RECORD = canonical_json(
    {
        "schema_version": "1.0",
        "id": "community:rajma-masala",
        "source": "community",
        "source_id": "rajma-masala",
        "name": "Rajma masala",
        "name_local": None,
        "category": "Home-style preparation",
        "attribution": {
            "source": "community",
            "license": "CC0-1.0",
            "source_uri": "https://example.org/source",
            "source_license": "CC BY 4.0",
            "contributed_by": "Punjab Foods Collective",
            "pack_id": "north-india-home-foods",
            "pack_version": "2.4.0",
            "provenance": "Checked household preparations",
        },
        "nutrients": {
            "basis": "per_100g",
            "nutrients": {"energy_kcal": "127", "protein_g": "6.2"},
        },
        "portions": [{"grams": "180", "name": "1 katori"}],
    }
)
PROVENANCE = b"<!doctype html><title>Rajma provenance</title><p>Verified evidence.</p>"
PACK = b"PK\x03\x04signed-pack-fixture"


def _sign(payload: dict[str, object]) -> bytes:
    signature = (
        base64.urlsafe_b64encode(MANIFEST_PRIVATE_KEY.sign(canonical_json(payload)))
        .decode()
        .rstrip("=")
    )
    envelope = SignedEnvelope(key_id="manifest-test", payload=payload, signature=signature)
    return canonical_json(envelope.model_dump(mode="json"))


def _descriptor(prefix: str, payload: bytes, media_type: str):  # type: ignore[no-untyped-def]
    digest = hashlib.sha256(payload).hexdigest()
    return artifact_descriptor(f"{prefix}/{digest}", payload, media_type)


async def _published(tmp_path: Path) -> tuple[PublicArtifactReadService, MemoryArtifactStore]:
    store = MemoryArtifactStore()
    checkpoint = tmp_path / "trusted-latest.json"
    service = PublicArtifactReadService(
        store=store,
        manifest_keys=MANIFEST_KEYS,
        receipt_keys=RECEIPT_KEYS,
        checkpoint_path=checkpoint,
    )
    record = _descriptor("records/v1", RECORD, "application/json")
    provenance = _descriptor("provenance/v1", PROVENANCE, "text/html")
    pack = _descriptor("packs/v1", PACK, "application/zip")
    manifest = PublicReadReleaseManifest(
        release_version=RELEASE,
        published_at=NOW,
        publication_receipt_key=("receipts/v1/11111111-1111-4111-8111-111111111111.json"),
        foods=(
            PublicFoodArtifact(
                source=FoodSource.COMMUNITY,
                source_id="rajma-masala",
                record=record,
                provenance=provenance,
            ),
        ),
        packs=(
            PublicPackArtifact(
                pack_id="north-india-home-foods",
                pack_version="2.4.0",
                download=pack,
            ),
        ),
    )
    manifest_bytes = _sign(manifest.model_dump(mode="json"))
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    source = snapshot(current=7)
    acknowledgements = tuple(
        replace(
            acknowledgement,
            content_digest=manifest_digest,
            context={
                **dict(acknowledgement.context),
                **(
                    {"release_version": RELEASE}
                    if acknowledgement.step is PublicationStepName.SIGN_RELEASE
                    else {}
                ),
            },
        )
        if acknowledgement.step
        in {PublicationStepName.SIGN_RELEASE, PublicationStepName.COPY_RELEASE}
        else acknowledgement
        for acknowledgement in source.acknowledgements
    )
    receipt = RECEIPT_SIGNER.sign(
        receipt_draft_from_snapshot(replace(source, acknowledgements=acknowledgements))
    )
    receipt_bytes = canonical_signed_receipt_bytes(receipt)
    manifest_descriptor = artifact_descriptor(
        f"releases/v1/release-{RELEASE}.json",
        manifest_bytes,
        "application/vnd.opennosh.release+json",
    )
    pointer = PublicReadLatestPointer(
        release_version=RELEASE,
        manifest=manifest_descriptor,
        expires_at=NOW + timedelta(minutes=5),
    )
    pointer_bytes = _sign(pointer.model_dump(mode="json"))
    await activate_verified_release(
        service=service,
        store=store,
        immutable_objects={
            record.object_key: RECORD,
            provenance.object_key: PROVENANCE,
            pack.object_key: PACK,
        },
        manifest_bytes=manifest_bytes,
        receipt_bytes=receipt_bytes,
        pointer_bytes=pointer_bytes,
    )
    return service, store


@pytest.mark.asyncio
async def test_exact_release_survives_without_fastapi_or_postgresql(tmp_path: Path) -> None:
    service, _ = await _published(tmp_path)

    food = await service.food(FoodSource.COMMUNITY, "rajma-masala", release_version=RELEASE)
    provenance, _ = await service.provenance(
        FoodSource.COMMUNITY, "rajma-masala", release_version=RELEASE
    )
    pack, _, _ = await service.pack("north-india-home-foods", "2.4.0", release_version=RELEASE)
    manifest, _ = await service.signed_manifest(RELEASE)

    assert food.record.name == "Rajma masala"
    assert food.release.state == "verified"
    assert food.immutable_url.endswith(f"/releases/{RELEASE}/foods/community/rajma-masala")
    assert provenance == PROVENANCE
    assert pack == PACK
    assert hashlib.sha256(manifest).hexdigest()


@pytest.mark.asyncio
async def test_latest_falls_back_to_visible_last_verified_release(tmp_path: Path) -> None:
    service, store = await _published(tmp_path)
    first = await service.food(FoodSource.COMMUNITY, "rajma-masala", now=NOW)
    store.objects["latest/v1.json"] = b"tampered"

    stale = await service.food(
        FoodSource.COMMUNITY,
        "rajma-masala",
        now=NOW + timedelta(hours=2),
    )

    assert first.release.state == "verified"
    assert stale.release.state == "stale"
    assert stale.release.stale_age_seconds == 6900


@pytest.mark.asyncio
async def test_verified_cache_survives_complete_origin_outage_and_restart(tmp_path: Path) -> None:
    published_service, origin = await _published(tmp_path / "publisher")
    release = await published_service.resolve_release(release_version=RELEASE)
    await published_service.aclose()
    cache_directory = tmp_path / "cache"
    checkpoint = tmp_path / "state" / "latest.json"
    warming_service = PublicArtifactReadService(
        store=origin,
        cache_store=LocalArtifactStore(cache_directory),
        manifest_keys=MANIFEST_KEYS,
        receipt_keys=RECEIPT_KEYS,
        checkpoint_path=checkpoint,
    )
    await warming_service.food(FoodSource.COMMUNITY, "rajma-masala", now=NOW)
    await warming_service.provenance(
        FoodSource.COMMUNITY,
        "rajma-masala",
        release_version=RELEASE,
    )
    await warming_service.pack(
        "north-india-home-foods",
        "2.4.0",
        release_version=RELEASE,
    )
    await warming_service.aclose()

    origin.objects.clear()
    restarted = PublicArtifactReadService(
        store=origin,
        cache_store=LocalArtifactStore(cache_directory),
        manifest_keys=MANIFEST_KEYS,
        receipt_keys=RECEIPT_KEYS,
        checkpoint_path=checkpoint,
    )
    try:
        stale = await restarted.food(
            FoodSource.COMMUNITY,
            "rajma-masala",
            now=release.manifest.published_at + timedelta(days=1),
        )
        provenance, _ = await restarted.provenance(
            FoodSource.COMMUNITY,
            "rajma-masala",
            release_version=RELEASE,
        )
        pack, _, _ = await restarted.pack(
            "north-india-home-foods",
            "2.4.0",
            release_version=RELEASE,
        )
    finally:
        await restarted.aclose()

    assert stale.release.state == "stale"
    assert stale.record.name == "Rajma masala"
    assert provenance == PROVENANCE
    assert pack == PACK


@pytest.mark.asyncio
async def test_verified_cache_conflict_fails_closed(tmp_path: Path) -> None:
    published_service, origin = await _published(tmp_path / "publisher")
    release = await published_service.resolve_release(release_version=RELEASE)
    await published_service.aclose()
    cache_directory = tmp_path / "cache"
    service = PublicArtifactReadService(
        store=origin,
        cache_store=LocalArtifactStore(cache_directory),
        manifest_keys=MANIFEST_KEYS,
        receipt_keys=RECEIPT_KEYS,
        checkpoint_path=tmp_path / "state" / "latest.json",
    )
    await service.food(FoodSource.COMMUNITY, "rajma-masala", now=NOW)
    record_key = release.manifest.foods[0].record.object_key
    await asyncio.to_thread(
        (cache_directory / record_key).write_bytes,
        b"tampered cache",
    )

    try:
        with pytest.raises(ArtifactUnavailableError, match="verified_cache_conflict"):
            await service.food(
                FoodSource.COMMUNITY,
                "rajma-masala",
                release_version=RELEASE,
            )
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_failed_activation_never_moves_latest_pointer(tmp_path: Path) -> None:
    service, store = await _published(tmp_path)
    release = await service.resolve_release(release_version=RELEASE)
    pointer_bytes = store.objects["latest/v1.json"]
    receipt_bytes = store.objects[release.manifest.publication_receipt_key]
    objects = {
        release.manifest.foods[0].record.object_key: b"tampered record",
        release.manifest.foods[0].provenance.object_key: PROVENANCE,
        release.manifest.packs[0].download.object_key: PACK,
    }

    with pytest.raises(ArtifactUnavailableError, match="artifact_size_mismatch"):
        await activate_verified_release(
            service=service,
            store=store,
            immutable_objects=objects,
            manifest_bytes=release.manifest_bytes,
            receipt_bytes=receipt_bytes,
            pointer_bytes=pointer_bytes,
        )

    assert store.objects["latest/v1.json"] == pointer_bytes
    verified = await service.food(FoodSource.COMMUNITY, "rajma-masala", release_version=RELEASE)
    assert verified.record.name == "Rajma masala"


@pytest.mark.asyncio
async def test_activation_rejects_manifest_size_mismatch_before_latest_moves(
    tmp_path: Path,
) -> None:
    service, store = await _published(tmp_path)
    release = await service.resolve_release(release_version=RELEASE)
    original_pointer = store.objects["latest/v1.json"]
    descriptor = artifact_descriptor(
        f"releases/v1/release-{RELEASE}.json",
        release.manifest_bytes,
        "application/vnd.opennosh.release+json",
    ).model_copy(update={"size_bytes": len(release.manifest_bytes) + 1})
    invalid_pointer = PublicReadLatestPointer(
        release_version=RELEASE,
        manifest=descriptor,
        expires_at=release.manifest.published_at + timedelta(minutes=5),
    )
    immutable_objects = {
        artifact.object_key: store.objects[artifact.object_key]
        for artifact in (
            release.manifest.foods[0].record,
            release.manifest.foods[0].provenance,
            release.manifest.packs[0].download,
        )
    }

    with pytest.raises(ArtifactUnavailableError, match="latest_pointer_manifest_mismatch"):
        await activate_verified_release(
            service=service,
            store=store,
            immutable_objects=immutable_objects,
            manifest_bytes=release.manifest_bytes,
            receipt_bytes=store.objects[release.manifest.publication_receipt_key],
            pointer_bytes=_sign(invalid_pointer.model_dump(mode="json")),
        )

    assert store.objects["latest/v1.json"] == original_pointer


@pytest.mark.asyncio
async def test_same_release_pointer_expiry_cannot_roll_back_checkpoint(tmp_path: Path) -> None:
    service, store = await _published(tmp_path)
    release = await service.resolve_release(release_version=RELEASE)
    descriptor = artifact_descriptor(
        f"releases/v1/release-{RELEASE}.json",
        release.manifest_bytes,
        "application/vnd.opennosh.release+json",
    )
    renewed = PublicReadLatestPointer(
        release_version=RELEASE,
        manifest=descriptor,
        issued_at=NOW + timedelta(minutes=1),
        expires_at=NOW + timedelta(hours=23),
    )
    store.objects["latest/v1.json"] = _sign(renewed.model_dump(mode="json"))
    await service.food(
        FoodSource.COMMUNITY,
        "rajma-masala",
        now=NOW + timedelta(minutes=2),
    )
    checkpoint = (tmp_path / "trusted-latest.json").read_bytes()

    rollback = renewed.model_copy(
        update={
            "issued_at": NOW + timedelta(minutes=2),
            "expires_at": NOW + timedelta(hours=22),
        }
    )
    store.objects["latest/v1.json"] = _sign(rollback.model_dump(mode="json"))
    result = await service.food(
        FoodSource.COMMUNITY,
        "rajma-masala",
        now=NOW + timedelta(minutes=3),
    )

    assert result.release.state == "stale"
    assert (tmp_path / "trusted-latest.json").read_bytes() == checkpoint


@pytest.mark.asyncio
async def test_tampered_record_is_never_exposed(tmp_path: Path) -> None:
    service, store = await _published(tmp_path)
    release = await service.resolve_release(release_version=RELEASE)
    key = release.manifest.foods[0].record.object_key
    store.objects[key] = b"invented nutrition"

    with pytest.raises(ArtifactUnavailableError, match="artifact_(size|digest)_mismatch"):
        await service.food(FoodSource.COMMUNITY, "rajma-masala", release_version=RELEASE)


def test_public_routes_do_not_acquire_a_database_session(tmp_path: Path) -> None:
    service, _ = asyncio.run(_published(tmp_path))
    database_calls = 0

    async def unavailable_database():  # type: ignore[no-untyped-def]
        nonlocal database_calls
        database_calls += 1
        raise RuntimeError("postgresql unavailable")

    app = create_app(Settings(_env_file=None))
    app.state.public_artifact_read_service = service
    app.dependency_overrides[get_database_session] = unavailable_database

    with TestClient(app) as client:
        record = client.get(f"/api/v1/public/releases/{RELEASE}/foods/community/rajma-masala")
        provenance = client.get(
            f"/api/v1/public/releases/{RELEASE}/foods/community/rajma-masala/provenance"
        )
        manifest = client.get(f"/api/v1/public/releases/{RELEASE}/manifest")
        pack = client.get(
            f"/api/v1/public/releases/{RELEASE}/packs/north-india-home-foods/2.4.0/download"
        )

    assert database_calls == 0
    assert record.status_code == 200
    assert record.json()["record"]["name"] == "Rajma masala"
    assert record.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert provenance.content == PROVENANCE
    assert "default-src 'none'" in provenance.headers["content-security-policy"]
    assert manifest.status_code == 200
    assert pack.content == PACK
    assert pack.headers["content-type"].startswith("application/zip")
    assert pack.headers["content-disposition"].endswith('north-india-home-foods-2.4.0.zip"')


@pytest.mark.asyncio
async def test_expired_untrusted_latest_pointer_is_not_checkpointed(tmp_path: Path) -> None:
    service, _ = await _published(tmp_path)

    with pytest.raises(ArtifactUnavailableError, match="latest_release_unavailable"):
        await service.food(
            FoodSource.COMMUNITY,
            "rajma-masala",
            now=NOW + timedelta(days=2),
        )

    assert not (tmp_path / "trusted-latest.json").exists()


@pytest.mark.asyncio
async def test_receipt_must_bind_copy_release_step_to_manifest(tmp_path: Path) -> None:
    service, store = await _published(tmp_path)
    release = await service.resolve_release(release_version=RELEASE)
    manifest_digest = hashlib.sha256(release.manifest_bytes).hexdigest()
    source = snapshot(current=7)
    acknowledgements = tuple(
        replace(
            acknowledgement,
            content_digest=(
                "f" * 64
                if acknowledgement.step is PublicationStepName.COPY_RELEASE
                else manifest_digest
                if acknowledgement.step
                in {PublicationStepName.SIGN_RELEASE, PublicationStepName.COPY_COMMIT}
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
    invalid_receipt = RECEIPT_SIGNER.sign(
        receipt_draft_from_snapshot(replace(source, acknowledgements=acknowledgements))
    )
    immutable_objects = {
        artifact.object_key: store.objects[artifact.object_key]
        for artifact in (
            release.manifest.foods[0].record,
            release.manifest.foods[0].provenance,
            release.manifest.packs[0].download,
        )
    }

    with pytest.raises(ArtifactUnavailableError, match="publication_receipt_binding_invalid"):
        await activate_verified_release(
            service=service,
            store=store,
            immutable_objects=immutable_objects,
            manifest_bytes=release.manifest_bytes,
            receipt_bytes=canonical_signed_receipt_bytes(invalid_receipt),
            pointer_bytes=store.objects["latest/v1.json"],
        )


def test_unconfigured_artifact_route_uses_service_unavailable_problem() -> None:
    app = create_app(Settings(_env_file=None))

    with TestClient(app) as client:
        response = client.get(f"/api/v1/public/releases/{RELEASE}/foods/community/rajma-masala")

    assert response.status_code == 503
    assert response.headers["retry-after"] == "60"
    assert response.json()["code"] == "service_unavailable"


def test_artifact_core_imports_without_fastapi_or_database_drivers() -> None:
    script = textwrap.dedent(
        """
        import builtins

        blocked = {"fastapi", "sqlalchemy", "asyncpg"}
        original_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name.partition(".")[0] in blocked:
                raise AssertionError(f"blocked dependency imported: {name}")
            return original_import(name, *args, **kwargs)

        builtins.__import__ = guarded_import
        from opennosh_api.public.artifacts import MemoryArtifactStore
        assert MemoryArtifactStore().objects == {}
        """
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[2],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
