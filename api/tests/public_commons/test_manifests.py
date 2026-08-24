import asyncio
import base64
import hashlib
import json
import multiprocessing
import os
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from opennosh_api.public_commons.manifests import (
    ManifestKeyRing,
    PublicCommonsSnapshotService,
    canonical_json,
)
from opennosh_api.public_commons.projections import MAX_PUBLIC_SNAPSHOT_BYTES
from opennosh_api.public_commons.schemas import CommonsSnapshotState

SIGNING_KEY_ID = "test-v1"
SIGNING_PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(
    hashlib.sha256(b"test-public-commons-signing-key-2026").digest()
)
VERIFYING_KEY = (
    base64.urlsafe_b64encode(
        SIGNING_PRIVATE_KEY.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    )
    .decode()
    .rstrip("=")
)
KEY_RING = ManifestKeyRing.from_config(f"{SIGNING_KEY_ID}:{VERIFYING_KEY}")
PUBLISHED_AT = datetime(2026, 8, 23, 18, tzinfo=UTC)


def _signed_envelope(payload: dict[str, object]) -> bytes:
    signature = (
        base64.urlsafe_b64encode(SIGNING_PRIVATE_KEY.sign(canonical_json(payload)))
        .decode()
        .rstrip("=")
    )
    return json.dumps(
        {
            "schema_version": "1",
            "key_id": SIGNING_KEY_ID,
            "payload": payload,
            "signature": signature,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _event(index: int, *, hours_ago: int = 1) -> dict[str, object]:
    return {
        "event_id": f"accepted-{index}",
        "event_type": ("food", "source", "portion", "pack")[index % 4],
        "food_or_pack_id": f"food-{index}",
        "food_locale": "Gujarat, India",
        "accepted_at": (PUBLISHED_AT - timedelta(hours=hours_ago)).isoformat(),
        "source_commit": f"abcde{index:02d}",
        "href": f"/en/explore/foods/community/food-{index}",
        "summary": f"Accepted verified food record {index}.",
        "public_contributor_credit": "Community contributor",
    }


def _write_release(
    root: Path,
    *,
    version: str = "0.30.0.0",
    events: list[dict[str, object]] | None = None,
    projection_complete: bool = True,
    record_count: int = 42,
    published_at: datetime = PUBLISHED_AT,
    recent_verified_at: datetime | None = None,
    recent_record: dict[str, object] | None = None,
) -> tuple[Path, Path]:
    releases = root / "releases"
    releases.mkdir()
    manifest_payload: dict[str, object] = {
        "schema_version": "1",
        "release_version": version,
        "published_at": published_at.isoformat(),
        "publication_receipt_digest": "a" * 64,
        "verified_record_count": record_count,
        "activity_projection_complete": projection_complete,
        "accepted_events": events or [],
        "most_recent_verified_record": recent_record
        or {
            "record_id": "dhokla-gujarati",
            "name": "Dhokla",
            "food_locale": "Gujarat, India",
            "verified_at": (recent_verified_at or published_at - timedelta(days=2)).isoformat(),
            "href": "/en/explore/dhokla-gujarati",
        },
    }
    manifest_bytes = _signed_envelope(manifest_payload)
    manifest_name = f"release-{version}.json"
    (releases / manifest_name).write_bytes(manifest_bytes)
    pointer_payload: dict[str, object] = {
        "schema_version": "1",
        "release_version": version,
        "manifest_filename": manifest_name,
        "manifest_digest": hashlib.sha256(manifest_bytes).hexdigest(),
    }
    pointer = root / "latest.json"
    pointer.write_bytes(_signed_envelope(pointer_payload))
    return pointer, releases


def _service(pointer: Path, releases: Path) -> PublicCommonsSnapshotService:
    return PublicCommonsSnapshotService(
        latest_pointer_path=pointer,
        release_directory=releases,
        key_ring=KEY_RING,
        stale_after_seconds=300,
        checkpoint_path=pointer.parent / "trusted-release.json",
        projection_path=pointer.parent / "homepage-snapshot.json",
    )


def _refresh_in_process(
    pointer_path: str,
    release_directory: str,
    start: Any,
    results: Any,
) -> None:
    start.wait()
    service = _service(Path(pointer_path), Path(release_directory))
    resolution = asyncio.run(service.refresh_response(now=PUBLISHED_AT))
    results.put(
        (
            resolution.cache_status,
            resolution.snapshot.model_dump_json(),
            resolution.etag,
            service.metrics.rebuilds,
            service.metrics.source_artifact_reads,
        )
    )


@pytest.mark.asyncio
async def test_signed_release_drives_one_verified_quiet_snapshot(tmp_path: Path) -> None:
    pointer, releases = _write_release(tmp_path)

    snapshot = await _service(pointer, releases).refresh(now=PUBLISHED_AT + timedelta(hours=1))

    assert snapshot.state is CommonsSnapshotState.QUIET
    assert snapshot.release is not None
    assert snapshot.release.version == "0.30.0.0"
    assert snapshot.verified_record_count == 42
    assert snapshot.activity.accepted_count == 0
    assert snapshot.activity.ends_at == PUBLISHED_AT + timedelta(hours=1)


@pytest.mark.asyncio
async def test_live_snapshot_counts_all_events_but_returns_only_latest_four(
    tmp_path: Path,
) -> None:
    pointer, releases = _write_release(
        tmp_path,
        events=[_event(index, hours_ago=index + 1) for index in range(6)],
    )

    snapshot = await _service(pointer, releases).refresh(now=PUBLISHED_AT)

    assert snapshot.state is CommonsSnapshotState.LIVE
    assert snapshot.activity.accepted_count == 6
    assert len(snapshot.activity.events) == 4
    assert [event.event_id for event in snapshot.activity.events] == [
        "accepted-0",
        "accepted-1",
        "accepted-2",
        "accepted-3",
    ]


@pytest.mark.asyncio
async def test_quiet_window_ages_out_old_accepted_events(tmp_path: Path) -> None:
    pointer, releases = _write_release(tmp_path, events=[_event(1)])
    service = _service(pointer, releases)

    live = await service.refresh(now=PUBLISHED_AT)
    quiet = await service.refresh(now=PUBLISHED_AT + timedelta(hours=26))

    assert live.state is CommonsSnapshotState.LIVE
    assert quiet.state is CommonsSnapshotState.QUIET
    assert quiet.activity.accepted_count == 0
    assert quiet.verified_record_count == live.verified_record_count


@pytest.mark.asyncio
async def test_projection_lag_is_partial_without_inventing_events(tmp_path: Path) -> None:
    pointer, releases = _write_release(tmp_path, events=[_event(1)], projection_complete=False)

    snapshot = await _service(pointer, releases).refresh(now=PUBLISHED_AT)

    assert snapshot.state is CommonsSnapshotState.PARTIAL
    assert snapshot.freshness.activity == "partial"
    assert [reason.value for reason in snapshot.reasons] == ["activity_projection_lag"]


@pytest.mark.asyncio
async def test_failed_revalidation_retains_only_last_verified_snapshot(tmp_path: Path) -> None:
    pointer, releases = _write_release(tmp_path, record_count=81)
    service = _service(pointer, releases)
    verified = await service.refresh(now=PUBLISHED_AT)
    pointer.write_text("not a signed pointer")

    stale = await service.refresh(now=PUBLISHED_AT + timedelta(hours=2))

    assert verified.state is CommonsSnapshotState.QUIET
    assert stale.state is CommonsSnapshotState.STALE
    assert stale.verified_record_count == 81
    assert stale.release == verified.release
    assert stale.freshness.release == "stale"
    assert stale.freshness.stale_since == PUBLISHED_AT + timedelta(minutes=5)


@pytest.mark.asyncio
async def test_missing_latest_pointer_is_a_typed_first_run_absence(tmp_path: Path) -> None:
    releases = tmp_path / "releases"
    releases.mkdir()
    service = _service(tmp_path / "missing.json", releases)

    snapshot = await service.resolve(now=PUBLISHED_AT)
    next_bucket = await service.resolve(now=PUBLISHED_AT + timedelta(minutes=5))

    assert snapshot.state is CommonsSnapshotState.UNAVAILABLE
    assert [reason.value for reason in snapshot.reasons] == ["no_published_release"]
    assert next_bucket.state is CommonsSnapshotState.UNAVAILABLE
    assert next_bucket.release is None
    assert next_bucket.verified_record_count is None
    assert [reason.value for reason in next_bucket.reasons] == ["no_published_release"]


@pytest.mark.asyncio
async def test_invalid_first_release_never_claims_a_verified_count(tmp_path: Path) -> None:
    pointer, releases = _write_release(tmp_path)
    pointer.write_text("not a signed pointer")

    snapshot = await _service(pointer, releases).refresh(now=PUBLISHED_AT)

    assert snapshot.state is CommonsSnapshotState.UNAVAILABLE
    assert snapshot.release is None
    assert snapshot.verified_record_count is None
    assert snapshot.activity.events == ()


@pytest.mark.asyncio
async def test_invalid_release_manifest_has_a_typed_unavailable_reason(tmp_path: Path) -> None:
    pointer, releases = _write_release(tmp_path)
    (releases / "release-0.30.0.0.json").write_text("corrupt manifest")

    snapshot = await _service(pointer, releases).refresh(now=PUBLISHED_AT)

    assert snapshot.state is CommonsSnapshotState.UNAVAILABLE
    assert [reason.value for reason in snapshot.reasons] == ["invalid_release_manifest"]


@pytest.mark.asyncio
async def test_duplicate_accepted_event_cannot_increment_the_release_count_twice(
    tmp_path: Path,
) -> None:
    duplicated = _event(1)
    pointer, releases = _write_release(tmp_path, events=[duplicated, duplicated])

    snapshot = await _service(pointer, releases).refresh(now=PUBLISHED_AT)

    assert snapshot.state is CommonsSnapshotState.UNAVAILABLE
    assert snapshot.verified_record_count is None


@pytest.mark.asyncio
async def test_pointer_resolution_stays_on_one_release_during_latest_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pointer, releases = _write_release(tmp_path, version="0.30.0.0", record_count=42)
    original_read = PublicCommonsSnapshotService._read_pointer
    reads = 0

    def read_then_replace(self: PublicCommonsSnapshotService, path: Path):  # type: ignore[no-untyped-def]
        nonlocal reads
        envelope = original_read(self, path)
        reads += 1
        if reads == 1:
            replacement = tmp_path / "replacement"
            replacement.mkdir()
            next_pointer, next_releases = _write_release(
                replacement, version="0.31.0.0", record_count=99
            )
            pointer.write_bytes(next_pointer.read_bytes())
            for file in next_releases.iterdir():
                (releases / file.name).write_bytes(file.read_bytes())
        return envelope

    monkeypatch.setattr(
        PublicCommonsSnapshotService, "_read_pointer", read_then_replace
    )

    snapshot = await _service(pointer, releases).refresh(now=PUBLISHED_AT)

    assert reads == 1
    assert snapshot.release is not None
    assert snapshot.release.version == "0.30.0.0"
    assert snapshot.verified_record_count == 42


@pytest.mark.asyncio
async def test_snapshot_uses_one_stable_five_minute_bucket(tmp_path: Path) -> None:
    pointer, releases = _write_release(tmp_path, events=[_event(1)])
    service = _service(pointer, releases)

    first = await service.refresh(now=PUBLISHED_AT + timedelta(minutes=1, seconds=12))
    second = await service.refresh(now=PUBLISHED_AT + timedelta(minutes=4, seconds=59))

    assert first == second
    assert first.as_of == PUBLISHED_AT
    assert first.activity.ends_at == PUBLISHED_AT


@pytest.mark.asyncio
async def test_current_projection_uses_the_in_memory_fast_path(tmp_path: Path) -> None:
    pointer, releases = _write_release(tmp_path, events=[_event(1)])
    service = _service(pointer, releases)

    built = await service.refresh_response(now=PUBLISHED_AT)
    cached = await service.resolve_response(now=PUBLISHED_AT)

    assert built.cache_status == "rebuilt"
    assert cached.cache_status == "memory"
    assert cached.snapshot == built.snapshot


@pytest.mark.asyncio
async def test_restart_serves_one_bounded_projection_read_without_source_scan(
    tmp_path: Path,
) -> None:
    pointer, releases = _write_release(
        tmp_path,
        events=[_event(index, hours_ago=index + 1) for index in range(6)],
    )
    first_service = _service(pointer, releases)

    built = await first_service.refresh_response(now=PUBLISHED_AT)
    restarted_service = _service(pointer, releases)
    restored = await restarted_service.resolve_response(now=PUBLISHED_AT)

    assert built.cache_status == "rebuilt"
    assert restored.cache_status == "projection"
    assert restored.snapshot == built.snapshot
    assert restored.response_bytes <= 24 * 1024
    assert restarted_service.metrics.projection_reads == 1
    assert restarted_service.metrics.source_artifact_reads == 0
    assert restarted_service.metrics.rebuilds == 0


@pytest.mark.asyncio
async def test_concurrent_cold_services_publish_one_complete_projection(
    tmp_path: Path,
) -> None:
    pointer, releases = _write_release(tmp_path, events=[_event(1), _event(2)])
    first_service = _service(pointer, releases)
    second_service = _service(pointer, releases)

    first, second = await asyncio.gather(
        first_service.refresh_response(now=PUBLISHED_AT),
        second_service.refresh_response(now=PUBLISHED_AT),
    )

    assert {first.cache_status, second.cache_status} == {"rebuilt", "projection"}
    assert first.snapshot == second.snapshot
    assert first.etag == second.etag
    assert (
        first_service.metrics.projection_writes + second_service.metrics.projection_writes
        == 1
    )
    assert (
        first_service.metrics.source_artifact_reads
        + second_service.metrics.source_artifact_reads
        == 2
    )


def test_cold_materializers_coordinate_across_processes(tmp_path: Path) -> None:
    pointer, releases = _write_release(tmp_path, events=[_event(1), _event(2)])
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_refresh_in_process,
            args=(str(pointer), str(releases), start, results),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    start.set()
    observed = [results.get(timeout=10) for _ in processes]
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    assert {item[0] for item in observed} == {"rebuilt", "projection"}
    assert len({item[1] for item in observed}) == 1
    assert len({item[2] for item in observed}) == 1
    assert sum(item[3] for item in observed) == 1
    assert sum(item[4] for item in observed) == 2
    stored = json.loads((tmp_path / "homepage-snapshot.json").read_bytes())
    assert stored["snapshot"]["activity"]["accepted_count"] == 2
    assert not list(tmp_path.glob(".homepage-snapshot.json.*.tmp"))


@pytest.mark.asyncio
async def test_publication_change_invalidates_same_bucket_atomically(tmp_path: Path) -> None:
    pointer, releases = _write_release(tmp_path, version="0.30.0.0", record_count=42)
    service = _service(pointer, releases)
    first = await service.refresh_response(now=PUBLISHED_AT)
    replacement = tmp_path / "replacement-release"
    replacement.mkdir()
    next_pointer, next_releases = _write_release(
        replacement,
        version="0.31.0.0",
        record_count=43,
        events=[_event(7)],
    )
    for manifest in next_releases.iterdir():
        (releases / manifest.name).write_bytes(manifest.read_bytes())
    pointer.write_bytes(next_pointer.read_bytes())

    second = await service.refresh_response(now=PUBLISHED_AT)

    assert first.snapshot.release is not None
    assert second.snapshot.release is not None
    assert first.snapshot.release.version == "0.30.0.0"
    assert second.snapshot.release.version == "0.31.0.0"
    assert second.snapshot.verified_record_count == 43
    assert second.cache_status == "rebuilt"
    assert second.etag != first.etag
    stored = json.loads((tmp_path / "homepage-snapshot.json").read_bytes())
    assert stored["source_release_version"] == "0.31.0.0"
    assert not list(tmp_path.glob(".homepage-snapshot.json.*.tmp"))  # noqa: ASYNC240


@pytest.mark.asyncio
async def test_worker_observes_another_materializers_same_bucket_publication(
    tmp_path: Path,
) -> None:
    pointer, releases = _write_release(tmp_path, version="0.30.0.0", record_count=42)
    await _service(pointer, releases).refresh_response(now=PUBLISHED_AT)
    first_worker = _service(pointer, releases)
    initial = await first_worker.resolve_response(now=PUBLISHED_AT)
    replacement = tmp_path / "replacement-worker-release"
    replacement.mkdir()
    next_pointer, next_releases = _write_release(
        replacement,
        version="0.31.0.0",
        record_count=99,
        events=[_event(7)],
    )
    for manifest in next_releases.iterdir():
        (releases / manifest.name).write_bytes(manifest.read_bytes())
    pointer.write_bytes(next_pointer.read_bytes())
    stale = await first_worker.resolve_response(now=PUBLISHED_AT)
    second_worker = _service(pointer, releases)
    rebuilt = await second_worker.refresh_response(now=PUBLISHED_AT)

    observed = await first_worker.resolve_response(now=PUBLISHED_AT)

    assert initial.snapshot.verified_record_count == 42
    assert stale.cache_status == "stale"
    assert rebuilt.cache_status == "rebuilt"
    assert observed.cache_status == "projection"
    assert observed.snapshot.state is CommonsSnapshotState.LIVE
    assert observed.snapshot.verified_record_count == 99


@pytest.mark.asyncio
async def test_request_does_not_wait_for_a_slow_background_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pointer, releases = _write_release(tmp_path, version="0.30.0.0", record_count=42)
    await _service(pointer, releases).refresh_response(now=PUBLISHED_AT)
    service = _service(pointer, releases)
    replacement = tmp_path / "slow-replacement"
    replacement.mkdir()
    next_pointer, next_releases = _write_release(
        replacement, version="0.31.0.0", record_count=99
    )
    for manifest in next_releases.iterdir():
        (releases / manifest.name).write_bytes(manifest.read_bytes())
    pointer.write_bytes(next_pointer.read_bytes())
    started = threading.Event()
    release = threading.Event()
    original_resolve = service._resolve_verified

    def paused_resolve(
        checked_at: datetime, **kwargs: object
    ):  # type: ignore[no-untyped-def]
        started.set()
        if not release.wait(timeout=5):
            raise TimeoutError("test did not release materializer")
        return original_resolve(checked_at, **kwargs)

    monkeypatch.setattr(service, "_resolve_verified", paused_resolve)
    refresh_task = asyncio.create_task(service.refresh_response(now=PUBLISHED_AT))
    assert await asyncio.to_thread(started.wait, 2)
    try:
        response = await asyncio.wait_for(
            service.resolve_response(now=PUBLISHED_AT), timeout=0.5
        )
    finally:
        release.set()
    rebuilt = await refresh_task

    assert response.cache_status == "stale"
    assert response.snapshot.verified_record_count == 42
    assert rebuilt.cache_status == "rebuilt"


@pytest.mark.asyncio
async def test_interrupted_checkpoint_replace_preserves_last_trusted_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pointer, releases = _write_release(tmp_path, version="0.30.0.0", record_count=42)
    service = _service(pointer, releases)
    await service.refresh_response(now=PUBLISHED_AT)
    checkpoint_path = tmp_path / "trusted-release.json"
    original_checkpoint = checkpoint_path.read_bytes()
    replacement = tmp_path / "checkpoint-replacement"
    replacement.mkdir()
    next_pointer, next_releases = _write_release(
        replacement, version="0.31.0.0", record_count=99
    )
    for manifest in next_releases.iterdir():
        (releases / manifest.name).write_bytes(manifest.read_bytes())
    pointer.write_bytes(next_pointer.read_bytes())
    original_replace = os.replace

    def interrupt_checkpoint(source: object, destination: object) -> None:
        if Path(destination) == checkpoint_path:
            raise OSError("simulated checkpoint publication interruption")
        original_replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", interrupt_checkpoint)
        interrupted = await service.refresh_response(now=PUBLISHED_AT)

    assert interrupted.cache_status == "stale"
    assert interrupted.snapshot.verified_record_count == 42
    assert checkpoint_path.read_bytes() == original_checkpoint
    assert not list(tmp_path.glob(".trusted-release.json.*.tmp"))  # noqa: ASYNC240

    recovered = await service.refresh_response(now=PUBLISHED_AT)
    assert recovered.cache_status == "rebuilt"
    assert recovered.snapshot.verified_record_count == 99


@pytest.mark.asyncio
async def test_interrupted_projection_replace_retains_prior_checkpoint_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pointer, releases = _write_release(tmp_path, version="0.30.0.0", record_count=42)
    service = _service(pointer, releases)
    await service.refresh_response(now=PUBLISHED_AT)
    replacement = tmp_path / "projection-replacement"
    replacement.mkdir()
    next_pointer, next_releases = _write_release(
        replacement, version="0.31.0.0", record_count=99
    )
    for manifest in next_releases.iterdir():
        (releases / manifest.name).write_bytes(manifest.read_bytes())
    pointer.write_bytes(next_pointer.read_bytes())
    projection_store = service._projection_store
    assert projection_store is not None

    with monkeypatch.context() as patch:
        patch.setattr(
            projection_store,
            "write",
            lambda _projection: (_ for _ in ()).throw(
                OSError("simulated projection publication interruption")
            ),
        )
        interrupted = await service.refresh_response(now=PUBLISHED_AT)

    restarted = _service(pointer, releases)
    durable = await restarted.resolve_response(now=PUBLISHED_AT)

    assert interrupted.cache_status == "stale"
    assert interrupted.snapshot.verified_record_count == 42
    assert durable.cache_status == "stale"
    assert durable.snapshot.verified_record_count == 42

    recovered = await restarted.refresh_response(now=PUBLISHED_AT)
    assert recovered.cache_status == "rebuilt"
    assert recovered.snapshot.verified_record_count == 99


@pytest.mark.asyncio
async def test_new_five_minute_bucket_rebuilds_the_whole_snapshot(tmp_path: Path) -> None:
    pointer, releases = _write_release(tmp_path, events=[_event(1)])
    service = _service(pointer, releases)

    first = await service.refresh_response(
        now=PUBLISHED_AT + timedelta(minutes=1, seconds=12)
    )
    second = await service.refresh_response(
        now=PUBLISHED_AT + timedelta(minutes=6, seconds=3)
    )

    assert first.snapshot.as_of == PUBLISHED_AT
    assert second.snapshot.as_of == PUBLISHED_AT + timedelta(minutes=5)
    assert first.snapshot.snapshot_id != second.snapshot.snapshot_id
    assert first.etag != second.etag
    assert service.metrics.rebuilds == 2


@pytest.mark.asyncio
async def test_request_bucket_miss_never_reads_or_rebuilds_signed_source(
    tmp_path: Path,
) -> None:
    pointer, releases = _write_release(tmp_path, record_count=81, events=[_event(1)])
    await _service(pointer, releases).refresh_response(now=PUBLISHED_AT)
    request_service = _service(pointer, releases)

    result = await request_service.resolve_response(
        now=PUBLISHED_AT + timedelta(minutes=5)
    )

    assert result.cache_status == "stale"
    assert result.snapshot.verified_record_count == 81
    assert request_service.metrics.projection_reads == 1
    assert request_service.metrics.source_artifact_reads == 0
    assert request_service.metrics.rebuilds == 0


@pytest.mark.asyncio
async def test_restart_retains_persisted_snapshot_as_atomic_stale_fallback(
    tmp_path: Path,
) -> None:
    pointer, releases = _write_release(tmp_path, record_count=81, events=[_event(1)])
    verified = await _service(pointer, releases).refresh_response(now=PUBLISHED_AT)
    pointer.write_text("not a signed pointer")

    restarted = _service(pointer, releases)
    stale = await restarted.refresh_response(now=PUBLISHED_AT + timedelta(minutes=5))

    assert verified.snapshot.state is CommonsSnapshotState.LIVE
    assert stale.cache_status == "stale"
    assert stale.snapshot.state is CommonsSnapshotState.STALE
    assert stale.snapshot.verified_record_count == 81
    assert stale.snapshot.activity.events == verified.snapshot.activity.events
    assert restarted.metrics.projection_reads == 1
    assert restarted.metrics.stale_fallbacks == 1


@pytest.mark.asyncio
async def test_tampered_projection_is_rebuilt_from_signed_source(tmp_path: Path) -> None:
    pointer, releases = _write_release(tmp_path, record_count=81)
    await _service(pointer, releases).refresh_response(now=PUBLISHED_AT)
    projection_path = tmp_path / "homepage-snapshot.json"
    projection = json.loads(projection_path.read_bytes())
    projection["snapshot"]["verified_record_count"] = 999_999
    projection_path.write_text(json.dumps(projection))

    restored = await _service(pointer, releases).refresh_response(now=PUBLISHED_AT)

    assert restored.cache_status == "rebuilt"
    assert restored.snapshot.verified_record_count == 81


@pytest.mark.asyncio
async def test_tampered_projection_is_not_a_stale_fallback(tmp_path: Path) -> None:
    pointer, releases = _write_release(tmp_path, record_count=81)
    await _service(pointer, releases).refresh_response(now=PUBLISHED_AT)
    projection_path = tmp_path / "homepage-snapshot.json"
    projection = json.loads(projection_path.read_bytes())
    projection["snapshot"]["verified_record_count"] = 999_999
    projection_path.write_text(json.dumps(projection))
    pointer.write_text("not a signed pointer")

    restored = await _service(pointer, releases).resolve_response(
        now=PUBLISHED_AT + timedelta(minutes=5)
    )

    assert restored.cache_status == "unavailable"
    assert restored.snapshot.state is CommonsSnapshotState.UNAVAILABLE
    assert restored.snapshot.verified_record_count is None


@pytest.mark.asyncio
async def test_checkpoint_mismatch_blocks_projection_stale_fallback(tmp_path: Path) -> None:
    pointer, releases = _write_release(tmp_path, version="0.31.0.0", record_count=99)
    await _service(pointer, releases).refresh_response(now=PUBLISHED_AT)
    projection_path = tmp_path / "homepage-snapshot.json"
    projection = json.loads(projection_path.read_bytes())
    projection["source_release_version"] = "0.30.0.0"
    projection["snapshot"]["release"]["version"] = "0.30.0.0"
    projection_path.write_text(json.dumps(projection))
    pointer.write_text("not a signed pointer")

    restored = await _service(pointer, releases).resolve_response(
        now=PUBLISHED_AT + timedelta(minutes=5)
    )

    assert restored.cache_status == "unavailable"
    assert restored.snapshot.state is CommonsSnapshotState.UNAVAILABLE


@pytest.mark.asyncio
async def test_projection_lock_failure_degrades_to_typed_unavailable(tmp_path: Path) -> None:
    pointer, releases = _write_release(tmp_path)
    invalid_parent = tmp_path / "not-a-directory"
    invalid_parent.write_text("file")
    service = PublicCommonsSnapshotService(
        latest_pointer_path=pointer,
        release_directory=releases,
        key_ring=KEY_RING,
        stale_after_seconds=300,
        checkpoint_path=tmp_path / "trusted-release.json",
        projection_path=invalid_parent / "homepage-snapshot.json",
    )

    result = await service.resolve_response(now=PUBLISHED_AT)

    assert result.cache_status == "unavailable"
    assert result.snapshot.state is CommonsSnapshotState.UNAVAILABLE


@pytest.mark.asyncio
async def test_oversize_projection_is_never_read_as_public_proof(tmp_path: Path) -> None:
    pointer, releases = _write_release(tmp_path)
    await _service(pointer, releases).refresh(now=PUBLISHED_AT)
    (tmp_path / "homepage-snapshot.json").write_bytes(b"{" + b"x" * (32 * 1024))
    pointer.write_text("not a signed pointer")

    result = await _service(pointer, releases).refresh_response(now=PUBLISHED_AT)

    assert result.cache_status == "unavailable"
    assert result.snapshot.state is CommonsSnapshotState.UNAVAILABLE
    assert result.response_bytes <= 24 * 1024


@pytest.mark.asyncio
async def test_oversize_event_summary_is_rejected_before_public_projection(
    tmp_path: Path,
) -> None:
    event = _event(1)
    event["summary"] = "x" * 241
    pointer, releases = _write_release(tmp_path, events=[event])

    result = await _service(pointer, releases).refresh_response(now=PUBLISHED_AT)

    assert result.cache_status == "unavailable"
    assert result.snapshot.state is CommonsSnapshotState.UNAVAILABLE
    assert result.snapshot.activity.events == ()


@pytest.mark.asyncio
async def test_event_without_record_link_is_rejected_before_public_projection(
    tmp_path: Path,
) -> None:
    event = _event(1)
    event.pop("href")
    pointer, releases = _write_release(tmp_path, events=[event])

    result = await _service(pointer, releases).refresh_response(now=PUBLISHED_AT)

    assert result.cache_status == "unavailable"
    assert result.snapshot.state is CommonsSnapshotState.UNAVAILABLE
    assert result.snapshot.activity.events == ()


@pytest.mark.asyncio
async def test_maximum_legal_four_event_snapshot_stays_under_response_budget(
    tmp_path: Path,
) -> None:
    events = [
        {
            "event_id": f"{index}" + "🫘" * 127,
            "event_type": ("food", "source", "portion", "pack")[index],
            "food_or_pack_id": "🥗" * 160,
            "food_locale": "界" * 80,
            "accepted_at": (PUBLISHED_AT - timedelta(minutes=index + 1)).isoformat(),
            "source_commit": "a" * 64,
            "href": "/" + "a" * 511,
            "summary": "🍲" * 240,
            "public_contributor_credit": "人" * 100,
        }
        for index in range(4)
    ]
    recent_record: dict[str, object] = {
        "record_id": "🥣" * 160,
        "name": "界" * 160,
        "food_locale": "人" * 80,
        "verified_at": (PUBLISHED_AT - timedelta(minutes=1)).isoformat(),
        "href": "/" + "a" * 511,
    }
    pointer, releases = _write_release(
        tmp_path,
        events=events,
        recent_record=recent_record,
    )

    result = await _service(pointer, releases).refresh_response(now=PUBLISHED_AT)

    assert result.snapshot.state is CommonsSnapshotState.LIVE
    assert len(result.snapshot.activity.events) == 4
    assert result.response_bytes == len(result.snapshot.model_dump_json().encode())
    assert result.response_bytes <= MAX_PUBLIC_SNAPSHOT_BYTES


@pytest.mark.asyncio
async def test_event_after_the_advertised_bucket_cutoff_is_not_counted(tmp_path: Path) -> None:
    future_event = _event(1)
    future_event["accepted_at"] = (PUBLISHED_AT + timedelta(minutes=2)).isoformat()
    pointer, releases = _write_release(
        tmp_path,
        events=[future_event],
        published_at=PUBLISHED_AT + timedelta(minutes=3),
    )

    snapshot = await _service(pointer, releases).refresh(
        now=PUBLISHED_AT + timedelta(minutes=4)
    )

    assert snapshot.state is CommonsSnapshotState.QUIET
    assert snapshot.activity.ends_at == PUBLISHED_AT
    assert snapshot.activity.accepted_count == 0


@pytest.mark.asyncio
async def test_recent_record_cannot_be_newer_than_its_release(tmp_path: Path) -> None:
    pointer, releases = _write_release(
        tmp_path, recent_verified_at=PUBLISHED_AT + timedelta(minutes=1)
    )

    snapshot = await _service(pointer, releases).refresh(now=PUBLISHED_AT)

    assert snapshot.state is CommonsSnapshotState.UNAVAILABLE
    assert snapshot.verified_record_count is None


@pytest.mark.asyncio
async def test_protocol_relative_recent_record_link_is_rejected(tmp_path: Path) -> None:
    pointer, releases = _write_release(tmp_path)
    manifest_path = releases / "release-0.30.0.0.json"
    envelope = json.loads(manifest_path.read_bytes())
    envelope["payload"]["most_recent_verified_record"]["href"] = "//attacker.example"
    manifest_bytes = _signed_envelope(envelope["payload"])
    manifest_path.write_bytes(manifest_bytes)
    pointer_payload = {
        "schema_version": "1",
        "release_version": "0.30.0.0",
        "manifest_filename": manifest_path.name,
        "manifest_digest": hashlib.sha256(manifest_bytes).hexdigest(),
    }
    pointer.write_bytes(_signed_envelope(pointer_payload))

    snapshot = await _service(pointer, releases).refresh(now=PUBLISHED_AT)

    assert snapshot.state is CommonsSnapshotState.UNAVAILABLE
    assert snapshot.verified_record_count is None


@pytest.mark.asyncio
async def test_durable_checkpoint_rejects_signed_release_rollback_after_restart(
    tmp_path: Path,
) -> None:
    pointer, releases = _write_release(tmp_path, version="0.31.0.0", record_count=99)
    verified = await _service(pointer, releases).refresh(now=PUBLISHED_AT)
    old_root = tmp_path / "old-release"
    old_root.mkdir()
    old_pointer, old_releases = _write_release(
        old_root,
        version="0.30.0.0",
        record_count=42,
        published_at=PUBLISHED_AT - timedelta(days=1),
    )
    pointer.write_bytes(old_pointer.read_bytes())
    for manifest in old_releases.iterdir():
        (releases / manifest.name).write_bytes(manifest.read_bytes())

    replayed = await _service(pointer, releases).refresh(
        now=PUBLISHED_AT + timedelta(minutes=5)
    )

    assert verified.verified_record_count == 99
    assert replayed.state is CommonsSnapshotState.STALE
    assert replayed.verified_record_count == 99
    assert replayed.release == verified.release
