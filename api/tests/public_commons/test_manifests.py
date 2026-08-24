import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from opennosh_api.public_commons.manifests import (
    ManifestKeyRing,
    PublicCommonsSnapshotService,
    canonical_json,
)
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
        "most_recent_verified_record": {
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
    )


@pytest.mark.asyncio
async def test_signed_release_drives_one_verified_quiet_snapshot(tmp_path: Path) -> None:
    pointer, releases = _write_release(tmp_path)

    snapshot = await _service(pointer, releases).resolve(now=PUBLISHED_AT + timedelta(hours=1))

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

    snapshot = await _service(pointer, releases).resolve(now=PUBLISHED_AT)

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

    live = await service.resolve(now=PUBLISHED_AT)
    quiet = await service.resolve(now=PUBLISHED_AT + timedelta(hours=26))

    assert live.state is CommonsSnapshotState.LIVE
    assert quiet.state is CommonsSnapshotState.QUIET
    assert quiet.activity.accepted_count == 0
    assert quiet.verified_record_count == live.verified_record_count


@pytest.mark.asyncio
async def test_projection_lag_is_partial_without_inventing_events(tmp_path: Path) -> None:
    pointer, releases = _write_release(tmp_path, events=[_event(1)], projection_complete=False)

    snapshot = await _service(pointer, releases).resolve(now=PUBLISHED_AT)

    assert snapshot.state is CommonsSnapshotState.PARTIAL
    assert snapshot.freshness.activity == "partial"
    assert [reason.value for reason in snapshot.reasons] == ["activity_projection_lag"]


@pytest.mark.asyncio
async def test_failed_revalidation_retains_only_last_verified_snapshot(tmp_path: Path) -> None:
    pointer, releases = _write_release(tmp_path, record_count=81)
    service = _service(pointer, releases)
    verified = await service.resolve(now=PUBLISHED_AT)
    pointer.write_text("not a signed pointer")

    stale = await service.resolve(now=PUBLISHED_AT + timedelta(hours=2))

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

    snapshot = await _service(tmp_path / "missing.json", releases).resolve(now=PUBLISHED_AT)

    assert snapshot.state is CommonsSnapshotState.UNAVAILABLE
    assert [reason.value for reason in snapshot.reasons] == ["no_published_release"]


@pytest.mark.asyncio
async def test_invalid_first_release_never_claims_a_verified_count(tmp_path: Path) -> None:
    pointer, releases = _write_release(tmp_path)
    pointer.write_text("not a signed pointer")

    snapshot = await _service(pointer, releases).resolve(now=PUBLISHED_AT)

    assert snapshot.state is CommonsSnapshotState.UNAVAILABLE
    assert snapshot.release is None
    assert snapshot.verified_record_count is None
    assert snapshot.activity.events == ()


@pytest.mark.asyncio
async def test_invalid_release_manifest_has_a_typed_unavailable_reason(tmp_path: Path) -> None:
    pointer, releases = _write_release(tmp_path)
    (releases / "release-0.30.0.0.json").write_text("corrupt manifest")

    snapshot = await _service(pointer, releases).resolve(now=PUBLISHED_AT)

    assert snapshot.state is CommonsSnapshotState.UNAVAILABLE
    assert [reason.value for reason in snapshot.reasons] == ["invalid_release_manifest"]


@pytest.mark.asyncio
async def test_duplicate_accepted_event_cannot_increment_the_release_count_twice(
    tmp_path: Path,
) -> None:
    duplicated = _event(1)
    pointer, releases = _write_release(tmp_path, events=[duplicated, duplicated])

    snapshot = await _service(pointer, releases).resolve(now=PUBLISHED_AT)

    assert snapshot.state is CommonsSnapshotState.UNAVAILABLE
    assert snapshot.verified_record_count is None


@pytest.mark.asyncio
async def test_pointer_resolution_stays_on_one_release_during_latest_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pointer, releases = _write_release(tmp_path, version="0.30.0.0", record_count=42)
    original_read = PublicCommonsSnapshotService._read_envelope
    reads = 0

    def read_then_replace(path: Path):  # type: ignore[no-untyped-def]
        nonlocal reads
        envelope = original_read(path)
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
        PublicCommonsSnapshotService, "_read_envelope", staticmethod(read_then_replace)
    )

    snapshot = await _service(pointer, releases).resolve(now=PUBLISHED_AT)

    assert reads == 1
    assert snapshot.release is not None
    assert snapshot.release.version == "0.30.0.0"
    assert snapshot.verified_record_count == 42


@pytest.mark.asyncio
async def test_snapshot_uses_one_stable_five_minute_bucket(tmp_path: Path) -> None:
    pointer, releases = _write_release(tmp_path, events=[_event(1)])
    service = _service(pointer, releases)

    first = await service.resolve(now=PUBLISHED_AT + timedelta(minutes=1, seconds=12))
    second = await service.resolve(now=PUBLISHED_AT + timedelta(minutes=4, seconds=59))

    assert first == second
    assert first.as_of == PUBLISHED_AT
    assert first.activity.ends_at == PUBLISHED_AT


@pytest.mark.asyncio
async def test_event_after_the_advertised_bucket_cutoff_is_not_counted(tmp_path: Path) -> None:
    future_event = _event(1)
    future_event["accepted_at"] = (PUBLISHED_AT + timedelta(minutes=2)).isoformat()
    pointer, releases = _write_release(
        tmp_path,
        events=[future_event],
        published_at=PUBLISHED_AT + timedelta(minutes=3),
    )

    snapshot = await _service(pointer, releases).resolve(now=PUBLISHED_AT + timedelta(minutes=4))

    assert snapshot.state is CommonsSnapshotState.QUIET
    assert snapshot.activity.ends_at == PUBLISHED_AT
    assert snapshot.activity.accepted_count == 0


@pytest.mark.asyncio
async def test_recent_record_cannot_be_newer_than_its_release(tmp_path: Path) -> None:
    pointer, releases = _write_release(
        tmp_path, recent_verified_at=PUBLISHED_AT + timedelta(minutes=1)
    )

    snapshot = await _service(pointer, releases).resolve(now=PUBLISHED_AT)

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

    snapshot = await _service(pointer, releases).resolve(now=PUBLISHED_AT)

    assert snapshot.state is CommonsSnapshotState.UNAVAILABLE
    assert snapshot.verified_record_count is None


@pytest.mark.asyncio
async def test_durable_checkpoint_rejects_signed_release_rollback_after_restart(
    tmp_path: Path,
) -> None:
    pointer, releases = _write_release(tmp_path, version="0.31.0.0", record_count=99)
    verified = await _service(pointer, releases).resolve(now=PUBLISHED_AT)
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

    replayed = await _service(pointer, releases).resolve(now=PUBLISHED_AT + timedelta(minutes=5))

    assert verified.verified_record_count == 99
    assert replayed.state is CommonsSnapshotState.UNAVAILABLE
    assert replayed.verified_record_count is None
