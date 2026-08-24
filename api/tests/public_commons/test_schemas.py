from datetime import UTC, datetime

import pytest
from opennosh_api.public_commons.manifests import unavailable_snapshot
from opennosh_api.public_commons.schemas import (
    CommonsSnapshotReason,
    PublicCommonsSnapshot,
)
from pydantic import ValidationError

CHECKED_AT = datetime(2026, 8, 23, 18, tzinfo=UTC)


def _unavailable_payload() -> dict[str, object]:
    return unavailable_snapshot(
        checked_at=CHECKED_AT,
        reason=CommonsSnapshotReason.NO_PUBLISHED_RELEASE,
    ).model_dump(mode="json")


def test_unavailable_snapshot_rejects_either_partial_proof_field() -> None:
    release = {
        "version": "0.30.0.0",
        "manifest_digest": "a" * 64,
        "publication_receipt_digest": "b" * 64,
        "published_at": CHECKED_AT.isoformat(),
    }
    for field, value in (("release", release), ("verified_record_count", 42)):
        payload = _unavailable_payload()
        payload[field] = value

        with pytest.raises(ValidationError):
            PublicCommonsSnapshot.model_validate(payload)


def test_quiet_snapshot_rejects_nonzero_accepted_activity() -> None:
    payload = _unavailable_payload()
    payload.update(
        {
            "state": "quiet",
            "release": {
                "version": "0.30.0.0",
                "manifest_digest": "a" * 64,
                "publication_receipt_digest": "b" * 64,
                "published_at": CHECKED_AT.isoformat(),
            },
            "verified_record_count": 42,
            "freshness": {
                "release": "verified",
                "activity": "verified",
                "checked_at": CHECKED_AT.isoformat(),
                "stale_since": None,
            },
            "reasons": [],
        }
    )
    activity = payload["activity"]
    assert isinstance(activity, dict)
    activity["accepted_count"] = 3

    with pytest.raises(ValidationError):
        PublicCommonsSnapshot.model_validate(payload)
