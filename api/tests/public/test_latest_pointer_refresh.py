from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from datetime import timedelta
from typing import cast

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from opennosh_api.public.artifacts import (
    MAX_POINTER_BYTES,
    MemoryArtifactStore,
    PublicReadLatestPointer,
    PublicReadReleaseManifest,
    artifact_descriptor,
)
from opennosh_api.public.refresh import (
    LATEST_POINTER_OBJECT_KEY,
    MAX_R2_OPERATIONS_PER_REFRESH,
    LatestPointerRefreshError,
    LatestPointerRefreshResult,
    LatestPointerRefreshService,
    run_latest_pointer_refresh_loop,
)
from opennosh_api.public.signing import sign_envelope
from opennosh_api.public_commons.manifests import ManifestKeyRing, SignedEnvelope
from opennosh_api.publication.receipts import (
    Ed25519ReceiptSigner,
    PublicationReceiptKeyRing,
    canonical_signed_receipt_bytes,
    receipt_draft_from_snapshot,
)
from opennosh_api.publication.state import PublicationStepName
from tests.publication.test_planner import NOW, snapshot

RELEASE = "0.56.0.0"
OFFLINE_KEY = Ed25519PrivateKey.from_private_bytes(b"m" * 32)
ONLINE_KEY = Ed25519PrivateKey.from_private_bytes(b"o" * 32)
RECEIPT_KEY = Ed25519PrivateKey.from_private_bytes(b"r" * 32)
MANIFEST_KEYS = ManifestKeyRing(
    {
        "manifest-offline": OFFLINE_KEY.public_key(),
        "manifest-online": ONLINE_KEY.public_key(),
    }
)
RECEIPT_KEYS = PublicationReceiptKeyRing({"receipt-production": RECEIPT_KEY.public_key()})


class MemoryPointerWriter:
    def __init__(
        self,
        remote: dict[str, bytes],
        *,
        mismatch_readback: bool = False,
        fail_write: bool = False,
        mutate_after_readback: tuple[str, bytes] | None = None,
        replace_before_put: bytes | None = None,
    ) -> None:
        self.remote = remote
        self.mismatch_readback = mismatch_readback
        self.fail_write = fail_write
        self.mutate_after_readback = mutate_after_readback
        self.replace_before_put = replace_before_put
        self.has_written = False
        self.calls: list[tuple[str, str, str]] = []
        self.read_calls: list[str] = []

    async def put_bytes(
        self,
        *,
        bucket: str,
        object_key: str,
        payload: bytes,
        media_type: str,
        cache_control: str,
        if_match: str | None = None,
    ) -> None:
        del media_type
        self.calls.append((bucket, object_key, cache_control))
        if self.fail_write:
            raise OSError("simulated R2 outage")
        if self.replace_before_put is not None:
            self.remote[object_key] = self.replace_before_put
            self.replace_before_put = None
        current_etag = hashlib.sha256(self.remote[object_key]).hexdigest()
        if if_match != current_etag:
            raise LatestPointerRefreshError("simulated conditional write conflict")
        self.remote[object_key] = payload
        self.has_written = True

    async def read_bytes(self, *, bucket: str, object_key: str, max_bytes: int) -> bytes:
        del bucket
        self.read_calls.append(object_key)
        payload = self.remote[object_key]
        assert len(payload) <= max_bytes
        if self.mutate_after_readback is not None and self.has_written:
            key, replacement = self.mutate_after_readback
            self.remote[key] = replacement
        return b"tampered" if self.mismatch_readback and self.has_written else payload

    async def read_revision(
        self,
        *,
        bucket: str,
        object_key: str,
        max_bytes: int,
    ) -> tuple[bytes, str]:
        payload = await self.read_bytes(
            bucket=bucket,
            object_key=object_key,
            max_bytes=max_bytes,
        )
        return payload, hashlib.sha256(payload).hexdigest()


def _signed_release() -> tuple[MemoryArtifactStore, PublicReadLatestPointer]:
    manifest = PublicReadReleaseManifest(
        release_version=RELEASE,
        published_at=NOW,
        publication_receipt_key="receipts/v1/11111111-1111-4111-8111-111111111111.json",
    )
    manifest_bytes = sign_envelope(
        manifest.model_dump(mode="json"),
        key_id="manifest-offline",
        private_key=OFFLINE_KEY,
    )
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
    receipt = Ed25519ReceiptSigner(
        key_id="receipt-production",
        publisher_identity="opennosh:refresh-test",
        private_key=RECEIPT_KEY,
    ).sign(receipt_draft_from_snapshot(replace(source, acknowledgements=acknowledgements)))
    receipt_bytes = canonical_signed_receipt_bytes(receipt)
    descriptor = artifact_descriptor(
        f"releases/v1/release-{RELEASE}.json",
        manifest_bytes,
        "application/vnd.opennosh.release+json",
    )
    pointer = PublicReadLatestPointer(
        release_version=RELEASE,
        manifest=descriptor,
        issued_at=NOW,
        expires_at=NOW + timedelta(hours=23),
    )
    pointer_bytes = sign_envelope(
        pointer.model_dump(mode="json"),
        key_id="manifest-offline",
        private_key=OFFLINE_KEY,
    )
    store = MemoryArtifactStore()
    store.objects.update(
        {
            descriptor.object_key: manifest_bytes,
            manifest.publication_receipt_key: receipt_bytes,
            LATEST_POINTER_OBJECT_KEY: pointer_bytes,
        }
    )
    return store, pointer


def _service(
    store: MemoryArtifactStore,
    writer: MemoryPointerWriter,
    *,
    manifest_keys: ManifestKeyRing = MANIFEST_KEYS,
) -> LatestPointerRefreshService:
    return LatestPointerRefreshService(
        origin=store,
        writer=writer,
        bucket="opennosh-public-commons",
        manifest_keys=manifest_keys,
        receipt_keys=RECEIPT_KEYS,
        signing_key_id="manifest-online",
        signing_key=ONLINE_KEY,
        refresh_after_seconds=72_000,
        pointer_lifetime_seconds=82_800,
        origin_timeout_seconds=2,
    )


@pytest.mark.asyncio
async def test_refresh_preserves_immutable_identity_and_advances_only_latest() -> None:
    store, previous = _signed_release()
    writer = MemoryPointerWriter(store.objects)
    service = _service(store, writer)

    result = await service.refresh(now=NOW + timedelta(hours=20))

    assert result.refreshed is True
    assert result.release_version == RELEASE
    assert result.manifest_digest == previous.manifest.digest
    assert result.previous_expires_at == previous.expires_at
    assert result.current_expires_at == NOW + timedelta(hours=43)
    assert writer.calls == [
        (
            "opennosh-public-commons",
            LATEST_POINTER_OBJECT_KEY,
            "public, max-age=0, must-revalidate",
        )
    ]
    assert writer.read_calls == [
        LATEST_POINTER_OBJECT_KEY,
        previous.manifest.object_key,
        "receipts/v1/11111111-1111-4111-8111-111111111111.json",
        LATEST_POINTER_OBJECT_KEY,
        previous.manifest.object_key,
        "receipts/v1/11111111-1111-4111-8111-111111111111.json",
    ]
    assert len(writer.read_calls) + len(writer.calls) == MAX_R2_OPERATIONS_PER_REFRESH
    envelope = SignedEnvelope.model_validate_json(store.objects[LATEST_POINTER_OBJECT_KEY])
    refreshed = PublicReadLatestPointer.model_validate(envelope.payload)
    assert envelope.key_id == "manifest-online"
    assert refreshed.release_version == previous.release_version
    assert refreshed.manifest == previous.manifest
    assert refreshed.issued_at == NOW + timedelta(hours=20)
    assert len(store.objects[LATEST_POINTER_OBJECT_KEY]) <= MAX_POINTER_BYTES


@pytest.mark.asyncio
async def test_refresh_accepts_a_genuinely_legacy_pointer_without_issued_at() -> None:
    store, pointer = _signed_release()
    legacy_payload = pointer.model_dump(mode="json", exclude={"issued_at"})
    assert "issued_at" not in legacy_payload
    store.objects[LATEST_POINTER_OBJECT_KEY] = sign_envelope(
        legacy_payload,
        key_id="manifest-offline",
        private_key=OFFLINE_KEY,
    )
    writer = MemoryPointerWriter(store.objects)
    service = _service(store, writer)

    result = await service.refresh(now=NOW + timedelta(hours=20))

    assert result.refreshed is True
    assert result.previous_expires_at == pointer.expires_at


@pytest.mark.asyncio
async def test_refresh_skips_before_the_twenty_hour_threshold() -> None:
    store, previous = _signed_release()
    writer = MemoryPointerWriter(store.objects)
    service = _service(store, writer)

    result = await service.refresh(now=NOW + timedelta(hours=19))

    assert result.refreshed is False
    assert result.current_expires_at == previous.expires_at
    assert result.signing_key_id == "manifest-offline"
    assert writer.calls == []


@pytest.mark.asyncio
async def test_refresh_never_overwrites_a_concurrent_newer_pointer() -> None:
    store, pointer = _signed_release()
    newer = pointer.model_copy(
        update={
            "issued_at": NOW + timedelta(hours=21),
            "expires_at": NOW + timedelta(hours=44),
        }
    )
    newer_bytes = sign_envelope(
        newer.model_dump(mode="json"),
        key_id="manifest-online",
        private_key=ONLINE_KEY,
    )
    writer = MemoryPointerWriter(store.objects, replace_before_put=newer_bytes)
    service = _service(store, writer)

    with pytest.raises(LatestPointerRefreshError, match="conditional write conflict"):
        await service.refresh(now=NOW + timedelta(hours=20))

    assert store.objects[LATEST_POINTER_OBJECT_KEY] == newer_bytes


@pytest.mark.asyncio
async def test_refresh_rejects_a_stale_public_origin_before_write() -> None:
    origin, pointer = _signed_release()
    remote = dict(origin.objects)
    newer = pointer.model_copy(
        update={
            "issued_at": NOW + timedelta(hours=21),
            "expires_at": NOW + timedelta(hours=44),
        }
    )
    remote[LATEST_POINTER_OBJECT_KEY] = sign_envelope(
        newer.model_dump(mode="json"),
        key_id="manifest-online",
        private_key=ONLINE_KEY,
    )
    writer = MemoryPointerWriter(remote)
    service = _service(origin, writer)

    with pytest.raises(LatestPointerRefreshError, match="does not match the current R2"):
        await service.refresh(now=NOW + timedelta(hours=20))

    assert writer.calls == []


@pytest.mark.asyncio
async def test_refresh_rejects_a_publicly_cached_manifest_that_differs_from_r2() -> None:
    origin, pointer = _signed_release()
    remote = dict(origin.objects)
    remote[pointer.manifest.object_key] += b"corrupted in durable R2"
    writer = MemoryPointerWriter(remote)
    service = _service(origin, writer)

    with pytest.raises(LatestPointerRefreshError, match="direct R2 immutable release"):
        await service.refresh(now=NOW + timedelta(hours=20))

    assert writer.calls == []


@pytest.mark.asyncio
async def test_refresh_rejects_a_publicly_cached_receipt_that_differs_from_r2() -> None:
    origin, _ = _signed_release()
    remote = dict(origin.objects)
    receipt_key = next(key for key in remote if key.startswith("receipts/"))
    remote[receipt_key] += b"corrupted in durable R2"
    writer = MemoryPointerWriter(remote)
    service = _service(origin, writer)

    with pytest.raises(LatestPointerRefreshError, match="direct R2 immutable release"):
        await service.refresh(now=NOW + timedelta(hours=20))

    assert writer.calls == []


@pytest.mark.asyncio
async def test_refresh_fails_closed_when_r2_readback_differs() -> None:
    store, _ = _signed_release()
    writer = MemoryPointerWriter(store.objects, mismatch_readback=True)
    service = _service(store, writer)

    with pytest.raises(LatestPointerRefreshError, match="readback did not match"):
        await service.refresh(now=NOW + timedelta(hours=20))


@pytest.mark.asyncio
async def test_refresh_propagates_write_failure_without_replacing_latest() -> None:
    store, _ = _signed_release()
    original = store.objects[LATEST_POINTER_OBJECT_KEY]
    writer = MemoryPointerWriter(store.objects, fail_write=True)
    service = _service(store, writer)

    with pytest.raises(OSError, match="simulated R2 outage"):
        await service.refresh(now=NOW + timedelta(hours=20))

    assert store.objects[LATEST_POINTER_OBJECT_KEY] == original


@pytest.mark.asyncio
async def test_prior_noop_never_caches_receipt_authority_for_a_later_write() -> None:
    store, _ = _signed_release()
    writer = MemoryPointerWriter(store.objects)
    service = _service(store, writer)
    first = await service.refresh(now=NOW + timedelta(hours=19))
    assert first.refreshed is False

    receipt_key = next(key for key in store.objects if key.startswith("receipts/"))
    store.objects[receipt_key] += b"tamper after prior verification"
    with pytest.raises(LatestPointerRefreshError, match="direct R2 immutable release"):
        await service.refresh(now=NOW + timedelta(hours=20))

    assert writer.calls == []


@pytest.mark.asyncio
async def test_cached_manifest_never_authorizes_a_write_after_live_tamper() -> None:
    store, pointer = _signed_release()
    writer = MemoryPointerWriter(store.objects)
    service = _service(store, writer)
    first = await service.refresh(now=NOW + timedelta(hours=19))
    assert first.refreshed is False

    store.objects[pointer.manifest.object_key] += b"tamper after cache warm"
    with pytest.raises(LatestPointerRefreshError, match="direct R2 immutable release"):
        await service.refresh(now=NOW + timedelta(hours=20))

    assert writer.calls == []


@pytest.mark.asyncio
async def test_refresh_rechecks_the_manifest_after_pointer_readback() -> None:
    store, pointer = _signed_release()
    writer = MemoryPointerWriter(
        store.objects,
        mutate_after_readback=(pointer.manifest.object_key, b"changed after pointer write"),
    )
    service = _service(store, writer)

    with pytest.raises(LatestPointerRefreshError, match="release changed"):
        await service.refresh(now=NOW + timedelta(hours=20))


@pytest.mark.asyncio
async def test_refresh_rejects_an_invalid_signed_pointer_window() -> None:
    store, pointer = _signed_release()
    invalid = pointer.model_copy(update={"expires_at": NOW - timedelta(minutes=1)})
    store.objects[LATEST_POINTER_OBJECT_KEY] = sign_envelope(
        invalid.model_dump(mode="json"),
        key_id="manifest-offline",
        private_key=OFFLINE_KEY,
    )
    writer = MemoryPointerWriter(store.objects)
    service = _service(store, writer)

    with pytest.raises(LatestPointerRefreshError, match="invalid bounded lifetime"):
        await service.refresh(now=NOW + timedelta(hours=20))

    assert writer.calls == []


@pytest.mark.asyncio
async def test_refresh_requires_the_online_key_in_the_trusted_ring_before_write() -> None:
    store, _ = _signed_release()
    writer = MemoryPointerWriter(store.objects)
    service = _service(
        store,
        writer,
        manifest_keys=ManifestKeyRing({"manifest-offline": OFFLINE_KEY.public_key()}),
    )

    with pytest.raises(LatestPointerRefreshError, match="cryptographically trusted"):
        await service.refresh(now=NOW + timedelta(hours=20))
    assert writer.calls == []


@pytest.mark.asyncio
async def test_public_origin_pointer_read_has_an_absolute_deadline() -> None:
    store, _ = _signed_release()
    writer = MemoryPointerWriter(store.objects)
    service = LatestPointerRefreshService(
        origin=store,
        writer=writer,
        bucket="opennosh-public-commons",
        manifest_keys=MANIFEST_KEYS,
        receipt_keys=RECEIPT_KEYS,
        signing_key_id="manifest-online",
        signing_key=ONLINE_KEY,
        refresh_after_seconds=72_000,
        pointer_lifetime_seconds=82_800,
        origin_timeout_seconds=0.001,
    )
    release = asyncio.Event()

    async def never_returns(object_key: str, *, max_bytes: int) -> bytes | None:
        del object_key, max_bytes
        await release.wait()
        return None

    store.read = never_returns  # type: ignore[method-assign]

    with pytest.raises(LatestPointerRefreshError, match="absolute deadline"):
        await service.refresh(now=NOW + timedelta(hours=20))

    assert writer.read_calls == []


@pytest.mark.asyncio
async def test_refresh_loop_finishes_active_refresh_then_closes_on_shutdown() -> None:
    shutdown = asyncio.Event()
    started = asyncio.Event()
    release = asyncio.Event()

    class ActiveService:
        def __init__(self) -> None:
            self.calls = 0
            self.closed = False

        async def refresh(self) -> LatestPointerRefreshResult:
            self.calls += 1
            started.set()
            await release.wait()
            return LatestPointerRefreshResult(
                refreshed=False,
                release_version=RELEASE,
                manifest_digest="a" * 64,
                previous_expires_at=NOW + timedelta(hours=23),
                current_expires_at=NOW + timedelta(hours=23),
                signing_key_id="manifest-offline",
                pointer_digest="b" * 64,
            )

        async def aclose(self) -> None:
            self.closed = True

    service = ActiveService()
    task = asyncio.create_task(
        run_latest_pointer_refresh_loop(
            cast(LatestPointerRefreshService, service),
            shutdown,
            interval_seconds=1,
        )
    )
    await started.wait()
    shutdown.set()
    release.set()
    await task

    assert service.calls == 1
    assert service.closed is True


@pytest.mark.asyncio
async def test_refresh_loop_runs_on_cadence_and_propagates_failure_after_closing() -> None:
    shutdown = asyncio.Event()

    class FailingService:
        def __init__(self) -> None:
            self.calls = 0
            self.closed = False

        async def refresh(self) -> LatestPointerRefreshResult:
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("simulated refresh failure")
            return LatestPointerRefreshResult(
                refreshed=False,
                release_version=RELEASE,
                manifest_digest="a" * 64,
                previous_expires_at=NOW + timedelta(hours=23),
                current_expires_at=NOW + timedelta(hours=23),
                signing_key_id="manifest-offline",
                pointer_digest="b" * 64,
            )

        async def aclose(self) -> None:
            self.closed = True

    service = FailingService()
    with pytest.raises(RuntimeError, match="simulated refresh failure"):
        await run_latest_pointer_refresh_loop(
            cast(LatestPointerRefreshService, service),
            shutdown,
            interval_seconds=0.001,
        )

    assert service.calls == 2
    assert service.closed is True


@pytest.mark.asyncio
async def test_refresh_rejects_a_manifest_that_no_longer_matches_latest() -> None:
    store, pointer = _signed_release()
    store.objects[pointer.manifest.object_key] += b"tamper"
    writer = MemoryPointerWriter(store.objects)
    service = _service(store, writer)

    with pytest.raises(LatestPointerRefreshError, match="direct R2 immutable release"):
        await service.refresh(now=NOW + timedelta(hours=20))
    assert writer.calls == []


@pytest.mark.parametrize(
    ("refresh_after_seconds", "origin_timeout_seconds", "message"),
    [
        (0, 2, "Latest pointer timing"),
        (72_000, 0, "Public origin timeout"),
    ],
)
def test_refresh_service_rejects_invalid_timing_configuration(
    refresh_after_seconds: int,
    origin_timeout_seconds: float,
    message: str,
) -> None:
    store, _ = _signed_release()
    writer = MemoryPointerWriter(store.objects)

    with pytest.raises(ValueError, match=message):
        LatestPointerRefreshService(
            origin=store,
            writer=writer,
            bucket="opennosh-public-commons",
            manifest_keys=MANIFEST_KEYS,
            receipt_keys=RECEIPT_KEYS,
            signing_key_id="manifest-online",
            signing_key=ONLINE_KEY,
            refresh_after_seconds=refresh_after_seconds,
            pointer_lifetime_seconds=82_800,
            origin_timeout_seconds=origin_timeout_seconds,
        )


@pytest.mark.asyncio
async def test_refresh_rejects_a_missing_public_pointer_before_r2_reads() -> None:
    store, _ = _signed_release()
    del store.objects[LATEST_POINTER_OBJECT_KEY]
    writer = MemoryPointerWriter(store.objects)

    with pytest.raises(LatestPointerRefreshError, match="current latest pointer is missing"):
        await _service(store, writer).refresh(now=NOW)

    assert writer.read_calls == []


@pytest.mark.asyncio
async def test_refresh_rejects_worker_clock_skew_before_write() -> None:
    store, _ = _signed_release()
    writer = MemoryPointerWriter(store.objects)

    with pytest.raises(LatestPointerRefreshError, match="worker clock is behind"):
        await _service(store, writer).refresh(now=NOW - timedelta(seconds=1))

    assert writer.calls == []


@pytest.mark.asyncio
async def test_refresh_loop_rejects_nonpositive_cadence() -> None:
    store, _ = _signed_release()
    service = _service(store, MemoryPointerWriter(store.objects))

    with pytest.raises(ValueError, match="interval must be positive"):
        await run_latest_pointer_refresh_loop(service, asyncio.Event(), interval_seconds=0)
