from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from opennosh_api.missions.contracts import (
    AcceptedMissionFact,
    MissionBindingFact,
    MissionProgressRecord,
)
from opennosh_api.missions.projector import MissionProjectionError, project_mission_progress

NOW = datetime(2026, 9, 2, 12, tzinfo=UTC)
MISSION_ID = UUID("10000000-0000-4000-8000-000000000001")
DEFINITION_ID = UUID("20000000-0000-4000-8000-000000000001")
DRAFT_ID = UUID("30000000-0000-4000-8000-000000000001")


def _binding(*, draft_id: UUID = DRAFT_ID, version: int = 1) -> MissionBindingFact:
    return MissionBindingFact(
        mission_id=MISSION_ID,
        definition_id=DEFINITION_ID,
        source_draft_id=draft_id,
        source_draft_version=version,
    )


def _event(
    *,
    event_id: UUID | None = None,
    receipt: str = "a" * 64,
    prior: str | None = None,
    event_type: str = "publication",
    draft_id: UUID = DRAFT_ID,
    version: int = 1,
    record_id: str = "food-1",
    published_at: datetime = NOW,
    activity_locale: str | None = None,
    activity_pack_version: str | None = None,
    activity_source_digest: str | None = None,
) -> AcceptedMissionFact:
    return AcceptedMissionFact(
        event_id=event_id or uuid4(),
        receipt_digest=receipt,
        prior_receipt_digest=prior,
        repository="github:RujitRaval/opennosh",
        commit_sha="b" * 40,
        pack_id="opennosh-starter",
        record_id=record_id,
        event_type=event_type,  # type: ignore[arg-type]
        published_at=published_at,
        source_draft_id=draft_id,
        source_draft_version=version,
        activity_locale=activity_locale,
        activity_pack_version=activity_pack_version,
        activity_source_digest=activity_source_digest,
    )


def _project(events: list[AcceptedMissionFact], bindings: list[MissionBindingFact] | None = None):
    return project_mission_progress(
        mission_id=MISSION_ID,
        definition_id=DEFINITION_ID,
        bindings=bindings or [_binding()],
        accepted_events=events,
    )


def test_projection_counts_only_directly_bound_publications() -> None:
    unbound = _event(
        receipt="c" * 64,
        draft_id=UUID("40000000-0000-4000-8000-000000000001"),
        record_id="food-2",
    )
    report = _project([unbound, _event()])

    assert report.accepted_count == 1
    assert report.matched_event_count == 1
    assert [record.record_id for record in report.records] == ["food-1"]


def test_projection_is_order_independent_and_collapses_exact_replays() -> None:
    first = _event(event_id=uuid4())
    correction = _event(
        event_id=uuid4(),
        receipt="c" * 64,
        prior=first.receipt_digest,
        event_type="correction",
        draft_id=uuid4(),
        published_at=NOW + timedelta(minutes=1),
    )

    ordered = _project([first, correction])
    reordered = _project([correction, first, first, correction])

    assert reordered == ordered
    assert ordered.accepted_count == 1
    assert ordered.matched_event_count == 2
    assert ordered.records[0].accepted_event_id == correction.event_id


def test_projection_orders_equal_time_events_by_lineage_not_receipt_hash() -> None:
    publication = _event(receipt="f" * 64)
    correction = _event(
        receipt="0" * 64,
        prior=publication.receipt_digest,
        event_type="correction",
        draft_id=uuid4(),
        published_at=publication.published_at,
    )

    report = _project([correction, publication])

    assert report.matched_event_count == 2
    assert report.records[0].accepted_event_id == correction.event_id


def test_projection_digest_normalizes_equivalent_timezone_offsets() -> None:
    event = _event()
    offset_event = event.model_copy(
        update={"published_at": event.published_at.astimezone(timezone(timedelta(hours=-4)))}
    )

    assert _project([event]).event_set_digest == _project([offset_event]).event_set_digest


def test_projection_binds_activity_locale_proof_into_record_and_checkpoint_digest() -> None:
    event = _event()
    proven = event.model_copy(
        update={
            "activity_locale": "en-US",
            "activity_pack_version": "1.2.3",
            "activity_source_digest": "c" * 64,
        }
    )

    original = _project([event])
    enriched = _project([proven])

    assert enriched.event_set_digest != original.event_set_digest
    assert enriched.records[0].activity_locale == "en-US"
    assert enriched.records[0].activity_pack_version == "1.2.3"
    assert enriched.records[0].activity_source_digest == "c" * 64


def test_activity_proof_contracts_reject_partial_material() -> None:
    with pytest.raises(ValueError, match="accepted event activity proof must be all present"):
        _event(activity_locale="en-US")

    with pytest.raises(ValueError, match="mission record activity proof must be all present"):
        MissionProgressRecord(
            repository="github:RujitRaval/opennosh",
            pack_id="opennosh-starter",
            record_id="food-1",
            accepted_event_id=uuid4(),
            receipt_digest="a" * 64,
            activity_locale="en-US",
            published_at=NOW,
        )


def test_revocation_removes_the_record_without_erasing_history() -> None:
    publication = _event()
    revocation = _event(
        receipt="d" * 64,
        prior=publication.receipt_digest,
        event_type="revocation",
        draft_id=uuid4(),
        published_at=NOW + timedelta(minutes=1),
    )

    report = _project([revocation, publication])

    assert report.accepted_count == 0
    assert report.matched_event_count == 2
    assert report.records == ()


@pytest.mark.parametrize(
    ("events", "code"),
    [
        (
            [
                _event(
                    receipt="c" * 64,
                    prior="f" * 64,
                    event_type="correction",
                )
            ],
            "lineage_missing",
        ),
        (
            [
                _event(),
                _event(
                    receipt="c" * 64,
                    prior="a" * 64,
                    event_type="correction",
                    record_id="food-2",
                    published_at=NOW + timedelta(minutes=1),
                ),
            ],
            "lineage_identity_mismatch",
        ),
        (
            [
                _event(),
                _event(
                    receipt="c" * 64,
                    prior="a" * 64,
                    event_type="correction",
                    published_at=NOW - timedelta(minutes=1),
                ),
            ],
            "lineage_time_invalid",
        ),
    ],
)
def test_projection_fails_closed_on_invalid_lineage(
    events: list[AcceptedMissionFact], code: str
) -> None:
    with pytest.raises(MissionProjectionError, match=code) as caught:
        _project(events)
    assert caught.value.code == code


def test_projection_rejects_conflicting_duplicate_event_identity() -> None:
    event_id = uuid4()
    first = _event(event_id=event_id)
    conflict = _event(event_id=event_id, record_id="different")

    with pytest.raises(MissionProjectionError, match="accepted_event_conflict"):
        _project([first, conflict])


def test_projection_rejects_cross_definition_bindings() -> None:
    wrong = MissionBindingFact(
        mission_id=MISSION_ID,
        definition_id=uuid4(),
        source_draft_id=DRAFT_ID,
        source_draft_version=1,
    )

    with pytest.raises(MissionProjectionError, match="binding_scope_mismatch"):
        _project([_event()], [wrong])


def test_projection_rejects_a_bound_lineage_cycle() -> None:
    first = _event(receipt="a" * 64, prior="b" * 64, event_type="correction")
    second = _event(receipt="b" * 64, prior="a" * 64, event_type="correction")

    with pytest.raises(MissionProjectionError, match="lineage_cycle") as caught:
        _project([first, second])
    assert caught.value.code == "lineage_cycle"
