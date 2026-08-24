import asyncio
import base64
import binascii
import fcntl
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
MAX_PUBLIC_SNAPSHOT_BYTES = 24 * 1024


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


class TrustedReleaseCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    release_version: Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$")]
    manifest_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    published_at: datetime


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
    ) -> None:
        self._latest_pointer_path = latest_pointer_path
        self._release_directory = release_directory
        self._key_ring = key_ring
        self._stale_after_seconds = stale_after_seconds
        self._checkpoint_path = checkpoint_path
        self._last_verified: PublicCommonsSnapshot | None = None
        self._lock = asyncio.Lock()

    async def resolve(self, *, now: datetime | None = None) -> PublicCommonsSnapshot:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        checked_at = current.replace(
            minute=current.minute - current.minute % 5, second=0, microsecond=0
        )
        async with self._lock:
            try:
                snapshot = await asyncio.to_thread(self._resolve_verified, checked_at)
            except SnapshotResolutionError as error:
                if self._last_verified is not None:
                    return self._stale_snapshot(self._last_verified, checked_at)
                return unavailable_snapshot(checked_at=checked_at, reason=error.reason)
            except (OSError, json.JSONDecodeError, ValueError):
                if self._last_verified is not None:
                    return self._stale_snapshot(self._last_verified, checked_at)
                return unavailable_snapshot(
                    checked_at=checked_at,
                    reason=CommonsSnapshotReason.NO_PUBLISHED_RELEASE,
                )
            self._last_verified = snapshot
            return snapshot

    def _resolve_verified(self, checked_at: datetime) -> PublicCommonsSnapshot:
        if self._latest_pointer_path is None or self._release_directory is None:
            raise ManifestVerificationError("public commons release paths are not configured")
        try:
            pointer_envelope = self._read_envelope(self._latest_pointer_path)
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
        snapshot_id = hashlib.sha256(
            f"1:{pointer.manifest_digest}:{window_end.strftime('%Y%m%d%H%M')}".encode()
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
        try:
            self._accept_trusted_checkpoint(pointer, manifest)
        except (OSError, ValueError) as error:
            raise SnapshotResolutionError(CommonsSnapshotReason.INVALID_RELEASE_MANIFEST) from error
        return snapshot

    def _accept_trusted_checkpoint(
        self, pointer: LatestReleasePointer, manifest: ReleaseManifest
    ) -> None:
        if self._checkpoint_path is None:
            return
        self._checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        candidate = TrustedReleaseCheckpoint(
            release_version=pointer.release_version,
            manifest_digest=pointer.manifest_digest,
            published_at=manifest.published_at,
        )
        with self._checkpoint_path.open("a+b") as checkpoint_file:
            fcntl.flock(checkpoint_file.fileno(), fcntl.LOCK_EX)
            checkpoint_file.seek(0)
            raw_checkpoint = checkpoint_file.read()
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
                    return
            checkpoint_file.seek(0)
            checkpoint_file.truncate()
            checkpoint_file.write(canonical_json(candidate.model_dump(mode="json")))
            checkpoint_file.flush()
            os.fsync(checkpoint_file.fileno())

    @staticmethod
    def _read_limited(path: Path, *, maximum_bytes: int) -> bytes:
        with path.open("rb") as artifact:
            content = artifact.read(maximum_bytes + 1)
        if len(content) > maximum_bytes:
            raise ManifestVerificationError("signed artifact exceeds its size limit")
        return content

    @classmethod
    def _read_envelope(cls, path: Path) -> SignedEnvelope:
        return SignedEnvelope.model_validate_json(
            cls._read_limited(path, maximum_bytes=MAX_LATEST_POINTER_BYTES)
        )

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
