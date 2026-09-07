from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pytest
from opennosh_api.main import create_app
from opennosh_api.public.artifacts import (
    ArtifactUnavailableError,
    PublicReadReleaseManifest,
    PublicReleaseMetadata,
    ResolvedRelease,
)
from opennosh_api.public_commons.accepted_activity import (
    ActivityProjectionUnavailable,
    CanonicalAcceptedActivityProjection,
)
from opennosh_api.public_commons.artifact_snapshot import (
    ArtifactBackedPublicCommonsSnapshotService,
)
from opennosh_api.public_commons.manifests import SignedEnvelope
from opennosh_api.public_commons.schemas import (
    AcceptedActivityEvent,
    AcceptedEventType,
    CommonsSnapshotReason,
    CommonsSnapshotState,
)
from opennosh_api.settings import Settings

NOW = datetime(2026, 9, 5, 12, 3, tzinfo=UTC)
BUCKET = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
MANIFEST_BYTES = b"signed-manifest"
RECEIPT_DIGEST = "b" * 64


def _release(
    *,
    state: Literal["verified", "stale"] = "verified",
    stale_age_seconds: int = 0,
) -> ResolvedRelease:
    manifest = PublicReadReleaseManifest(
        release_version="1.2.3.4",
        published_at=datetime(2026, 9, 4, 12, 0, tzinfo=UTC),
        publication_receipt_key="receipts/v1/11111111-1111-4111-8111-111111111111.json",
    )
    return ResolvedRelease(
        manifest=manifest,
        manifest_envelope=SignedEnvelope(
            key_id="test",
            payload=manifest.model_dump(mode="json"),
            signature="A" * 86,
        ),
        manifest_bytes=MANIFEST_BYTES,
        publication_receipt_digest=RECEIPT_DIGEST,
        metadata=PublicReleaseMetadata(
            release_version=manifest.release_version,
            published_at=manifest.published_at,
            state=state,
            stale_age_seconds=stale_age_seconds,
        ),
        publication_receipt_published_at=datetime(2026, 9, 4, 12, 5, tzinfo=UTC),
    )


class FakeArtifactService:
    def __init__(self, release: ResolvedRelease | None) -> None:
        self.release = release
        self.calls: list[datetime | None] = []

    async def resolve_release(
        self,
        *,
        release_version: str | None,
        now: datetime | None = None,
    ) -> ResolvedRelease:
        assert release_version is None
        self.calls.append(now)
        if self.release is None:
            raise ArtifactUnavailableError("latest_release_unavailable")
        return self.release


class FakeActivitySource:
    def __init__(self, projection: CanonicalAcceptedActivityProjection) -> None:
        self.projection = projection
        self.calls: list[tuple[ResolvedRelease, datetime]] = []

    async def project(
        self,
        *,
        release: ResolvedRelease,
        checked_at: datetime,
    ) -> CanonicalAcceptedActivityProjection:
        self.calls.append((release, checked_at))
        return self.projection


@pytest.mark.asyncio
async def test_verified_artifact_release_exposes_proof_without_inventing_activity() -> None:
    reader = FakeArtifactService(_release())
    service = ArtifactBackedPublicCommonsSnapshotService(
        reader,
        stale_after_seconds=300,
    )

    first = await service.refresh_response(now=NOW)
    cached = await service.resolve_response(now=NOW)

    assert first.cache_status == "rebuilt"
    assert cached.cache_status == "memory"
    assert reader.calls == [BUCKET]
    assert first.snapshot.state is CommonsSnapshotState.PARTIAL
    assert first.snapshot.release is not None
    assert first.snapshot.release.version == "1.2.3.4"
    assert first.snapshot.release.manifest_digest == hashlib.sha256(MANIFEST_BYTES).hexdigest()
    assert first.snapshot.release.publication_receipt_digest == RECEIPT_DIGEST
    assert first.snapshot.verified_record_count == 0
    assert first.snapshot.activity.accepted_count == 0
    assert first.snapshot.activity.events == ()
    assert first.snapshot.reasons == (CommonsSnapshotReason.ACTIVITY_PROJECTION_LAG,)
    assert service.metrics.rebuilds == 1
    assert service.metrics.source_artifact_reads == 1


@pytest.mark.asyncio
async def test_verified_accepted_event_projection_drives_live_snapshot() -> None:
    release = _release()
    event = AcceptedActivityEvent(
        event_id="accepted-1",
        event_type=AcceptedEventType.FOOD,
        food_or_pack_id="dhokla-gujarati",
        food_locale="en-IN",
        accepted_at=datetime(2026, 9, 4, 12, 4, tzinfo=UTC),
        source_commit="a" * 40,
        href="/en/explore/foods/community/dhokla-gujarati",
        summary="Accepted Dhokla as a verified food record.",
    )
    activity = FakeActivitySource(
        CanonicalAcceptedActivityProjection(
            accepted_count=1,
            events=(event,),
            most_recent_verified_record=None,
            event_checkpoint="c" * 64,
        )
    )
    service = ArtifactBackedPublicCommonsSnapshotService(
        FakeArtifactService(release),
        stale_after_seconds=300,
        activity_source=activity,
    )

    resolution = await service.refresh_response(now=NOW)

    assert resolution.snapshot.state is CommonsSnapshotState.LIVE
    assert resolution.snapshot.activity.accepted_count == 1
    assert resolution.snapshot.activity.events == (event,)
    assert resolution.snapshot.reasons == ()
    assert resolution.snapshot.release is not None
    assert resolution.snapshot.release.published_at == release.publication_receipt_published_at
    assert activity.calls == [(release, BUCKET)]


@pytest.mark.asyncio
async def test_verified_empty_accepted_event_projection_drives_quiet_snapshot() -> None:
    service = ArtifactBackedPublicCommonsSnapshotService(
        FakeArtifactService(_release()),
        stale_after_seconds=300,
        activity_source=FakeActivitySource(
            CanonicalAcceptedActivityProjection(
                accepted_count=0,
                events=(),
                most_recent_verified_record=None,
                event_checkpoint="d" * 64,
            )
        ),
    )

    resolution = await service.refresh_response(now=NOW)

    assert resolution.snapshot.state is CommonsSnapshotState.QUIET
    assert resolution.snapshot.freshness.activity == "verified"
    assert resolution.snapshot.reasons == ()


@pytest.mark.asyncio
async def test_activity_projection_failure_remains_truthfully_partial(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FailedActivitySource:
        async def project(self, **_arguments: object) -> CanonicalAcceptedActivityProjection:
            raise RuntimeError("database details stay private")

    service = ArtifactBackedPublicCommonsSnapshotService(
        FakeArtifactService(_release()),
        stale_after_seconds=300,
    )
    service.configure_activity_source(FailedActivitySource())

    resolution = await service.refresh_response(now=NOW)

    assert resolution.snapshot.state is CommonsSnapshotState.PARTIAL
    assert "error=RuntimeError" in caplog.text
    assert "database details stay private" not in caplog.text


@pytest.mark.asyncio
async def test_activity_projection_logs_stable_safe_failure_code(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FailedActivitySource:
        async def project(self, **_arguments: object) -> CanonicalAcceptedActivityProjection:
            raise ActivityProjectionUnavailable("accepted_event_release_boundary_incomplete")

    service = ArtifactBackedPublicCommonsSnapshotService(
        FakeArtifactService(_release()),
        stale_after_seconds=300,
        activity_source=FailedActivitySource(),
    )

    resolution = await service.refresh_response(now=NOW)

    assert resolution.snapshot.state is CommonsSnapshotState.PARTIAL
    assert "error=accepted_event_release_boundary_incomplete" in caplog.text


@pytest.mark.asyncio
async def test_checkpoint_fallback_preserves_release_proof_as_stale() -> None:
    reader = FakeArtifactService(_release(state="stale", stale_age_seconds=3600))
    service = ArtifactBackedPublicCommonsSnapshotService(
        reader,
        stale_after_seconds=300,
    )

    resolution = await service.refresh_response(now=NOW)

    assert resolution.cache_status == "stale"
    assert resolution.snapshot.state is CommonsSnapshotState.STALE
    assert resolution.snapshot.freshness.stale_since == datetime(2026, 9, 5, 11, 0, tzinfo=UTC)
    assert resolution.snapshot.reasons == (CommonsSnapshotReason.LATEST_RELEASE_UNAVAILABLE,)
    assert service.metrics.stale_fallbacks == 1


@pytest.mark.asyncio
async def test_unavailable_artifact_release_never_claims_proof() -> None:
    service = ArtifactBackedPublicCommonsSnapshotService(
        FakeArtifactService(None),
        stale_after_seconds=300,
    )

    resolution = await service.refresh_response(now=NOW)

    assert resolution.cache_status == "unavailable"
    assert resolution.snapshot.state is CommonsSnapshotState.UNAVAILABLE
    assert resolution.snapshot.release is None
    assert resolution.snapshot.verified_record_count is None
    assert resolution.snapshot.reasons == (CommonsSnapshotReason.LATEST_RELEASE_UNAVAILABLE,)
    assert service.metrics.unavailable_responses == 1


@pytest.mark.asyncio
async def test_request_path_never_waits_for_the_artifact_origin() -> None:
    reader = FakeArtifactService(_release())
    service = ArtifactBackedPublicCommonsSnapshotService(
        reader,
        stale_after_seconds=300,
    )

    resolution = await service.resolve_response(now=NOW)

    assert reader.calls == []
    assert resolution.snapshot.state is CommonsSnapshotState.UNAVAILABLE


def test_app_uses_artifact_backed_snapshot_when_canonical_artifacts_are_configured(
    tmp_path: Path,
) -> None:
    app = create_app(
        Settings(  # type: ignore[call-arg]
            public_artifact_directory=tmp_path / "artifacts",
            public_artifact_checkpoint_path=tmp_path / "state" / "latest.json",
            _env_file=None,
        )
    )

    assert isinstance(
        app.state.public_commons_snapshot_service,
        ArtifactBackedPublicCommonsSnapshotService,
    )
