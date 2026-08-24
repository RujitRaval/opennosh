from datetime import UTC, datetime

from fastapi.testclient import TestClient
from opennosh_api.main import create_app
from opennosh_api.public_commons.manifests import unavailable_snapshot
from opennosh_api.public_commons.schemas import CommonsSnapshotReason
from opennosh_api.settings import Settings


class UnavailableService:
    async def resolve(self):  # type: ignore[no-untyped-def]
        return unavailable_snapshot(
            checked_at=datetime(2026, 8, 23, 18, tzinfo=UTC),
            reason=CommonsSnapshotReason.NO_PUBLISHED_RELEASE,
        )


class ChangingUnavailableService:
    def __init__(self) -> None:
        self.calls = 0

    async def resolve(self):  # type: ignore[no-untyped-def]
        self.calls += 1
        reason = (
            CommonsSnapshotReason.NO_PUBLISHED_RELEASE
            if self.calls == 1
            else CommonsSnapshotReason.LATEST_RELEASE_UNAVAILABLE
        )
        return unavailable_snapshot(
            checked_at=datetime(2026, 8, 23, 18, tzinfo=UTC),
            reason=reason,
        )


def test_public_snapshot_has_stable_etag_and_supports_revalidation() -> None:
    app = create_app(Settings(_env_file=None))
    app.state.public_commons_snapshot_service = UnavailableService()

    with TestClient(app) as client:
        first = client.get("/api/v1/public/commons-snapshot")
        second = client.get(
            "/api/v1/public/commons-snapshot",
            headers={"If-None-Match": first.headers["etag"]},
        )

    assert first.status_code == 200
    assert first.json()["state"] == "unavailable"
    assert first.json()["verified_record_count"] is None
    assert first.headers["cache-control"].startswith("public, max-age=0")
    assert second.status_code == 304
    assert second.content == b""


def test_etag_changes_when_snapshot_content_changes_within_the_same_bucket() -> None:
    app = create_app(Settings(_env_file=None))
    app.state.public_commons_snapshot_service = ChangingUnavailableService()

    with TestClient(app) as client:
        first = client.get("/api/v1/public/commons-snapshot")
        second = client.get(
            "/api/v1/public/commons-snapshot",
            headers={"If-None-Match": first.headers["etag"]},
        )

    assert first.json()["snapshot_id"] == second.json()["snapshot_id"]
    assert first.headers["etag"] != second.headers["etag"]
    assert second.status_code == 200
