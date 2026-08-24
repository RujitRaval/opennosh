import asyncio
import base64
import binascii
import fcntl
import hashlib
import hmac
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from opennosh_api.public_commons.projections import (
    MAX_PUBLIC_SNAPSHOT_BYTES,
    PointerRevision,
    PublicCommonsProjection,
    SnapshotProjectionStore,
)
from opennosh_api.public_commons.schemas import (
    AcceptedActivityEvent,
    CommonsActivityWindow,
    CommonsComponentFreshness,
    CommonsSnapshotReason,
    CommonsSnapshotState,
    MostRecentVerifiedRecord,
    PublicCommonsSnapshot,
    PublicReleaseProof,
)

MAX_LATEST_POINTER_BYTES = 16 * 1024
MAX_RELEASE_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_RELEASE_EVENTS = 10_000


@dataclass(frozen=True)
class PublicCommonsResolution:
    snapshot: PublicCommonsSnapshot
    etag: str
    response_bytes: int
    cache_status: Literal["memory", "projection", "rebuilt", "stale", "unavailable"]


@dataclass(frozen=True)
class PublicCommonsSnapshotMetrics:
    projection_reads: int
    projection_read_bytes: int
    projection_writes: int
    projection_write_bytes: int
    source_artifact_reads: int
    rebuilds: int
    stale_fallbacks: int
    unavailable_responses: int
    last_response_bytes: int


class ManifestVerificationError(ValueError):
    """Raised when a latest pointer or release manifest cannot be trusted."""


class SnapshotResolutionError(ValueError):
    def __init__(self, reason: CommonsSnapshotReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


class SignedEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    key_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]{1,64}$")]
    payload: dict[str, Any]
    signature: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{86}$")]


class LatestReleasePointer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    release_version: Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$")]
    manifest_filename: Annotated[
        str, Field(pattern=r"^release-[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+\.json$")
    ]
    manifest_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class TrustedProjectionReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    release_version: Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$")]
    manifest_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    projection_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class TrustedReleaseCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    release_version: Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$")]
    manifest_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    published_at: datetime
    projection_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    previous_projection: TrustedProjectionReference | None = None


class ReleaseManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    release_version: Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$")]
    published_at: datetime
    publication_receipt_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    verified_record_count: Annotated[int, Field(ge=0)]
    activity_projection_complete: bool = True
    accepted_events: Annotated[
        tuple[AcceptedActivityEvent, ...], Field(max_length=MAX_RELEASE_EVENTS)
    ] = ()
    most_recent_verified_record: MostRecentVerifiedRecord | None = None

    @field_validator("published_at")
    @classmethod
    def published_at_is_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("published_at must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_events(self) -> "ReleaseManifest":
        event_ids = [event.event_id for event in self.accepted_events]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("accepted event IDs must be unique")
        if any(event.accepted_at > self.published_at for event in self.accepted_events):
            raise ValueError("accepted events cannot be newer than their release")
        if (
            self.most_recent_verified_record is not None
            and self.most_recent_verified_record.verified_at > self.published_at
        ):
            raise ValueError("most recent verified record cannot be newer than its release")
        return self


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _decode_base64url(value: str, *, expected_bytes: int) -> bytes:
    try:
        decoded = base64.urlsafe_b64decode(f"{value}{'=' * (-len(value) % 4)}")
    except (ValueError, binascii.Error) as error:
        raise ManifestVerificationError("value is not valid base64url") from error
    if len(decoded) != expected_bytes:
        raise ManifestVerificationError("value has an invalid decoded length")
    return decoded


class ManifestKeyRing:
    def __init__(self, keys: dict[str, Ed25519PublicKey]) -> None:
        self._keys = keys

    @classmethod
    def from_config(cls, value: str) -> "ManifestKeyRing":
        keys: dict[str, Ed25519PublicKey] = {}
        for entry in value.split(","):
            key_id, separator, encoded_key = entry.partition(":")
            if not separator or not key_id:
                raise ValueError(
                    "public commons verifying keys must use key-id:base64url-public-key entries"
                )
            if key_id in keys:
                raise ValueError("public commons verifying key IDs must be unique")
            try:
                key_bytes = _decode_base64url(encoded_key, expected_bytes=32)
                keys[key_id] = Ed25519PublicKey.from_public_bytes(key_bytes)
            except ManifestVerificationError as error:
                raise ValueError("public commons verifying key is invalid") from error
        return cls(keys)

    def verify(self, envelope: SignedEnvelope) -> None:
        key = self._keys.get(envelope.key_id)
        if key is None:
            raise ManifestVerificationError("signature key is not trusted")
        try:
            key.verify(
                _decode_base64url(envelope.signature, expected_bytes=64),
                canonical_json(envelope.payload),
            )
        except InvalidSignature as error:
            raise ManifestVerificationError("signature verification failed") from error


class PublicCommonsSnapshotService:
    def __init__(
        self,
        *,
        latest_pointer_path: Path | None,
        release_directory: Path | None,
        key_ring: ManifestKeyRing,
        stale_after_seconds: int,
        checkpoint_path: Path | None = None,
        projection_path: Path | None = None,
    ) -> None:
        self._latest_pointer_path = latest_pointer_path
        self._release_directory = release_directory
        self._key_ring = key_ring
        self._stale_after_seconds = stale_after_seconds
        self._checkpoint_path = checkpoint_path
        self._projection_store = (
            SnapshotProjectionStore(projection_path) if projection_path is not None else None
        )
        (
            self._trusted_checkpoint,
            self._trusted_checkpoint_revision,
        ) = self._read_trusted_checkpoint()
        self._last_verified: PublicCommonsSnapshot | None = None
        self._cached_key: (
            tuple[datetime, PointerRevision | None, PointerRevision | None] | None
        ) = None
        self._cached_resolution: PublicCommonsResolution | None = None
        self._refresh_lock = asyncio.Lock()
        self._projection_reads = 0
        self._projection_read_bytes = 0
        self._projection_writes = 0
        self._projection_write_bytes = 0
        self._source_artifact_reads = 0
        self._rebuilds = 0
        self._stale_fallbacks = 0
        self._unavailable_responses = 0
        self._last_response_bytes = 0

    async def resolve(self, *, now: datetime | None = None) -> PublicCommonsSnapshot:
        return (await self.resolve_response(now=now)).snapshot

    async def refresh(self, *, now: datetime | None = None) -> PublicCommonsSnapshot:
        return (await self.refresh_response(now=now)).snapshot

    async def resolve_response(
        self, *, now: datetime | None = None
    ) -> PublicCommonsResolution:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        checked_at = current.replace(
            minute=current.minute - current.minute % 5, second=0, microsecond=0
        )
        return await asyncio.to_thread(self._serve_materialized, checked_at)

    async def refresh_response(
        self, *, now: datetime | None = None
    ) -> PublicCommonsResolution:
        """Reconcile signed source into the bounded projection off the request path."""

        current = (now or datetime.now(UTC)).astimezone(UTC)
        checked_at = current.replace(
            minute=current.minute - current.minute % 5, second=0, microsecond=0
        )
        async with self._refresh_lock:
            return await asyncio.to_thread(self._refresh_materialized, checked_at)

    @property
    def materialization_enabled(self) -> bool:
        return (
            self._latest_pointer_path is not None
            and self._release_directory is not None
            and self._checkpoint_path is not None
            and self._projection_store is not None
        )

    @property
    def metrics(self) -> PublicCommonsSnapshotMetrics:
        return PublicCommonsSnapshotMetrics(
            projection_reads=self._projection_reads,
            projection_read_bytes=self._projection_read_bytes,
            projection_writes=self._projection_writes,
            projection_write_bytes=self._projection_write_bytes,
            source_artifact_reads=self._source_artifact_reads,
            rebuilds=self._rebuilds,
            stale_fallbacks=self._stale_fallbacks,
            unavailable_responses=self._unavailable_responses,
            last_response_bytes=self._last_response_bytes,
        )

    def _serve_materialized(self, checked_at: datetime) -> PublicCommonsResolution:
        pointer_revision = self._current_pointer_revision()
        checkpoint_revision = self._current_checkpoint_revision()
        cache_key = (checked_at, pointer_revision, checkpoint_revision)
        if self._cached_key == cache_key and self._cached_resolution is not None:
            cached = self._cached_resolution
            return PublicCommonsResolution(
                snapshot=cached.snapshot,
                etag=cached.etag,
                response_bytes=cached.response_bytes,
                cache_status="memory",
            )

        try:
            pointer_revision = self._current_pointer_revision()
            checkpoint_revision = self._current_checkpoint_revision()
            cache_key = (checked_at, pointer_revision, checkpoint_revision)
            if checkpoint_revision != self._trusted_checkpoint_revision:
                (
                    self._trusted_checkpoint,
                    self._trusted_checkpoint_revision,
                ) = self._read_trusted_checkpoint()
            projection = self._read_projection()
            if projection is not None and self._projection_matches_checkpoint(projection):
                if (
                    pointer_revision is not None
                    and projection.as_of_bucket == checked_at
                    and projection.pointer_revision == pointer_revision
                ):
                    resolution = self._projection_resolution(
                        projection, cache_status="projection"
                    )
                    self._remember(cache_key, resolution)
                    return resolution
                return self._fallback_resolution(
                    checked_at=checked_at,
                    pointer_revision=pointer_revision,
                    projection=projection,
                    reason=CommonsSnapshotReason.LATEST_RELEASE_UNAVAILABLE,
                )
            return self._fallback_resolution(
                checked_at=checked_at,
                pointer_revision=pointer_revision,
                projection=None,
                reason=CommonsSnapshotReason.NO_PUBLISHED_RELEASE,
            )
        except (OSError, ValueError):
            return self._fallback_resolution(
                checked_at=checked_at,
                pointer_revision=pointer_revision,
                projection=None,
                reason=CommonsSnapshotReason.NO_PUBLISHED_RELEASE,
            )

    def _refresh_materialized(self, checked_at: datetime) -> PublicCommonsResolution:
        try:
            with self._projection_lock():
                pointer_revision = self._current_pointer_revision()
                (
                    self._trusted_checkpoint,
                    self._trusted_checkpoint_revision,
                ) = self._read_trusted_checkpoint()
                projection = self._read_projection()
                projection_is_trusted = (
                    projection is not None
                    and self._projection_matches_checkpoint(projection)
                )
                if (
                    projection_is_trusted
                    and projection is not None
                    and pointer_revision is not None
                    and projection.as_of_bucket == checked_at
                    and projection.pointer_revision == pointer_revision
                ):
                    resolution = self._projection_resolution(
                        projection, cache_status="projection"
                    )
                    self._remember(
                        (
                            checked_at,
                            pointer_revision,
                            self._trusted_checkpoint_revision,
                        ),
                        resolution,
                    )
                    return resolution

                try:
                    rebuilt = self._resolve_verified(
                        checked_at,
                        fallback_projection=(
                            projection if projection_is_trusted else None
                        ),
                    )
                    if self._projection_store is not None:
                        written = self._projection_store.write(rebuilt)
                        self._projection_writes += 1
                        self._projection_write_bytes += written
                    self._rebuilds += 1
                    resolution = self._projection_resolution(
                        rebuilt, cache_status="rebuilt"
                    )
                    self._remember(
                        (
                            checked_at,
                            rebuilt.pointer_revision,
                            self._trusted_checkpoint_revision,
                        ),
                        resolution,
                    )
                    return resolution
                except SnapshotResolutionError as error:
                    return self._fallback_resolution(
                        checked_at=checked_at,
                        pointer_revision=pointer_revision,
                        projection=projection if projection_is_trusted else None,
                        reason=error.reason,
                    )
        except (OSError, json.JSONDecodeError, ValueError):
            return self._fallback_resolution(
                checked_at=checked_at,
                pointer_revision=self._current_pointer_revision(),
                projection=None,
                reason=CommonsSnapshotReason.NO_PUBLISHED_RELEASE,
            )

    def _read_projection(self) -> PublicCommonsProjection | None:
        if self._projection_store is None:
            return None
        try:
            projection, read_bytes = self._projection_store.read()
        except (FileNotFoundError, OSError, ValueError):
            return None
        self._projection_reads += 1
        self._projection_read_bytes += read_bytes
        return projection

    @contextmanager
    def _projection_lock(self) -> Iterator[None]:
        if self._projection_store is None:
            yield
            return
        self._projection_store.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self._projection_store.lock_path.open("a+b") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _projection_matches_checkpoint(self, projection: PublicCommonsProjection) -> bool:
        trusted = self._trusted_checkpoint
        if trusted is None:
            return False
        projection_digest = projection.content_digest()
        matches_current = (
            projection.source_release_version == trusted.release_version
            and projection.source_release_digest == trusted.manifest_digest
            and trusted.projection_digest is not None
            and hmac.compare_digest(projection_digest, trusted.projection_digest)
        )
        previous = trusted.previous_projection
        matches_previous = (
            previous is not None
            and projection.source_release_version == previous.release_version
            and projection.source_release_digest == previous.manifest_digest
            and hmac.compare_digest(projection_digest, previous.projection_digest)
        )
        return matches_current or matches_previous

    def _read_trusted_checkpoint(
        self,
    ) -> tuple[TrustedReleaseCheckpoint | None, PointerRevision | None]:
        if self._checkpoint_path is None:
            return None, None
        try:
            with self._checkpoint_path.open("rb") as checkpoint_file:
                raw_checkpoint = checkpoint_file.read(MAX_LATEST_POINTER_BYTES + 1)
                revision = PointerRevision.from_stat(os.fstat(checkpoint_file.fileno()))
            if len(raw_checkpoint) > MAX_LATEST_POINTER_BYTES:
                raise ManifestVerificationError("checkpoint exceeds its size limit")
            return TrustedReleaseCheckpoint.model_validate_json(raw_checkpoint), revision
        except (FileNotFoundError, OSError, ValueError):
            return None, self._current_checkpoint_revision()

    def _current_checkpoint_revision(self) -> PointerRevision | None:
        if self._checkpoint_path is None:
            return None
        try:
            return PointerRevision.from_stat(self._checkpoint_path.stat())
        except OSError:
            return None

    def _current_pointer_revision(self) -> PointerRevision | None:
        if self._latest_pointer_path is None:
            return None
        try:
            return PointerRevision.from_stat(self._latest_pointer_path.stat())
        except OSError:
            return None

    def _remember(
        self,
        key: tuple[datetime, PointerRevision | None, PointerRevision | None],
        resolution: PublicCommonsResolution,
    ) -> None:
        self._last_response_bytes = resolution.response_bytes
        if resolution.snapshot.state in {
            CommonsSnapshotState.LIVE,
            CommonsSnapshotState.QUIET,
            CommonsSnapshotState.PARTIAL,
        }:
            self._cached_key = key
            self._cached_resolution = resolution
            self._last_verified = resolution.snapshot
        else:
            self._cached_key = None
            self._cached_resolution = None

    def _projection_resolution(
        self,
        projection: PublicCommonsProjection,
        *,
        cache_status: Literal["projection", "rebuilt"],
    ) -> PublicCommonsResolution:
        payload = projection.snapshot.model_dump_json().encode()
        etag_value = hashlib.sha256(
            projection.cache_identity() + b":" + payload
        ).hexdigest()
        return PublicCommonsResolution(
            snapshot=projection.snapshot,
            etag=f'"{etag_value}"',
            response_bytes=len(payload),
            cache_status=cache_status,
        )

    def _fallback_resolution(
        self,
        *,
        checked_at: datetime,
        pointer_revision: PointerRevision | None,
        projection: PublicCommonsProjection | None,
        reason: CommonsSnapshotReason,
    ) -> PublicCommonsResolution:
        fallback = projection.snapshot if projection is not None else self._last_verified
        if fallback is not None:
            snapshot = self._stale_snapshot(fallback, checked_at)
            status: Literal["stale", "unavailable"] = "stale"
            self._stale_fallbacks += 1
        else:
            snapshot = unavailable_snapshot(checked_at=checked_at, reason=reason)
            status = "unavailable"
            self._unavailable_responses += 1
        payload = snapshot.model_dump_json().encode()
        etag = f'"{hashlib.sha256(payload).hexdigest()}"'
        resolution = PublicCommonsResolution(
            snapshot=snapshot,
            etag=etag,
            response_bytes=len(payload),
            cache_status=status,
        )
        self._remember(
            (
                checked_at,
                pointer_revision,
                self._trusted_checkpoint_revision,
            ),
            resolution,
        )
        return resolution

    def _resolve_verified(
        self,
        checked_at: datetime,
        *,
        fallback_projection: PublicCommonsProjection | None = None,
    ) -> PublicCommonsProjection:
        if self._latest_pointer_path is None or self._release_directory is None:
            raise ManifestVerificationError("public commons release paths are not configured")
        try:
            pointer_envelope, pointer_revision = self._read_pointer(
                self._latest_pointer_path
            )
            self._key_ring.verify(pointer_envelope)
            pointer = LatestReleasePointer.model_validate(pointer_envelope.payload)
        except FileNotFoundError as error:
            raise SnapshotResolutionError(CommonsSnapshotReason.NO_PUBLISHED_RELEASE) from error
        except (OSError, json.JSONDecodeError, ValueError) as error:
            raise SnapshotResolutionError(CommonsSnapshotReason.INVALID_LATEST_POINTER) from error

        try:
            release_root = self._release_directory.resolve()
            manifest_path = (release_root / pointer.manifest_filename).resolve()
            if not manifest_path.is_relative_to(release_root):
                raise ManifestVerificationError("release manifest escapes the configured directory")
            self._source_artifact_reads += 1
            raw_manifest = self._read_limited(
                manifest_path, maximum_bytes=MAX_RELEASE_MANIFEST_BYTES
            )
            if hashlib.sha256(raw_manifest).hexdigest() != pointer.manifest_digest:
                raise ManifestVerificationError(
                    "release manifest digest does not match the pointer"
                )
            manifest_envelope = SignedEnvelope.model_validate_json(raw_manifest)
            self._key_ring.verify(manifest_envelope)
            manifest = ReleaseManifest.model_validate(manifest_envelope.payload)
            if manifest.release_version != pointer.release_version:
                raise ManifestVerificationError("release versions do not match")
        except (OSError, json.JSONDecodeError, ValueError) as error:
            raise SnapshotResolutionError(CommonsSnapshotReason.INVALID_RELEASE_MANIFEST) from error

        release_cutoff = min(checked_at, manifest.published_at.astimezone(UTC))
        window_end = checked_at
        window_start = window_end - timedelta(hours=24)
        events = tuple(
            sorted(
                (
                    event
                    for event in manifest.accepted_events
                    if window_start
                    <= event.accepted_at.astimezone(UTC)
                    <= min(window_end, release_cutoff)
                ),
                key=lambda event: event.accepted_at,
                reverse=True,
            )
        )
        accepted_count = len(events)
        reasons: tuple[CommonsSnapshotReason, ...]
        if not manifest.activity_projection_complete:
            state = CommonsSnapshotState.PARTIAL
            reasons = (CommonsSnapshotReason.ACTIVITY_PROJECTION_LAG,)
            activity_freshness: Literal["partial", "verified"] = "partial"
        elif accepted_count:
            state = CommonsSnapshotState.LIVE
            reasons = ()
            activity_freshness = "verified"
        else:
            state = CommonsSnapshotState.QUIET
            reasons = ()
            activity_freshness = "verified"
        event_checkpoint = hashlib.sha256(
            canonical_json(
                [
                    event.model_dump(mode="json")
                    for event in sorted(
                        manifest.accepted_events,
                        key=lambda event: (event.accepted_at, event.event_id),
                    )
                ]
            )
        ).hexdigest()
        snapshot_id = hashlib.sha256(
            canonical_json(
                {
                    "schema_version": "1",
                    "release_digest": pointer.manifest_digest,
                    "event_checkpoint": event_checkpoint,
                    "activity_cutoff": window_end.isoformat(),
                    "as_of_bucket": checked_at.isoformat(),
                }
            )
        ).hexdigest()[:32]
        snapshot = PublicCommonsSnapshot(
            snapshot_id=snapshot_id,
            as_of=checked_at,
            state=state,
            release=PublicReleaseProof(
                version=manifest.release_version,
                manifest_digest=pointer.manifest_digest,
                publication_receipt_digest=manifest.publication_receipt_digest,
                published_at=manifest.published_at,
            ),
            verified_record_count=manifest.verified_record_count,
            activity=CommonsActivityWindow(
                starts_at=window_start,
                ends_at=window_end,
                accepted_count=accepted_count,
                events=events[:4],
                most_recent_verified_record=manifest.most_recent_verified_record,
            ),
            freshness=CommonsComponentFreshness(
                release="verified",
                activity=activity_freshness,
                checked_at=checked_at,
            ),
            reasons=reasons,
        )
        if len(snapshot.model_dump_json().encode()) > MAX_PUBLIC_SNAPSHOT_BYTES:
            raise SnapshotResolutionError(CommonsSnapshotReason.INVALID_RELEASE_MANIFEST)
        projection = PublicCommonsProjection(
            source_release_digest=pointer.manifest_digest,
            source_release_version=pointer.release_version,
            event_checkpoint=event_checkpoint,
            activity_cutoff=window_end,
            as_of_bucket=checked_at,
            built_at=datetime.now(UTC),
            pointer_revision=pointer_revision,
            snapshot=snapshot,
        )
        try:
            self._accept_trusted_checkpoint(
                pointer,
                manifest,
                projection,
                fallback_projection=fallback_projection,
            )
        except (OSError, ValueError) as error:
            raise SnapshotResolutionError(CommonsSnapshotReason.INVALID_RELEASE_MANIFEST) from error
        return projection

    def _accept_trusted_checkpoint(
        self,
        pointer: LatestReleasePointer,
        manifest: ReleaseManifest,
        projection: PublicCommonsProjection,
        *,
        fallback_projection: PublicCommonsProjection | None,
    ) -> None:
        if self._checkpoint_path is None:
            return
        self._checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_lock_path = self._checkpoint_path.with_suffix(
            f"{self._checkpoint_path.suffix}.lock"
        )
        candidate = TrustedReleaseCheckpoint(
            release_version=pointer.release_version,
            manifest_digest=pointer.manifest_digest,
            published_at=manifest.published_at,
            projection_digest=projection.content_digest(),
            previous_projection=(
                TrustedProjectionReference(
                    release_version=fallback_projection.source_release_version,
                    manifest_digest=fallback_projection.source_release_digest,
                    projection_digest=fallback_projection.content_digest(),
                )
                if fallback_projection is not None
                else None
            ),
        )
        with checkpoint_lock_path.open("a+b") as checkpoint_lock:
            fcntl.flock(checkpoint_lock.fileno(), fcntl.LOCK_EX)
            try:
                raw_checkpoint = self._read_limited(
                    self._checkpoint_path,
                    maximum_bytes=MAX_LATEST_POINTER_BYTES,
                )
            except FileNotFoundError:
                raw_checkpoint = b""
            if raw_checkpoint:
                trusted = TrustedReleaseCheckpoint.model_validate_json(raw_checkpoint)
                candidate_version = tuple(
                    int(part) for part in candidate.release_version.split(".")
                )
                trusted_version = tuple(int(part) for part in trusted.release_version.split("."))
                if candidate_version < trusted_version:
                    raise ManifestVerificationError("release rollback rejected")
                if (
                    candidate_version == trusted_version
                    and candidate.manifest_digest != trusted.manifest_digest
                ):
                    raise ManifestVerificationError("release equivocation rejected")
                if candidate.published_at < trusted.published_at:
                    raise ManifestVerificationError("release publication time rollback rejected")
                if candidate == trusted:
                    self._trusted_checkpoint = trusted
                    self._trusted_checkpoint_revision = self._current_checkpoint_revision()
                    return
            payload = canonical_json(candidate.model_dump(mode="json"))
            temporary = self._checkpoint_path.with_name(
                f".{self._checkpoint_path.name}.{os.getpid()}."
                f"{projection.content_digest()[:16]}.tmp"
            )
            try:
                with temporary.open("xb") as checkpoint_file:
                    checkpoint_file.write(payload)
                    checkpoint_file.flush()
                    os.fsync(checkpoint_file.fileno())
                os.replace(temporary, self._checkpoint_path)
                directory_fd = os.open(self._checkpoint_path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                temporary.unlink(missing_ok=True)
            self._trusted_checkpoint = candidate
            self._trusted_checkpoint_revision = self._current_checkpoint_revision()

    @staticmethod
    def _read_limited(path: Path, *, maximum_bytes: int) -> bytes:
        with path.open("rb") as artifact:
            content = artifact.read(maximum_bytes + 1)
        if len(content) > maximum_bytes:
            raise ManifestVerificationError("signed artifact exceeds its size limit")
        return content

    def _read_pointer(self, path: Path) -> tuple[SignedEnvelope, PointerRevision]:
        with path.open("rb") as artifact:
            content = artifact.read(MAX_LATEST_POINTER_BYTES + 1)
            revision = PointerRevision.from_stat(os.fstat(artifact.fileno()))
        self._source_artifact_reads += 1
        if len(content) > MAX_LATEST_POINTER_BYTES:
            raise ManifestVerificationError("signed artifact exceeds its size limit")
        return SignedEnvelope.model_validate_json(content), revision

    def _stale_snapshot(
        self, snapshot: PublicCommonsSnapshot, checked_at: datetime
    ) -> PublicCommonsSnapshot:
        stale_since = min(
            checked_at,
            snapshot.freshness.checked_at + timedelta(seconds=self._stale_after_seconds),
        )
        return snapshot.model_copy(
            update={
                "as_of": checked_at,
                "state": CommonsSnapshotState.STALE,
                "freshness": CommonsComponentFreshness(
                    release="stale",
                    activity="stale",
                    checked_at=checked_at,
                    stale_since=stale_since,
                ),
                "reasons": (CommonsSnapshotReason.LATEST_RELEASE_UNAVAILABLE,),
            }
        )


def unavailable_snapshot(
    *, checked_at: datetime, reason: CommonsSnapshotReason
) -> PublicCommonsSnapshot:
    return PublicCommonsSnapshot(
        snapshot_id=f"unavailable-{checked_at.strftime('%Y%m%d%H%M')}",
        as_of=checked_at,
        state=CommonsSnapshotState.UNAVAILABLE,
        release=None,
        verified_record_count=None,
        activity=CommonsActivityWindow(
            starts_at=checked_at - timedelta(hours=24),
            ends_at=checked_at,
            accepted_count=0,
        ),
        freshness=CommonsComponentFreshness(
            release="unavailable",
            activity="unavailable",
            checked_at=checked_at,
        ),
        reasons=(reason,),
    )
