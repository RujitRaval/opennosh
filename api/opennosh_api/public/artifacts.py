from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import re
import tempfile
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal, Protocol
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from opennosh_api.foods.schemas import FoodDetail, FoodSource
from opennosh_api.public_commons.manifests import (
    ManifestKeyRing,
    ManifestVerificationError,
    SignedEnvelope,
    canonical_json,
)
from opennosh_api.publication.receipts import (
    PublicationReceipt,
    PublicationReceiptKeyRing,
    ReceiptVerificationError,
    parse_signed_receipt,
)
from opennosh_api.publication.state import PublicationStepName

MAX_POINTER_BYTES = 16 * 1024
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_RECORD_BYTES = 512 * 1024
MAX_PROVENANCE_BYTES = 2 * 1024 * 1024
MAX_PACK_BYTES = 64 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OBJECT_KEY = re.compile(r"^[a-z0-9][a-z0-9/._-]{0,1023}$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$")


class ArtifactReadError(RuntimeError):
    """A requested artifact is absent, malformed, or not cryptographically trusted."""


class ArtifactNotFoundError(ArtifactReadError):
    pass


class ArtifactUnavailableError(ArtifactReadError):
    pass


class ArtifactConflictError(RuntimeError):
    pass


class ArtifactDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    object_key: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9/._-]{0,1023}$")]
    digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    size_bytes: Annotated[int, Field(ge=1, le=MAX_PACK_BYTES)]
    media_type: Annotated[str, Field(min_length=1, max_length=127)]


class PublicFoodArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source: FoodSource
    source_id: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
    record: ArtifactDescriptor
    provenance: ArtifactDescriptor

    @model_validator(mode="after")
    def require_safe_media_types(self) -> PublicFoodArtifact:
        if self.record.media_type != "application/json":
            raise ValueError("food record artifacts must be JSON")
        if self.record.size_bytes > MAX_RECORD_BYTES:
            raise ValueError("food record artifact is too large")
        if self.provenance.media_type != "text/html":
            raise ValueError("food provenance artifacts must be HTML")
        if self.provenance.size_bytes > MAX_PROVENANCE_BYTES:
            raise ValueError("food provenance artifact is too large")
        return self


class PublicPackArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    pack_id: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
    pack_version: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")]
    download: ArtifactDescriptor

    @model_validator(mode="after")
    def require_download_media_type(self) -> PublicPackArtifact:
        allowed = {"application/zip", "application/vnd.opennosh.pack+zip"}
        if self.download.media_type not in allowed:
            raise ValueError("pack artifact media type is not allowed")
        return self


class PublicReadReleaseManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1"] = "1"
    release_version: Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$")]
    published_at: datetime
    publication_receipt_key: Annotated[str, Field(pattern=r"^receipts/v1/[0-9a-f-]{36}\.json$")]
    foods: Annotated[tuple[PublicFoodArtifact, ...], Field(max_length=250_000)] = ()
    packs: Annotated[tuple[PublicPackArtifact, ...], Field(max_length=10_000)] = ()

    @field_validator("published_at")
    @classmethod
    def require_aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("published_at must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def require_unique_artifacts(self) -> PublicReadReleaseManifest:
        food_ids = tuple((food.source.value, food.source_id) for food in self.foods)
        pack_ids = tuple((pack.pack_id, pack.pack_version) for pack in self.packs)
        if food_ids != tuple(sorted(set(food_ids))):
            raise ValueError("foods must be sorted and unique")
        if pack_ids != tuple(sorted(set(pack_ids))):
            raise ValueError("packs must be sorted and unique")
        descriptors = [
            *(item.record for item in self.foods),
            *(item.provenance for item in self.foods),
            *(item.download for item in self.packs),
        ]
        if len({item.object_key for item in descriptors}) != len(descriptors):
            raise ValueError("artifact object keys must be unique")
        if any(item.digest not in item.object_key for item in descriptors):
            raise ValueError("release payload object keys must be content-addressed")
        return self


class PublicReadLatestPointer(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1"] = "1"
    release_version: Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$")]
    manifest: ArtifactDescriptor
    issued_at: datetime | None = None
    expires_at: datetime

    @field_validator("issued_at", "expires_at")
    @classmethod
    def require_aware_pointer_time(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("pointer timestamps must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def bind_release_manifest(self) -> PublicReadLatestPointer:
        expected_key = f"releases/v1/release-{self.release_version}.json"
        if self.manifest.object_key != expected_key:
            raise ValueError("latest pointer manifest key must match its release")
        if self.manifest.media_type != "application/vnd.opennosh.release+json":
            raise ValueError("latest pointer must reference a release manifest")
        if self.manifest.size_bytes > MAX_MANIFEST_BYTES:
            raise ValueError("release manifest is too large")
        return self


class PublicReleaseMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    release_version: str
    published_at: datetime
    state: Literal["verified", "stale"]
    stale_age_seconds: Annotated[int, Field(ge=0)] = 0


class PublicFoodRecordResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    record: FoodDetail
    release: PublicReleaseMetadata
    immutable_url: str
    provenance_url: str


@dataclass(frozen=True, slots=True)
class ResolvedRelease:
    manifest: PublicReadReleaseManifest
    manifest_envelope: SignedEnvelope
    manifest_bytes: bytes
    metadata: PublicReleaseMetadata


class ArtifactStore(Protocol):
    async def read(self, object_key: str, *, max_bytes: int) -> bytes | None: ...

    async def put_immutable(
        self, object_key: str, payload: bytes, *, expected_digest: str
    ) -> None: ...

    async def replace_pointer(self, object_key: str, payload: bytes) -> None: ...

    async def aclose(self) -> None: ...


class MemoryArtifactStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def read(self, object_key: str, *, max_bytes: int) -> bytes | None:
        _validate_object_key(object_key)
        value = self.objects.get(object_key)
        if value is not None and len(value) > max_bytes:
            raise ArtifactUnavailableError("artifact_too_large")
        return value

    async def put_immutable(self, object_key: str, payload: bytes, *, expected_digest: str) -> None:
        _validate_write(object_key, payload, expected_digest)
        existing = self.objects.get(object_key)
        if existing is not None and existing != payload:
            raise ArtifactConflictError("immutable_artifact_conflict")
        self.objects[object_key] = payload

    async def replace_pointer(self, object_key: str, payload: bytes) -> None:
        _validate_object_key(object_key)
        if len(payload) > MAX_POINTER_BYTES:
            raise ArtifactUnavailableError("latest_pointer_too_large")
        self.objects[object_key] = payload

    async def aclose(self) -> None:
        return None


class LocalArtifactStore:
    """Filesystem adapter for development and self-hosting; production uses object storage."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve(strict=False)

    async def read(self, object_key: str, *, max_bytes: int) -> bytes | None:
        return await asyncio.to_thread(self._read, object_key, max_bytes)

    def _read(self, object_key: str, max_bytes: int) -> bytes | None:
        path = self._path(object_key)
        try:
            with path.open("rb") as handle:
                payload = handle.read(max_bytes + 1)
        except FileNotFoundError:
            return None
        if len(payload) > max_bytes:
            raise ArtifactUnavailableError("artifact_too_large")
        return payload

    async def put_immutable(self, object_key: str, payload: bytes, *, expected_digest: str) -> None:
        await asyncio.to_thread(self._put_immutable, object_key, payload, expected_digest)

    def _put_immutable(self, object_key: str, payload: bytes, expected_digest: str) -> None:
        _validate_write(object_key, payload, expected_digest)
        path = self._path(object_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise ArtifactConflictError("immutable_artifact_conflict") from None
            return
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

    async def replace_pointer(self, object_key: str, payload: bytes) -> None:
        await asyncio.to_thread(self._replace_pointer, object_key, payload)

    def _replace_pointer(self, object_key: str, payload: bytes) -> None:
        if len(payload) > MAX_POINTER_BYTES:
            raise ArtifactUnavailableError("latest_pointer_too_large")
        path = self._path(object_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    async def aclose(self) -> None:
        return None

    def _path(self, object_key: str) -> Path:
        _validate_object_key(object_key)
        path = (self._root / object_key).resolve(strict=False)
        if not path.is_relative_to(self._root):
            raise ArtifactUnavailableError("artifact_path_escape")
        return path


class HttpArtifactStore:
    """Bounded read-only adapter for an independently hosted object-store/CDN origin."""

    def __init__(self, base_url: str, *, timeout_seconds: float = 3.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=timeout_seconds,
            follow_redirects=False,
        )

    async def read(self, object_key: str, *, max_bytes: int) -> bytes | None:
        _validate_object_key(object_key)
        url = f"{self._base_url}/{quote(object_key, safe='/')}"
        try:
            async with self._client.stream(
                "GET", url, headers={"Accept-Encoding": "identity"}
            ) as response:
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                declared = response.headers.get("content-length")
                if declared is not None and int(declared) > max_bytes:
                    raise ArtifactUnavailableError("artifact_too_large")
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise ArtifactUnavailableError("artifact_too_large")
                    chunks.append(chunk)
                return b"".join(chunks)
        except (httpx.HTTPError, ValueError) as error:
            raise ArtifactUnavailableError("artifact_origin_unavailable") from error

    async def put_immutable(self, object_key: str, payload: bytes, *, expected_digest: str) -> None:
        del object_key, payload, expected_digest
        raise ArtifactUnavailableError("artifact_origin_is_read_only")

    async def replace_pointer(self, object_key: str, payload: bytes) -> None:
        del object_key, payload
        raise ArtifactUnavailableError("artifact_origin_is_read_only")

    async def aclose(self) -> None:
        await self._client.aclose()


class PublicArtifactReadService:
    def __init__(
        self,
        *,
        store: ArtifactStore | None,
        cache_store: LocalArtifactStore | None = None,
        manifest_keys: ManifestKeyRing,
        receipt_keys: PublicationReceiptKeyRing,
        checkpoint_path: Path | None = None,
        max_cached_releases: int = 16,
    ) -> None:
        self._store = store
        self._cache_store = cache_store
        self._manifest_keys = manifest_keys
        self._receipt_keys = receipt_keys
        if not 0 <= max_cached_releases <= 16:
            raise ValueError("Release cache size must be between zero and 16")
        self._checkpoint_path = checkpoint_path
        self._max_cached_releases = max_cached_releases
        self._checkpoint_lock = asyncio.Lock()
        self._pack_semaphore = asyncio.Semaphore(1)
        self._release_cache: OrderedDict[tuple[str, str], ResolvedRelease] = OrderedDict()

    async def aclose(self) -> None:
        if self._store is not None:
            await self._store.aclose()
        if self._cache_store is not None:
            await self._cache_store.aclose()

    async def food(
        self,
        source: FoodSource,
        source_id: str,
        *,
        release_version: str | None = None,
        now: datetime | None = None,
    ) -> PublicFoodRecordResponse:
        release = await self.resolve_release(release_version=release_version, now=now)
        item = next(
            (
                food
                for food in release.manifest.foods
                if food.source is source and food.source_id == source_id
            ),
            None,
        )
        if item is None:
            raise ArtifactNotFoundError("food_not_found")
        record_bytes = await self._verified_read(item.record, max_bytes=MAX_RECORD_BYTES)
        try:
            record = FoodDetail.model_validate_json(record_bytes)
        except ValueError as error:
            raise ArtifactUnavailableError("food_record_invalid") from error
        if record.source is not source or record.source_id != source_id:
            raise ArtifactUnavailableError("food_record_identity_mismatch")
        base = f"/api/v1/public/releases/{release.manifest.release_version}"
        return PublicFoodRecordResponse(
            record=record,
            release=release.metadata,
            immutable_url=f"{base}/foods/{source.value}/{source_id}",
            provenance_url=f"{base}/foods/{source.value}/{source_id}/provenance",
        )

    async def provenance(
        self, source: FoodSource, source_id: str, *, release_version: str
    ) -> tuple[bytes, ResolvedRelease]:
        release = await self.resolve_release(release_version=release_version)
        item = next(
            (
                food
                for food in release.manifest.foods
                if food.source is source and food.source_id == source_id
            ),
            None,
        )
        if item is None:
            raise ArtifactNotFoundError("food_not_found")
        return (
            await self._verified_read(item.provenance, max_bytes=MAX_PROVENANCE_BYTES),
            release,
        )

    async def pack(
        self, pack_id: str, pack_version: str, *, release_version: str
    ) -> tuple[bytes, PublicPackArtifact, ResolvedRelease]:
        release = await self.resolve_release(release_version=release_version)
        item = next(
            (
                pack
                for pack in release.manifest.packs
                if pack.pack_id == pack_id and pack.pack_version == pack_version
            ),
            None,
        )
        if item is None:
            raise ArtifactNotFoundError("pack_not_found")
        async with self._pack_semaphore:
            payload = await self._verified_read(item.download, max_bytes=MAX_PACK_BYTES)
        return payload, item, release

    async def resolve_release(
        self,
        *,
        release_version: str | None,
        now: datetime | None = None,
    ) -> ResolvedRelease:
        if self._store is None:
            raise ArtifactUnavailableError("artifact_store_unconfigured")
        current = (now or datetime.now(UTC)).astimezone(UTC)
        if release_version is not None:
            if not _VERSION.fullmatch(release_version):
                raise ArtifactNotFoundError("release_not_found")
            descriptor = ArtifactDescriptor(
                object_key=f"releases/v1/release-{release_version}.json",
                digest="0" * 64,
                size_bytes=1,
                media_type="application/vnd.opennosh.release+json",
            )
            return await self._verified_release(
                descriptor, expected_version=release_version, exact=True
            )

        try:
            pointer_bytes = await self._required_read("latest/v1.json", MAX_POINTER_BYTES)
            pointer_envelope, pointer = self._parse_pointer(pointer_bytes)
            release = await self._verified_release(
                pointer.manifest,
                expected_version=pointer.release_version,
                exact=False,
            )
            _validate_pointer_window(pointer, release.manifest)
            if current > pointer.expires_at:
                raise ArtifactUnavailableError("latest_pointer_expired")
            await self._advance_checkpoint(pointer_bytes, pointer)
            return release
        except (ArtifactReadError, ManifestVerificationError, ValueError):
            checkpoint = await self._read_checkpoint()
            if checkpoint is None:
                raise ArtifactUnavailableError("latest_release_unavailable") from None
            pointer_envelope, pointer = self._parse_pointer(checkpoint)
            del pointer_envelope
            release = await self._verified_release(
                pointer.manifest,
                expected_version=pointer.release_version,
                exact=False,
            )
            _validate_pointer_window(pointer, release.manifest)
            return _as_stale(release, current, stale_since=pointer.expires_at)

    async def signed_manifest(self, release_version: str) -> tuple[bytes, ResolvedRelease]:
        release = await self.resolve_release(release_version=release_version)
        return release.manifest_bytes, release

    async def _verified_release(
        self,
        descriptor: ArtifactDescriptor,
        *,
        expected_version: str,
        exact: bool,
    ) -> ResolvedRelease:
        cache_key = (expected_version, "exact" if exact else descriptor.digest)
        cached = (
            self._release_cache.get(cache_key) if self._max_cached_releases > 0 else None
        )
        if cached is not None:
            self._release_cache.move_to_end(cache_key)
            return cached
        manifest_bytes = await self._required_read(
            descriptor.object_key, MAX_MANIFEST_BYTES, allow_cache=True
        )
        if not exact:
            _verify_descriptor(descriptor, manifest_bytes)
        envelope = _parse_envelope(manifest_bytes, self._manifest_keys)
        try:
            manifest = PublicReadReleaseManifest.model_validate(envelope.payload)
        except ValueError as error:
            raise ArtifactUnavailableError("release_manifest_invalid") from error
        if manifest.release_version != expected_version:
            raise ArtifactUnavailableError("release_version_mismatch")
        canonical_manifest = canonical_json(envelope.model_dump(mode="json"))
        if canonical_manifest != manifest_bytes:
            raise ArtifactUnavailableError("release_manifest_not_canonical")
        manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
        receipt_bytes = await self._required_read(
            manifest.publication_receipt_key, 256 * 1024, allow_cache=True
        )
        try:
            receipt = parse_signed_receipt(receipt_bytes)
            self._receipt_keys.verify(receipt)
        except ReceiptVerificationError as error:
            raise ArtifactUnavailableError("publication_receipt_invalid") from error
        bound = receipt.receipt
        if (
            bound.release_version != manifest.release_version
            or bound.published_at < manifest.published_at
            or bound.signed_release_metadata_digest != manifest_digest
            or _copy_release_digest(bound) != manifest_digest
        ):
            raise ArtifactUnavailableError("publication_receipt_binding_invalid")
        await self._cache_verified(
            descriptor.object_key,
            manifest_bytes,
            expected_digest=manifest_digest,
        )
        await self._cache_verified(
            manifest.publication_receipt_key,
            receipt_bytes,
            expected_digest=hashlib.sha256(receipt_bytes).hexdigest(),
        )
        metadata = PublicReleaseMetadata(
            release_version=manifest.release_version,
            published_at=manifest.published_at,
            state="verified",
            stale_age_seconds=0,
        )
        release = ResolvedRelease(manifest, envelope, manifest_bytes, metadata)
        if self._max_cached_releases > 0:
            self._release_cache[cache_key] = release
            self._release_cache.move_to_end(cache_key)
            while len(self._release_cache) > self._max_cached_releases:
                self._release_cache.popitem(last=False)
        return release

    def _parse_pointer(self, payload: bytes) -> tuple[SignedEnvelope, PublicReadLatestPointer]:
        envelope = _parse_envelope(payload, self._manifest_keys)
        if canonical_json(envelope.model_dump(mode="json")) != payload:
            raise ArtifactUnavailableError("latest_pointer_not_canonical")
        try:
            return envelope, PublicReadLatestPointer.model_validate(envelope.payload)
        except ValueError as error:
            raise ArtifactUnavailableError("latest_pointer_invalid") from error

    async def _verified_read(self, descriptor: ArtifactDescriptor, *, max_bytes: int) -> bytes:
        if descriptor.size_bytes > max_bytes:
            raise ArtifactUnavailableError("artifact_too_large")
        payload = await self._required_read(descriptor.object_key, max_bytes, allow_cache=True)
        _verify_descriptor(descriptor, payload)
        await self._cache_verified(
            descriptor.object_key,
            payload,
            expected_digest=descriptor.digest,
        )
        return payload

    async def _required_read(
        self,
        object_key: str,
        max_bytes: int,
        *,
        allow_cache: bool = False,
    ) -> bytes:
        if self._store is None:
            raise ArtifactUnavailableError("artifact_store_unconfigured")
        try:
            payload = await self._store.read(object_key, max_bytes=max_bytes)
        except ArtifactUnavailableError:
            payload = None
        if payload is None and allow_cache and self._cache_store is not None:
            payload = await self._cache_store.read(object_key, max_bytes=max_bytes)
        if payload is None:
            raise ArtifactUnavailableError("artifact_missing")
        return payload

    async def _cache_verified(
        self,
        object_key: str,
        payload: bytes,
        *,
        expected_digest: str,
    ) -> None:
        if self._cache_store is None:
            return
        try:
            await self._cache_store.put_immutable(
                object_key,
                payload,
                expected_digest=expected_digest,
            )
        except ArtifactConflictError as error:
            raise ArtifactUnavailableError("verified_cache_conflict") from error

    async def _advance_checkpoint(
        self,
        payload: bytes,
        pointer: PublicReadLatestPointer,
    ) -> None:
        if self._checkpoint_path is None:
            return
        async with self._checkpoint_lock:
            await asyncio.to_thread(self._advance_checkpoint_locked, payload, pointer)

    def _advance_checkpoint_locked(
        self,
        payload: bytes,
        pointer: PublicReadLatestPointer,
    ) -> None:
        if self._checkpoint_path is None:
            return
        lock_path = self._checkpoint_path.with_suffix(f"{self._checkpoint_path.suffix}.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                try:
                    existing = self._checkpoint_path.read_bytes()
                except FileNotFoundError:
                    existing = None
                if existing is not None:
                    if existing == payload:
                        return
                    _, previous = self._parse_pointer(existing)
                    previous_version = _version_tuple(previous.release_version)
                    candidate_version = _version_tuple(pointer.release_version)
                    if candidate_version < previous_version:
                        raise ArtifactUnavailableError("latest_release_rollback")
                    if (
                        candidate_version == previous_version
                        and previous.manifest.digest != pointer.manifest.digest
                    ):
                        raise ArtifactUnavailableError("latest_release_equivocation")
                    if (
                        candidate_version == previous_version
                        and previous.manifest.digest == pointer.manifest.digest
                        and pointer.expires_at < previous.expires_at
                    ):
                        raise ArtifactUnavailableError("latest_pointer_expiry_rollback")
                _atomic_write(self._checkpoint_path, payload)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    async def _read_checkpoint(self) -> bytes | None:
        if self._checkpoint_path is None:
            return None
        try:
            payload = await asyncio.to_thread(self._checkpoint_path.read_bytes)
        except FileNotFoundError:
            return None
        if len(payload) > MAX_POINTER_BYTES:
            return None
        return payload


async def activate_verified_release(
    *,
    service: PublicArtifactReadService,
    store: ArtifactStore,
    immutable_objects: dict[str, bytes],
    manifest_bytes: bytes,
    receipt_bytes: bytes,
    pointer_bytes: bytes,
) -> None:
    """Publish immutable bytes first and move latest only after all proof verifies."""

    envelope = _parse_envelope(manifest_bytes, service._manifest_keys)
    if canonical_json(envelope.model_dump(mode="json")) != manifest_bytes:
        raise ArtifactUnavailableError("release_manifest_not_canonical")
    manifest = PublicReadReleaseManifest.model_validate(envelope.payload)
    pointer_envelope = _parse_envelope(pointer_bytes, service._manifest_keys)
    if canonical_json(pointer_envelope.model_dump(mode="json")) != pointer_bytes:
        raise ArtifactUnavailableError("latest_pointer_not_canonical")
    pointer = PublicReadLatestPointer.model_validate(pointer_envelope.payload)
    _validate_pointer_window(pointer, manifest)
    if pointer.release_version != manifest.release_version:
        raise ArtifactUnavailableError("latest_pointer_release_mismatch")
    try:
        _verify_descriptor(pointer.manifest, manifest_bytes)
    except ArtifactUnavailableError as error:
        raise ArtifactUnavailableError("latest_pointer_manifest_mismatch") from error
    expected = {
        item.object_key: item
        for item in (
            *(food.record for food in manifest.foods),
            *(food.provenance for food in manifest.foods),
            *(pack.download for pack in manifest.packs),
        )
    }
    if set(immutable_objects) != set(expected):
        raise ArtifactUnavailableError("release_artifact_set_mismatch")
    for key, payload in immutable_objects.items():
        _verify_descriptor(expected[key], payload)
    parsed_receipt = parse_signed_receipt(receipt_bytes)
    service._receipt_keys.verify(parsed_receipt)
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    if (
        parsed_receipt.receipt.release_version != manifest.release_version
        or parsed_receipt.receipt.published_at < manifest.published_at
        or parsed_receipt.receipt.signed_release_metadata_digest != manifest_digest
        or _copy_release_digest(parsed_receipt.receipt) != manifest_digest
    ):
        raise ArtifactUnavailableError("publication_receipt_binding_invalid")
    for key, payload in immutable_objects.items():
        await store.put_immutable(key, payload, expected_digest=expected[key].digest)
    await store.put_immutable(
        manifest.publication_receipt_key,
        receipt_bytes,
        expected_digest=hashlib.sha256(receipt_bytes).hexdigest(),
    )
    await store.put_immutable(
        pointer.manifest.object_key,
        manifest_bytes,
        expected_digest=pointer.manifest.digest,
    )
    await store.replace_pointer("latest/v1.json", pointer_bytes)


def _copy_release_digest(receipt: PublicationReceipt) -> str | None:
    return next(
        (
            proof.content_digest
            for proof in receipt.verified_steps
            if proof.step is PublicationStepName.COPY_RELEASE
        ),
        None,
    )


def artifact_descriptor(object_key: str, payload: bytes, media_type: str) -> ArtifactDescriptor:
    return ArtifactDescriptor(
        object_key=object_key,
        digest=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        media_type=media_type,
    )


def _parse_envelope(payload: bytes, keys: ManifestKeyRing) -> SignedEnvelope:
    try:
        value = json.loads(payload)
        envelope = SignedEnvelope.model_validate(value)
        keys.verify(envelope)
        return envelope
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
        raise ArtifactUnavailableError("signed_envelope_invalid") from error


def _verify_descriptor(descriptor: ArtifactDescriptor, payload: bytes) -> None:
    if len(payload) != descriptor.size_bytes:
        raise ArtifactUnavailableError("artifact_size_mismatch")
    if hashlib.sha256(payload).hexdigest() != descriptor.digest:
        raise ArtifactUnavailableError("artifact_digest_mismatch")


def _validate_write(object_key: str, payload: bytes, expected_digest: str) -> None:
    _validate_object_key(object_key)
    if not _SHA256.fullmatch(expected_digest):
        raise ValueError("expected digest must be SHA-256")
    if hashlib.sha256(payload).hexdigest() != expected_digest:
        raise ArtifactUnavailableError("artifact_digest_mismatch")


def _validate_object_key(value: str) -> None:
    if not _OBJECT_KEY.fullmatch(value) or ".." in value.split("/"):
        raise ArtifactUnavailableError("artifact_object_key_invalid")


def _version_tuple(value: str) -> tuple[int, int, int, int]:
    return tuple(int(part) for part in value.split("."))  # type: ignore[return-value]


def _validate_pointer_window(
    pointer: PublicReadLatestPointer, manifest: PublicReadReleaseManifest
) -> None:
    issued_at = pointer.issued_at or manifest.published_at
    if not (
        manifest.published_at <= issued_at < pointer.expires_at <= issued_at + timedelta(hours=24)
    ):
        raise ArtifactUnavailableError("latest_pointer_lifetime_invalid")


def _as_stale(
    release: ResolvedRelease, current: datetime, *, stale_since: datetime
) -> ResolvedRelease:
    age = max(0, int((current - stale_since).total_seconds()))
    return ResolvedRelease(
        release.manifest,
        release.manifest_envelope,
        release.manifest_bytes,
        PublicReleaseMetadata(
            release_version=release.manifest.release_version,
            published_at=release.manifest.published_at,
            state="stale",
            stale_age_seconds=age,
        ),
    )


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
