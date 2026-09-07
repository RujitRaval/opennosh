from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

from opennosh_api.public.artifacts import (
    ArtifactUnavailableError,
    ResolvedRelease,
)
from opennosh_api.public_commons.accepted_activity import (
    ActivityProjectionUnavailable,
    CanonicalAcceptedActivityProjection,
)
from opennosh_api.public_commons.manifests import (
    PublicCommonsResolution,
    PublicCommonsSnapshotMetrics,
    canonical_json,
    unavailable_snapshot,
)
from opennosh_api.public_commons.schemas import (
    CommonsActivityWindow,
    CommonsComponentFreshness,
    CommonsSnapshotReason,
    CommonsSnapshotState,
    PublicCommonsSnapshot,
    PublicReleaseProof,
)

logger = logging.getLogger(__name__)


class PublicReleaseResolver(Protocol):
    async def resolve_release(
        self,
        *,
        release_version: str | None,
        now: datetime | None = None,
    ) -> ResolvedRelease: ...


class AcceptedActivitySource(Protocol):
    async def project(
        self,
        *,
        release: ResolvedRelease,
        checked_at: datetime,
    ) -> CanonicalAcceptedActivityProjection: ...


class ArtifactBackedPublicCommonsSnapshotService:
    """Project the canonical verified artifact release into the homepage contract.

    The public read manifest proves the immutable release and its record inventory,
    and combines it with a separately verified projection of the append-only accepted-event
    ledger. The request path remains an in-memory read; source reconciliation happens only
    in the background materializer.
    """

    def __init__(
        self,
        artifact_service: PublicReleaseResolver,
        *,
        stale_after_seconds: int,
        activity_source: AcceptedActivitySource | None = None,
    ) -> None:
        if stale_after_seconds <= 0:
            raise ValueError("Public commons stale threshold must be positive")
        self._artifact_service = artifact_service
        self._activity_source = activity_source
        self._stale_after_seconds = stale_after_seconds
        self._cached_bucket: datetime | None = None
        self._cached_resolution: PublicCommonsResolution | None = None
        self._resolution_lock = asyncio.Lock()
        self._source_artifact_reads = 0
        self._rebuilds = 0
        self._stale_fallbacks = 0
        self._unavailable_responses = 0
        self._last_response_bytes = 0

    @property
    def materialization_enabled(self) -> bool:
        return True

    def configure_activity_source(self, source: AcceptedActivitySource) -> None:
        self._activity_source = source

    @property
    def metrics(self) -> PublicCommonsSnapshotMetrics:
        return PublicCommonsSnapshotMetrics(
            projection_reads=0,
            projection_read_bytes=0,
            projection_writes=0,
            projection_write_bytes=0,
            source_artifact_reads=self._source_artifact_reads,
            rebuilds=self._rebuilds,
            stale_fallbacks=self._stale_fallbacks,
            unavailable_responses=self._unavailable_responses,
            last_response_bytes=self._last_response_bytes,
        )

    async def resolve(self, *, now: datetime | None = None) -> PublicCommonsSnapshot:
        return (await self.resolve_response(now=now)).snapshot

    async def refresh(self, *, now: datetime | None = None) -> PublicCommonsSnapshot:
        return (await self.refresh_response(now=now)).snapshot

    async def refresh_response(self, *, now: datetime | None = None) -> PublicCommonsResolution:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        checked_at = self._bucket(current)
        async with self._resolution_lock:
            resolution = await self._resolve_bucket(checked_at)
            if (
                self._cached_bucket == checked_at
                and self._cached_resolution is not None
                and self._cached_resolution.snapshot == resolution.snapshot
            ):
                return self._as_memory(self._cached_resolution)
            if resolution.cache_status == "rebuilt":
                self._rebuilds += 1
            elif resolution.cache_status == "stale":
                self._stale_fallbacks += 1
            else:
                self._unavailable_responses += 1
            self._cached_bucket = checked_at
            self._cached_resolution = resolution
            self._last_response_bytes = resolution.response_bytes
            return resolution

    async def resolve_response(self, *, now: datetime | None = None) -> PublicCommonsResolution:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        checked_at = self._bucket(current)
        if self._cached_bucket == checked_at and self._cached_resolution is not None:
            return self._as_memory(self._cached_resolution)
        if self._cached_resolution is None or self._cached_resolution.snapshot.release is None:
            return self._resolution(
                unavailable_snapshot(
                    checked_at=checked_at,
                    reason=CommonsSnapshotReason.LATEST_RELEASE_UNAVAILABLE,
                ),
                cache_status="unavailable",
            )
        self._stale_fallbacks += 1
        return self._resolution(
            self._stale_snapshot(self._cached_resolution.snapshot, checked_at),
            cache_status="stale",
        )

    async def _resolve_bucket(self, checked_at: datetime) -> PublicCommonsResolution:
        self._source_artifact_reads += 1
        try:
            release = await self._artifact_service.resolve_release(
                release_version=None,
                now=checked_at,
            )
        except ArtifactUnavailableError:
            return self._resolution(
                unavailable_snapshot(
                    checked_at=checked_at,
                    reason=CommonsSnapshotReason.LATEST_RELEASE_UNAVAILABLE,
                ),
                cache_status="unavailable",
            )

        cache_status: Literal["rebuilt", "stale"]
        if release.metadata.state == "stale":
            cache_status = "stale"
        else:
            cache_status = "rebuilt"
        activity: CanonicalAcceptedActivityProjection | None = None
        if release.metadata.state != "stale" and self._activity_source is not None:
            try:
                activity = await self._activity_source.project(
                    release=release,
                    checked_at=checked_at,
                )
            except Exception as error:
                error_code = (
                    error.code
                    if isinstance(error, ActivityProjectionUnavailable)
                    else type(error).__name__
                )
                logger.warning(
                    "Public Commons accepted-event projection state=partial error=%s",
                    error_code,
                )
        return self._resolution(
            self._snapshot_from_release(release, checked_at, activity=activity),
            cache_status=cache_status,
        )

    @staticmethod
    def _snapshot_from_release(
        release: ResolvedRelease,
        checked_at: datetime,
        *,
        activity: CanonicalAcceptedActivityProjection | None,
    ) -> PublicCommonsSnapshot:
        is_stale = release.metadata.state == "stale"
        reasons: tuple[CommonsSnapshotReason, ...]
        if is_stale:
            state = CommonsSnapshotState.STALE
            reasons = (CommonsSnapshotReason.LATEST_RELEASE_UNAVAILABLE,)
        elif activity is None:
            state = CommonsSnapshotState.PARTIAL
            reasons = (CommonsSnapshotReason.ACTIVITY_PROJECTION_LAG,)
        elif activity.accepted_count:
            state = CommonsSnapshotState.LIVE
            reasons = ()
        else:
            state = CommonsSnapshotState.QUIET
            reasons = ()
        manifest_digest = hashlib.sha256(release.manifest_bytes).hexdigest()
        activity_checkpoint = (
            "unavailable" if activity is None else activity.event_checkpoint
        )
        snapshot_id = hashlib.sha256(
            canonical_json(
                {
                    "activity_projection": activity_checkpoint,
                    "as_of_bucket": checked_at.isoformat(),
                    "manifest_digest": manifest_digest,
                    "publication_receipt_digest": release.publication_receipt_digest,
                    "release_state": release.metadata.state,
                    "schema_version": "1",
                }
            )
        ).hexdigest()[:32]
        stale_since = (
            checked_at - timedelta(seconds=release.metadata.stale_age_seconds) if is_stale else None
        )
        published_at = (
            release.publication_receipt_published_at or release.manifest.published_at
        )
        return PublicCommonsSnapshot(
            snapshot_id=snapshot_id,
            as_of=checked_at,
            state=state,
            release=PublicReleaseProof(
                version=release.manifest.release_version,
                manifest_digest=manifest_digest,
                publication_receipt_digest=release.publication_receipt_digest,
                published_at=published_at,
            ),
            verified_record_count=len(release.manifest.foods),
            activity=CommonsActivityWindow(
                starts_at=checked_at - timedelta(hours=24),
                ends_at=checked_at,
                accepted_count=0 if activity is None else activity.accepted_count,
                events=() if activity is None else activity.events,
                most_recent_verified_record=(
                    None if activity is None else activity.most_recent_verified_record
                ),
            ),
            freshness=CommonsComponentFreshness(
                release="stale" if is_stale else "verified",
                activity=(
                    "stale"
                    if is_stale
                    else "partial"
                    if activity is None
                    else "verified"
                ),
                checked_at=checked_at,
                stale_since=stale_since,
            ),
            reasons=reasons,
        )

    def _stale_snapshot(
        self,
        snapshot: PublicCommonsSnapshot,
        checked_at: datetime,
    ) -> PublicCommonsSnapshot:
        stale_since = snapshot.freshness.stale_since or min(
            checked_at,
            snapshot.as_of + timedelta(seconds=self._stale_after_seconds),
        )
        return snapshot.model_copy(
            update={
                "as_of": checked_at,
                "state": CommonsSnapshotState.STALE,
                "activity": CommonsActivityWindow(
                    starts_at=checked_at - timedelta(hours=24),
                    ends_at=checked_at,
                    accepted_count=0,
                ),
                "freshness": CommonsComponentFreshness(
                    release="stale",
                    activity="stale",
                    checked_at=checked_at,
                    stale_since=stale_since,
                ),
                "reasons": (CommonsSnapshotReason.LATEST_RELEASE_UNAVAILABLE,),
            }
        )

    @staticmethod
    def _bucket(current: datetime) -> datetime:
        return current.replace(
            minute=current.minute - current.minute % 5,
            second=0,
            microsecond=0,
        )

    @staticmethod
    def _resolution(
        snapshot: PublicCommonsSnapshot,
        *,
        cache_status: Literal["rebuilt", "stale", "unavailable"],
    ) -> PublicCommonsResolution:
        payload = snapshot.model_dump_json().encode()
        etag = f'"{hashlib.sha256(payload).hexdigest()}"'
        return PublicCommonsResolution(
            snapshot=snapshot,
            etag=etag,
            response_bytes=len(payload),
            cache_status=cache_status,
        )

    @staticmethod
    def _as_memory(resolution: PublicCommonsResolution) -> PublicCommonsResolution:
        return PublicCommonsResolution(
            snapshot=resolution.snapshot,
            etag=resolution.etag,
            response_bytes=resolution.response_bytes,
            cache_status="memory",
        )
