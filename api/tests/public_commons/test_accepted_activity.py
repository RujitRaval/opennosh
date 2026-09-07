from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from opennosh_api.foods.schemas import FoodSource
from opennosh_api.governance.contracts import ApprovedChangeSet, ApprovedFileChange
from opennosh_api.governance.models import GovernanceDecision
from opennosh_api.public.artifacts import (
    PublicFoodRecordResponse,
    PublicReadReleaseManifest,
    PublicReleaseMetadata,
    ResolvedRelease,
)
from opennosh_api.public_commons.accepted_activity import (
    ActivityProjectionUnavailable,
    CanonicalAcceptedActivitySource,
    _pack_locale,
    _version,
)
from opennosh_api.public_commons.manifests import SignedEnvelope
from opennosh_api.publication.models import AcceptedEvent, PublicationReceiptRecord
from opennosh_api.publication.receipts import (
    Ed25519ReceiptSigner,
    PublicationReceiptKeyRing,
    receipt_draft_from_snapshot,
    signed_receipt_digest,
)
from tests.publication.test_planner import snapshot

NOW = datetime(2026, 9, 5, 12, 5, tzinfo=UTC)
PUBLISHED_AT = datetime(2026, 9, 5, 11, 55, tzinfo=UTC)
EVENT_ID = UUID("11111111-1111-4111-8111-111111111119")
INTENT_ID = UUID("11111111-1111-4111-8111-111111111118")
DECISION_ID = UUID("44444444-4444-4444-8444-444444444444")
PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(b"a" * 32)
SIGNER = Ed25519ReceiptSigner(
    key_id="activity-test",
    publisher_identity="opennosh:activity-test",
    private_key=PRIVATE_KEY,
)
KEY_RING = PublicationReceiptKeyRing({"activity-test": PRIVATE_KEY.public_key()})
CHANGES = ApprovedChangeSet.build(
    pack_id="commons",
    files=(
        ApprovedFileChange(
            path="packs/commons/pack.yaml",
            content="id: commons\nlocale: en-US\n",
        ),
    ),
)


def _fixtures() -> tuple[
    ResolvedRelease,
    AcceptedEvent,
    PublicationReceiptRecord,
    GovernanceDecision,
    PublicFoodRecordResponse,
]:
    original_draft = receipt_draft_from_snapshot(snapshot(current=7))
    draft = original_draft.model_copy(
        update={
            "approved_payload_digest": CHANGES.digest,
            "release_version": "1.2.3.4",
            "published_at": PUBLISHED_AT,
            "verified_steps": tuple(
                proof.model_copy(update={"verified_at": PUBLISHED_AT})
                for proof in original_draft.verified_steps
            ),
        }
    )
    envelope = SIGNER.sign(draft)
    digest = signed_receipt_digest(envelope)
    manifest = PublicReadReleaseManifest(
        release_version="1.2.3.4",
        published_at=datetime(2026, 9, 5, 11, 50, tzinfo=UTC),
        publication_receipt_key=(
            "receipts/v1/11111111-1111-4111-8111-111111111111.json"
        ),
    )
    release = ResolvedRelease(
        manifest=manifest,
        manifest_envelope=SignedEnvelope(
            key_id="manifest-test",
            payload=manifest.model_dump(mode="json"),
            signature="A" * 86,
        ),
        manifest_bytes=b"manifest",
        publication_receipt_digest=digest,
        publication_receipt_published_at=PUBLISHED_AT,
        metadata=PublicReleaseMetadata(
            release_version="1.2.3.4",
            published_at=manifest.published_at,
            state="verified",
        ),
    )
    accepted = AcceptedEvent(
        id=EVENT_ID,
        publication_intent_id=INTENT_ID,
        repository="https://forge.example/opennosh/packs",
        commit_sha="b" * 40,
        pack_id="commons",
        record_id="lentils",
        event_type="record.published",
        receipt_digest=digest,
        published_at=PUBLISHED_AT,
    )
    receipt = PublicationReceiptRecord(
        publication_intent_id=INTENT_ID,
        receipt_digest=digest,
        envelope_json=envelope.model_dump(mode="json"),
    )
    decision = GovernanceDecision(
        id=DECISION_ID,
        approved_payload_digest=CHANGES.digest,
        approved_changes_json=CHANGES.as_json(),
    )
    record = PublicFoodRecordResponse.model_validate(
        {
            "record": {
                "id": "community:lentils",
                "source": "community",
                "source_id": "lentils",
                "name": "Lentils",
                "attribution": {
                    "source": "community",
                    "license": "CC0-1.0",
                    "contributed_by": "Commons kitchen",
                    "pack_id": "commons",
                    "pack_version": "1.0.0",
                },
                "nutrients": {},
                "portions": [],
            },
            "release": release.metadata.model_dump(mode="json"),
            "immutable_url": "https://artifacts.example.test/foods/lentils",
            "provenance_url": "https://artifacts.example.test/provenance/lentils",
        }
    )
    return release, accepted, receipt, decision, record


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _Transaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_arguments: object) -> None:
        return None


class _Session:
    def __init__(self, scalars: list[Any], results: list[list[Any]]) -> None:
        self._scalars = iter(scalars)
        self._results = iter(results)

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_arguments: object) -> None:
        return None

    def begin(self) -> _Transaction:
        return _Transaction()

    async def scalar(self, _statement: object) -> Any:
        return next(self._scalars)

    async def execute(self, _statement: object) -> _Result:
        return _Result(next(self._results))


class _Factory:
    def __init__(self, session: _Session) -> None:
        self._session = session

    def __call__(self) -> _Session:
        return self._session


class _ArtifactReader:
    def __init__(self, record: PublicFoodRecordResponse, *, fail: bool = False) -> None:
        self.record = record
        self.fail = fail
        self.calls: list[tuple[FoodSource, str, str | None]] = []

    async def food(
        self,
        source: FoodSource,
        source_id: str,
        *,
        release_version: str | None = None,
        now: datetime | None = None,
    ) -> PublicFoodRecordResponse:
        del now
        self.calls.append((source, source_id, release_version))
        if self.fail:
            raise RuntimeError("origin unavailable")
        return self.record


def _source(
    session: _Session,
    reader: _ArtifactReader,
) -> CanonicalAcceptedActivitySource:
    return CanonicalAcceptedActivitySource(  # type: ignore[arg-type]
        _Factory(session),
        receipt_keys=KEY_RING,
        artifact_reader=reader,
    )


@pytest.mark.asyncio
async def test_projection_verifies_release_ledger_receipt_governance_and_artifact() -> None:
    release, accepted, receipt, decision, record = _fixtures()
    identity = (
        accepted.id,
        accepted.receipt_digest,
        accepted.published_at,
        accepted.event_type,
        accepted.record_id,
        accepted.pack_id,
        accepted.commit_sha,
        accepted.repository,
    )
    session = _Session(
        [accepted.id, None, accepted.id],
        [[], [identity], [(accepted, receipt, decision)]],
    )
    reader = _ArtifactReader(record)

    projection = await _source(session, reader).project(release=release, checked_at=NOW)

    assert projection.accepted_count == 1
    assert len(projection.event_checkpoint) == 64
    assert len(projection.events) == 1
    assert projection.events[0].summary == "Accepted Lentils as a verified food record."
    assert projection.events[0].public_contributor_credit == "Commons kitchen"
    assert projection.most_recent_verified_record is not None
    assert projection.most_recent_verified_record.name == "Lentils"
    assert reader.calls == [
        (FoodSource.COMMUNITY, "lentils", "1.2.3.4"),
        (FoodSource.COMMUNITY, "lentils", "1.2.3.4"),
    ]


@pytest.mark.asyncio
async def test_projection_rejects_release_without_receipt_cutoff() -> None:
    release, _, _, _, record = _fixtures()
    release = ResolvedRelease(
        manifest=release.manifest,
        manifest_envelope=release.manifest_envelope,
        manifest_bytes=release.manifest_bytes,
        publication_receipt_digest=release.publication_receipt_digest,
        metadata=release.metadata,
    )

    with pytest.raises(ActivityProjectionUnavailable, match="cutoff"):
        await _source(_Session([], []), _ArtifactReader(record)).project(
            release=release,
            checked_at=NOW,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("current_event,newer_event", [(None, None), (EVENT_ID, EVENT_ID)])
async def test_projection_rejects_incomplete_release_boundary(
    current_event: UUID | None,
    newer_event: UUID | None,
) -> None:
    release, _, _, _, record = _fixtures()
    session = _Session([current_event, newer_event, None], [[], []])

    with pytest.raises(ActivityProjectionUnavailable, match="release_boundary"):
        await _source(session, _ArtifactReader(record)).project(
            release=release,
            checked_at=NOW,
        )


@pytest.mark.asyncio
async def test_projection_rejects_missing_governance_detail() -> None:
    release, accepted, _, _, record = _fixtures()
    identity = (
        accepted.id,
        accepted.receipt_digest,
        accepted.published_at,
        accepted.event_type,
        accepted.record_id,
        accepted.pack_id,
        accepted.commit_sha,
        accepted.repository,
    )
    session = _Session([accepted.id, None, accepted.id], [[], [identity], []])

    with pytest.raises(ActivityProjectionUnavailable, match="governance_proof_missing"):
        await _source(session, _ArtifactReader(record)).project(
            release=release,
            checked_at=NOW,
        )


@pytest.mark.asyncio
async def test_projection_rejects_oversized_activity_window() -> None:
    release, accepted, _, _, record = _fixtures()
    identity = (
        accepted.id,
        accepted.receipt_digest,
        accepted.published_at,
        accepted.event_type,
        accepted.record_id,
        accepted.pack_id,
        accepted.commit_sha,
        accepted.repository,
    )
    session = _Session(
        [accepted.id, None, None],
        [[], [identity] * 10_001],
    )

    with pytest.raises(ActivityProjectionUnavailable, match="window_too_large"):
        await _source(session, _ArtifactReader(record)).project(
            release=release,
            checked_at=NOW,
        )


@pytest.mark.asyncio
async def test_projection_fails_closed_when_exact_artifact_is_unavailable() -> None:
    release, accepted, receipt, decision, record = _fixtures()

    with pytest.raises(ActivityProjectionUnavailable, match="artifact_unavailable"):
        await _source(_Session([], []), _ArtifactReader(record, fail=True))._public_event(
            accepted,
            receipt,
            decision,
            release=release,
        )


@pytest.mark.asyncio
async def test_public_event_rejects_receipt_and_artifact_binding_drift() -> None:
    release, accepted, receipt, decision, record = _fixtures()
    accepted.commit_sha = "c" * 40
    source = _source(_Session([], []), _ArtifactReader(record))
    with pytest.raises(ActivityProjectionUnavailable, match="receipt_binding_invalid"):
        await source._public_event(accepted, receipt, decision, release=release)

    accepted.commit_sha = "b" * 40
    mismatched_record = record.model_copy(
        update={
            "record": record.record.model_copy(
                update={
                    "attribution": record.record.attribution.model_copy(
                        update={"pack_id": "different-pack"}
                    )
                }
            )
        }
    )
    with pytest.raises(ActivityProjectionUnavailable, match="artifact_binding_invalid"):
        await _source(_Session([], []), _ArtifactReader(mismatched_record))._public_event(
            accepted,
            receipt,
            decision,
            release=release,
        )


def test_activity_pack_locale_and_release_version_validation_fail_closed() -> None:
    missing = ApprovedChangeSet.build(
        pack_id="commons",
        files=(ApprovedFileChange(path="packs/commons/food.yaml", content="id: food\n"),),
    )
    malformed = ApprovedChangeSet.build(
        pack_id="commons",
        files=(ApprovedFileChange(path="packs/commons/pack.yaml", content="["),),
    )
    wrong_pack = ApprovedChangeSet.build(
        pack_id="commons",
        files=(ApprovedFileChange(path="packs/commons/pack.yaml", content="id: other\n"),),
    )
    missing_locale = ApprovedChangeSet.build(
        pack_id="commons",
        files=(ApprovedFileChange(path="packs/commons/pack.yaml", content="id: commons\n"),),
    )

    for changes, message in (
        (missing, "manifest_missing"),
        (malformed, "manifest_invalid"),
        (wrong_pack, "manifest_invalid"),
        (missing_locale, "locale_invalid"),
    ):
        with pytest.raises(ActivityProjectionUnavailable, match=message):
            _pack_locale(changes)

    with pytest.raises(ActivityProjectionUnavailable, match="version_invalid"):
        _version("one.two.three.four")
    with pytest.raises(ActivityProjectionUnavailable, match="version_invalid"):
        _version("1.2.3")
