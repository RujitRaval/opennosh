"""Refresh-only publication of a signed pointer to one verified immutable release."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from opennosh_api.public.artifacts import (
    MAX_POINTER_BYTES,
    ArtifactReadError,
    ArtifactStore,
    PublicArtifactReadService,
    PublicReadLatestPointer,
)
from opennosh_api.public.signing import sign_envelope
from opennosh_api.public_commons.manifests import (
    ManifestKeyRing,
    ManifestVerificationError,
    SignedEnvelope,
    canonical_json,
)
from opennosh_api.publication.receipts import PublicationReceiptKeyRing

LATEST_POINTER_OBJECT_KEY = "latest/v1.json"
LATEST_POINTER_MEDIA_TYPE = "application/vnd.opennosh.latest+json"
LATEST_POINTER_CACHE_CONTROL = "public, max-age=0, must-revalidate"
MAX_R2_OPERATIONS_PER_REFRESH = 7
logger = logging.getLogger(__name__)


class LatestPointerRefreshError(RuntimeError):
    pass


class LatestPointerWriter(Protocol):
    async def put_bytes(
        self,
        *,
        bucket: str,
        object_key: str,
        payload: bytes,
        media_type: str,
        cache_control: str,
        if_match: str | None = None,
    ) -> None: ...

    async def read_bytes(self, *, bucket: str, object_key: str, max_bytes: int) -> bytes: ...

    async def read_revision(
        self,
        *,
        bucket: str,
        object_key: str,
        max_bytes: int,
    ) -> tuple[bytes, str]: ...


@dataclass(frozen=True, slots=True)
class LatestPointerRefreshResult:
    refreshed: bool
    release_version: str
    manifest_digest: str
    previous_expires_at: datetime
    current_expires_at: datetime
    signing_key_id: str
    pointer_digest: str


class _DirectR2ArtifactStore:
    """Read immutable release authority directly from R2, bypassing public caches."""

    def __init__(self, writer: LatestPointerWriter, bucket: str) -> None:
        self._writer = writer
        self._bucket = bucket
        self.observations: dict[str, bytes] = {}

    async def read(self, object_key: str, *, max_bytes: int) -> bytes | None:
        payload = await self._writer.read_bytes(
            bucket=self._bucket,
            object_key=object_key,
            max_bytes=max_bytes,
        )
        self.observations[object_key] = payload
        return payload

    async def put_immutable(
        self,
        object_key: str,
        payload: bytes,
        *,
        expected_digest: str,
    ) -> None:
        del object_key, payload, expected_digest
        raise LatestPointerRefreshError("The direct R2 verifier is read-only")

    async def replace_pointer(self, object_key: str, payload: bytes) -> None:
        del object_key, payload
        raise LatestPointerRefreshError("The direct R2 verifier is read-only")

    async def aclose(self) -> None:
        return None


class LatestPointerRefreshService:
    """Verify the selected release, then re-sign only its mutable latest pointer."""

    def __init__(
        self,
        *,
        origin: ArtifactStore,
        writer: LatestPointerWriter,
        bucket: str,
        manifest_keys: ManifestKeyRing,
        receipt_keys: PublicationReceiptKeyRing,
        signing_key_id: str,
        signing_key: Ed25519PrivateKey,
        refresh_after_seconds: int,
        pointer_lifetime_seconds: int,
        origin_timeout_seconds: float,
    ) -> None:
        if not 0 < refresh_after_seconds < pointer_lifetime_seconds <= 86_400:
            raise ValueError("Latest pointer timing must refresh before a maximum 24-hour expiry")
        if not 0 < origin_timeout_seconds <= 5:
            raise ValueError("Public origin timeout must be between zero and five seconds")
        self._origin = origin
        self._origin_timeout_seconds = origin_timeout_seconds
        self._writer = writer
        self._bucket = bucket
        self._manifest_keys = manifest_keys
        self._receipt_keys = receipt_keys
        self._signing_key_id = signing_key_id
        self._signing_key = signing_key
        self._refresh_after = timedelta(seconds=refresh_after_seconds)
        self._pointer_lifetime = timedelta(seconds=pointer_lifetime_seconds)
        self._direct_store = _DirectR2ArtifactStore(writer, bucket)
        self._reader = PublicArtifactReadService(
            store=self._direct_store,
            manifest_keys=manifest_keys,
            receipt_keys=receipt_keys,
            max_cached_releases=0,
        )

    async def refresh(self, *, now: datetime | None = None) -> LatestPointerRefreshResult:
        current_time = (now or datetime.now(UTC)).astimezone(UTC)
        try:
            async with asyncio.timeout(self._origin_timeout_seconds):
                pointer_bytes = await self._origin.read(
                    LATEST_POINTER_OBJECT_KEY,
                    max_bytes=MAX_POINTER_BYTES,
                )
        except TimeoutError as error:
            raise LatestPointerRefreshError(
                "The public latest pointer read exceeded its absolute deadline"
            ) from error
        if pointer_bytes is None:
            raise LatestPointerRefreshError("The current latest pointer is missing")
        pointer_envelope, pointer = _verified_pointer(pointer_bytes, self._manifest_keys)
        r2_pointer_bytes, r2_pointer_etag = await self._writer.read_revision(
            bucket=self._bucket,
            object_key=LATEST_POINTER_OBJECT_KEY,
            max_bytes=MAX_POINTER_BYTES,
        )
        if r2_pointer_bytes != pointer_bytes:
            raise LatestPointerRefreshError(
                "The public origin does not match the current R2 latest pointer"
            )
        try:
            release = await self._reader.resolve_release(
                release_version=pointer.release_version
            )
        except ArtifactReadError as error:
            raise LatestPointerRefreshError(
                "The direct R2 immutable release is not cryptographically trusted"
            ) from error
        live_manifest_bytes = release.manifest_bytes
        if (
            len(live_manifest_bytes) != pointer.manifest.size_bytes
            or hashlib.sha256(live_manifest_bytes).hexdigest() != pointer.manifest.digest
        ):
            raise LatestPointerRefreshError(
                "The latest pointer does not bind the direct R2 immutable manifest"
            )
        receipt_key = release.manifest.publication_receipt_key
        live_receipt_bytes = self._direct_store.observations.get(receipt_key)
        if live_receipt_bytes is None:
            raise LatestPointerRefreshError(
                "The direct R2 publication receipt was not verified"
            )

        issued_at = pointer.issued_at or release.manifest.published_at
        if not (
            release.manifest.published_at
            <= issued_at
            < pointer.expires_at
            <= issued_at + timedelta(hours=24)
        ):
            raise LatestPointerRefreshError("The latest pointer has an invalid bounded lifetime")
        if current_time < issued_at:
            raise LatestPointerRefreshError("The worker clock is behind the signed latest pointer")

        existing_digest = hashlib.sha256(pointer_bytes).hexdigest()
        if current_time < issued_at + self._refresh_after and current_time < pointer.expires_at:
            return LatestPointerRefreshResult(
                refreshed=False,
                release_version=pointer.release_version,
                manifest_digest=pointer.manifest.digest,
                previous_expires_at=pointer.expires_at,
                current_expires_at=pointer.expires_at,
                signing_key_id=pointer_envelope.key_id,
                pointer_digest=existing_digest,
            )

        candidate = PublicReadLatestPointer(
            release_version=pointer.release_version,
            manifest=pointer.manifest,
            issued_at=current_time,
            expires_at=current_time + self._pointer_lifetime,
        )
        candidate_bytes = sign_envelope(
            candidate.model_dump(mode="json"),
            key_id=self._signing_key_id,
            private_key=self._signing_key,
        )
        _verified_pointer(candidate_bytes, self._manifest_keys)
        if candidate.expires_at <= pointer.expires_at:
            raise LatestPointerRefreshError("A refresh must advance the latest pointer expiry")

        await self._writer.put_bytes(
            bucket=self._bucket,
            object_key=LATEST_POINTER_OBJECT_KEY,
            payload=candidate_bytes,
            media_type=LATEST_POINTER_MEDIA_TYPE,
            cache_control=LATEST_POINTER_CACHE_CONTROL,
            if_match=r2_pointer_etag,
        )
        readback = await self._writer.read_bytes(
            bucket=self._bucket,
            object_key=LATEST_POINTER_OBJECT_KEY,
            max_bytes=MAX_POINTER_BYTES,
        )
        if readback != candidate_bytes:
            raise LatestPointerRefreshError("R2 latest pointer readback did not match the write")
        _, verified_readback = _verified_pointer(readback, self._manifest_keys)
        if (
            verified_readback.release_version != pointer.release_version
            or verified_readback.manifest != pointer.manifest
        ):
            raise LatestPointerRefreshError("R2 latest pointer readback changed immutable identity")
        try:
            release_readback = await self._reader.resolve_release(
                release_version=pointer.release_version
            )
        except ArtifactReadError as error:
            raise LatestPointerRefreshError(
                "The immutable release changed during latest pointer refresh"
            ) from error
        receipt_readback = self._direct_store.observations.get(receipt_key)
        if (
            release_readback.manifest_bytes != live_manifest_bytes
            or receipt_readback != live_receipt_bytes
        ):
            raise LatestPointerRefreshError(
                "The immutable release changed during latest pointer refresh"
            )
        return LatestPointerRefreshResult(
            refreshed=True,
            release_version=pointer.release_version,
            manifest_digest=pointer.manifest.digest,
            previous_expires_at=pointer.expires_at,
            current_expires_at=candidate.expires_at,
            signing_key_id=self._signing_key_id,
            pointer_digest=hashlib.sha256(candidate_bytes).hexdigest(),
        )

    async def aclose(self) -> None:
        await self._reader.aclose()
        await self._origin.aclose()


async def run_latest_pointer_refresh_loop(
    service: LatestPointerRefreshService,
    shutdown_requested: asyncio.Event,
    *,
    interval_seconds: float,
) -> None:
    """Run immediately, then on a bounded cadence; any failure exits for Render restart."""

    if interval_seconds <= 0:
        raise ValueError("Latest pointer refresh interval must be positive")
    try:
        while not shutdown_requested.is_set():
            result = await service.refresh()
            logger.info(
                "Latest pointer refresh completed",
                extra={
                    "refreshed": result.refreshed,
                    "release_version": result.release_version,
                    "manifest_digest": result.manifest_digest,
                    "pointer_digest": result.pointer_digest,
                    "current_expires_at": result.current_expires_at.isoformat(),
                    "signing_key_id": result.signing_key_id,
                },
            )
            try:
                await asyncio.wait_for(shutdown_requested.wait(), timeout=interval_seconds)
            except TimeoutError:
                continue
    finally:
        await service.aclose()


def _verified_pointer(
    payload: bytes,
    key_ring: ManifestKeyRing,
) -> tuple[SignedEnvelope, PublicReadLatestPointer]:
    try:
        value = json.loads(payload)
        envelope = SignedEnvelope.model_validate(value)
        key_ring.verify(envelope)
        if canonical_json(envelope.model_dump(mode="json")) != payload:
            raise LatestPointerRefreshError("The latest pointer is not canonical JSON")
        return envelope, PublicReadLatestPointer.model_validate(envelope.payload)
    except LatestPointerRefreshError:
        raise
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        RecursionError,
        ValueError,
        ManifestVerificationError,
    ) as error:
        raise LatestPointerRefreshError(
            "The latest pointer is not cryptographically trusted"
        ) from error
