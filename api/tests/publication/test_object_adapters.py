from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from opennosh_api.public.artifacts import PublicReadReleaseManifest
from opennosh_api.public.r2 import R2ImmutableConflictError, S3R2ObjectWriter
from opennosh_api.public.signing import public_key_text
from opennosh_api.public_commons.manifests import ManifestKeyRing, SignedEnvelope
from opennosh_api.publication.adapters import PublicationEffectError
from opennosh_api.publication.object_adapters import (
    Ed25519ReleaseManifestSource,
    PublicationObject,
    PublicationObjectSet,
    R2ImmutablePublicationAdapter,
    R2PublicationReceiptStore,
)
from opennosh_api.publication.state import (
    EffectIntent,
    ObservationStatus,
    PublicationStepName,
)

NOW = datetime(2026, 8, 28, 1, tzinfo=UTC)
PUBLICATION_ID = UUID("00000000-0000-4000-8000-000000000001")
BUCKET = "opennosh-public-commons"


class StaticSource:
    identity = "canonical-fixture"
    version = "1.0"

    def __init__(self, material: PublicationObject) -> None:
        self.material = material
        self.calls = 0

    async def materialize(self, _intent: EffectIntent) -> PublicationObject:
        self.calls += 1
        return self.material


class StaticManifestSource:
    identity = "canonical-manifest-fixture"
    version = "1.0"

    async def materialize_manifest(
        self, _intent: EffectIntent
    ) -> PublicReadReleaseManifest:
        return PublicReadReleaseManifest(
            release_version="0.60.0.0",
            published_at=NOW,
            publication_receipt_key=f"receipts/v1/{PUBLICATION_ID}.json",
        )


class StaticSetSource:
    identity = "canonical-set-fixture"
    version = "1.0"

    def __init__(self, material: PublicationObjectSet) -> None:
        self.material = material

    async def materialize(self, _intent: EffectIntent) -> PublicationObjectSet:
        return self.material


class MemoryWriter:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.puts: list[dict[str, object]] = []

    async def read_optional_bytes(
        self, *, bucket: str, object_key: str, max_bytes: int
    ) -> bytes | None:
        assert bucket == BUCKET
        value = self.objects.get(object_key)
        if value is not None and len(value) > max_bytes:
            raise AssertionError("test object exceeded the requested bound")
        return value

    async def put_bytes(self, **arguments: object) -> None:
        self.puts.append(arguments)
        object_key = cast(str, arguments["object_key"])
        payload = cast(bytes, arguments["payload"])
        if arguments.get("if_none_match") == "*" and object_key in self.objects:
            raise R2ImmutableConflictError("conditional conflict")
        self.objects[object_key] = payload

    async def list_keys(
        self, *, bucket: str, prefix: str, max_keys: int = 1_000
    ) -> tuple[str, ...]:
        assert bucket == BUCKET
        return tuple(sorted(key for key in self.objects if key.startswith(prefix)))[:max_keys]


def _intent(step: PublicationStepName, destination: str) -> EffectIntent:
    return EffectIntent(
        publication_id=PUBLICATION_ID,
        workflow_version="1.0",
        workflow_revision=1,
        step=step,
        destination=destination,
        approved_payload_digest="a" * 64,
        idempotency_key="b" * 64,
        forge_target="github:RujitRaval/opennosh",
    )


@pytest.mark.asyncio
async def test_r2_object_adapter_observes_then_conditionally_creates_and_reads_back() -> None:
    writer = MemoryWriter()
    material = PublicationObject(
        object_key="durability/git/" + "c" * 40 + ".json",
        payload=b'{"commit":"' + b"c" * 40 + b'"}',
        media_type="application/json",
        context={"merged_tree_digest": "d" * 64},
    )
    source = StaticSource(material)
    destination = "urn:opennosh:durability:git"
    adapter = R2ImmutablePublicationAdapter(
        step=PublicationStepName.COPY_COMMIT,
        destination=destination,
        source=source,
        writer=cast(S3R2ObjectWriter, writer),
        bucket=BUCKET,
        clock=lambda: NOW,
    )
    intent = _intent(PublicationStepName.COPY_COMMIT, destination)

    assert (await adapter.observe(intent)).status is ObservationStatus.ABSENT
    await adapter.apply(intent)
    observation = await adapter.observe(intent)

    assert observation.status is ObservationStatus.VERIFIED
    assert observation.content_digest == hashlib.sha256(material.payload).hexdigest()
    assert observation.external_reference == f"r2://{BUCKET}/{material.object_key}"
    assert observation.context["merged_tree_digest"] == "d" * 64
    assert writer.puts == [
        {
            "bucket": BUCKET,
            "object_key": material.object_key,
            "payload": material.payload,
            "media_type": "application/json",
            "cache_control": "public, max-age=31536000, immutable",
            "if_none_match": "*",
        }
    ]


@pytest.mark.asyncio
async def test_r2_object_adapter_quarantines_conflicting_immutable_bytes() -> None:
    writer = MemoryWriter()
    material = PublicationObject(
        object_key="durability/evidence/" + "d" * 64 + ".json",
        payload=b'{"evidence":"expected"}',
        media_type="application/json",
        context={},
    )
    writer.objects[material.object_key] = b'{"evidence":"different"}'
    destination = "urn:opennosh:durability:evidence"
    adapter = R2ImmutablePublicationAdapter(
        step=PublicationStepName.COPY_EVIDENCE,
        destination=destination,
        source=StaticSource(material),
        writer=cast(S3R2ObjectWriter, writer),
        bucket=BUCKET,
        clock=lambda: NOW,
    )

    observation = await adapter.observe(
        _intent(PublicationStepName.COPY_EVIDENCE, destination)
    )

    assert observation.status is ObservationStatus.CONFLICT
    assert observation.code == "immutable_r2_object_conflict"
    assert not writer.puts


@pytest.mark.asyncio
async def test_r2_object_set_resumes_partial_release_and_verifies_inventory() -> None:
    writer = MemoryWriter()
    first = PublicationObject(
        object_key="records/v1/" + "1" * 64 + ".json",
        payload=b'{"record":1}',
        media_type="application/json",
        context={},
    )
    second = PublicationObject(
        object_key="releases/v1/release-1.0.0.1.json",
        payload=b'{"release":1}',
        media_type="application/vnd.opennosh.release+json",
        context={},
    )
    material = PublicationObjectSet(
        objects=(first, second),
        context={"release_version": "1.0.0.1"},
    )
    writer.objects[first.object_key] = first.payload
    destination = "urn:opennosh:commons:release"
    adapter = R2ImmutablePublicationAdapter(
        step=PublicationStepName.PUBLISH_RELEASE,
        destination=destination,
        source=StaticSetSource(material),
        writer=cast(S3R2ObjectWriter, writer),
        bucket=BUCKET,
        clock=lambda: NOW,
    )
    intent = _intent(PublicationStepName.PUBLISH_RELEASE, destination)

    assert (await adapter.observe(intent)).status is ObservationStatus.ABSENT
    await adapter.apply(intent)
    observed = await adapter.observe(intent)

    assert observed.status is ObservationStatus.VERIFIED
    assert observed.content_digest == material.digest
    assert observed.context["object_keys"] == [first.object_key, second.object_key]
    assert [item["object_key"] for item in writer.puts] == [second.object_key]


@pytest.mark.asyncio
async def test_r2_receipt_store_uses_conditional_create_and_readback() -> None:
    writer = MemoryWriter()
    store = R2PublicationReceiptStore(
        writer=cast(S3R2ObjectWriter, writer),
        bucket=BUCKET,
        destination="urn:opennosh:durability:receipt",
        list_prefix="durability/receipts",
    )
    payload = b'{"receipt":"signed"}'
    digest = hashlib.sha256(payload).hexdigest()
    object_key = f"durability/receipts/{digest}.json"

    await store.put_immutable(object_key, payload, expected_digest=digest)
    await store.put_immutable(object_key, payload, expected_digest=digest)
    observation = await store.observe(object_key)

    assert observation is not None
    assert observation.receipt_digest == digest
    assert await store.list_keys() == (object_key,)
    assert len(writer.puts) == 1


def test_publication_object_rejects_noncanonical_or_empty_material() -> None:
    with pytest.raises(ValueError, match="key"):
        PublicationObject(
            object_key="../secret",
            payload=b"payload",
            media_type="application/json",
            context={},
        )
    with pytest.raises(ValueError, match="empty"):
        PublicationObject(
            object_key="durability/evidence/item.json",
            payload=b"",
            media_type="application/json",
            context={},
        )
    with pytest.raises(ValueError, match="media type"):
        PublicationObject(
            object_key="durability/evidence/item.json",
            payload=b"payload",
            media_type="",
            context={},
        )


@pytest.mark.asyncio
async def test_release_manifest_source_signs_and_self_verifies_with_online_role() -> None:
    signing_key = Ed25519PrivateKey.from_private_bytes(b"m" * 32)
    source = Ed25519ReleaseManifestSource(
        source=StaticManifestSource(),
        key_id="manifest-online",
        signing_key=signing_key,
    )

    material = await source.materialize(
        _intent(
            PublicationStepName.SIGN_RELEASE,
            "github:RujitRaval/opennosh#release-signature",
        )
    )
    envelope = SignedEnvelope.model_validate_json(material.payload)
    ManifestKeyRing.from_config(
        f"manifest-online:{public_key_text(signing_key)}"
    ).verify(envelope)

    assert material.object_key == "signatures/releases/v1/release-0.60.0.0.json"
    assert material.context == {
        "release_version": "0.60.0.0",
        "signature_key_id": "manifest-online",
    }


@pytest.mark.asyncio
async def test_r2_object_adapter_maps_observe_apply_race_to_terminal_conflict() -> None:
    writer = MemoryWriter()
    material = PublicationObject(
        object_key="durability/evidence/" + "d" * 64 + ".json",
        payload=b'{"evidence":"expected"}',
        media_type="application/json",
        context={},
    )
    destination = "urn:opennosh:durability:evidence"
    adapter = R2ImmutablePublicationAdapter(
        step=PublicationStepName.COPY_EVIDENCE,
        destination=destination,
        source=StaticSource(material),
        writer=cast(S3R2ObjectWriter, writer),
        bucket=BUCKET,
        clock=lambda: NOW,
    )
    writer.objects[material.object_key] = b'{"evidence":"different"}'

    with pytest.raises(PublicationEffectError) as captured:
        await adapter.apply(_intent(PublicationStepName.COPY_EVIDENCE, destination))

    assert captured.value.status is ObservationStatus.CONFLICT
