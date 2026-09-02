from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from opennosh_api.missions.contracts import (
    AcceptedMissionFact,
    MissionBindingFact,
)
from opennosh_api.missions.projector import project_mission_progress
from opennosh_api.missions.repository import MissionRepository

NOW = datetime(2026, 9, 2, 18, tzinfo=UTC)
MISSION_ID = UUID("10000000-0000-4000-8000-000000000020")
DEFINITION_ID = UUID("20000000-0000-4000-8000-000000000020")
DRAFT_ID = UUID("30000000-0000-4000-8000-000000000020")


class _ScalarRows:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return self._rows


class _ExecutedRows:
    def __init__(self, rows: list[tuple[object, object, object]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[object, object, object]]:
        return self._rows


class FakeSession:
    def __init__(
        self,
        *,
        bindings: list[object],
        accepted_rows: list[tuple[object, object, object]],
        stored_rows: list[object],
    ) -> None:
        self.scalar_rows = [bindings, stored_rows]
        self.accepted_rows = accepted_rows

    async def scalars(self, _statement: object) -> _ScalarRows:
        return _ScalarRows(self.scalar_rows.pop(0))

    async def execute(self, _statement: object) -> _ExecutedRows:
        return _ExecutedRows(self.accepted_rows)


def _accepted_row(
    *,
    receipt_digest: str,
    commit_sha: str,
    event_type: str,
    published_at: datetime,
    prior_receipt_digest: str | None,
    intent: object | None,
) -> tuple[object, object, object | None]:
    accepted = SimpleNamespace(
        id=uuid4(),
        receipt_digest=receipt_digest,
        repository="github:RujitRaval/opennosh",
        commit_sha=commit_sha,
        pack_id="opennosh-starter",
        record_id="food-1",
        event_type=event_type,
        published_at=published_at,
    )
    receipt = SimpleNamespace(
        receipt_digest=receipt_digest,
        prior_receipt_digest=prior_receipt_digest,
        pack_id=accepted.pack_id,
        record_id=accepted.record_id,
        event_type=event_type,
        published_at=published_at,
        envelope_json={"receipt": {"merged_commit": commit_sha}},
    )
    return accepted, receipt, intent


@pytest.mark.asyncio
async def test_current_progress_follows_correction_lineage_and_materialized_records() -> None:
    intent = SimpleNamespace(source_draft_id=DRAFT_ID, source_draft_version=1)
    publication = _accepted_row(
        receipt_digest="a" * 64,
        commit_sha="b" * 40,
        event_type="publication",
        published_at=NOW,
        prior_receipt_digest=None,
        intent=intent,
    )
    correction = _accepted_row(
        receipt_digest="c" * 64,
        commit_sha="d" * 40,
        event_type="correction",
        published_at=NOW + timedelta(minutes=1),
        prior_receipt_digest="a" * 64,
        intent=None,
    )
    binding = SimpleNamespace(
        mission_id=MISSION_ID,
        definition_id=DEFINITION_ID,
        source_draft_id=DRAFT_ID,
        source_draft_version=1,
    )
    facts = tuple(
        AcceptedMissionFact(
            event_id=row[0].id,
            receipt_digest=row[0].receipt_digest,
            prior_receipt_digest=row[1].prior_receipt_digest,
            repository=row[0].repository,
            commit_sha=row[0].commit_sha,
            pack_id=row[0].pack_id,
            record_id=row[0].record_id,
            event_type=row[0].event_type,
            published_at=row[0].published_at,
            source_draft_id=intent.source_draft_id if row[2] is not None else UUID(int=0),
            source_draft_version=intent.source_draft_version if row[2] is not None else 1,
        )
        for row in (publication, correction)
    )
    projected = project_mission_progress(
        mission_id=MISSION_ID,
        definition_id=DEFINITION_ID,
        bindings=(
            MissionBindingFact(
                mission_id=MISSION_ID,
                definition_id=DEFINITION_ID,
                source_draft_id=DRAFT_ID,
                source_draft_version=1,
            ),
        ),
        accepted_events=facts,
    )
    active = projected.records[0]
    checkpoint = SimpleNamespace(
        id=uuid4(),
        mission_id=MISSION_ID,
        definition_id=DEFINITION_ID,
        accepted_count=projected.accepted_count,
        matched_event_count=projected.matched_event_count,
        event_set_digest=projected.event_set_digest,
    )
    stored = SimpleNamespace(
        repository=active.repository,
        pack_id=active.pack_id,
        record_id=active.record_id,
        accepted_event_id=active.accepted_event_id,
        published_at=active.published_at,
    )
    repository = MissionRepository(  # type: ignore[arg-type]
        FakeSession(
            bindings=[binding],
            accepted_rows=[publication, correction],  # type: ignore[list-item]
            stored_rows=[stored],
        )
    )

    assert await repository.progress_is_current(checkpoint)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_current_progress_fails_closed_on_invalid_relevant_receipt() -> None:
    intent = SimpleNamespace(source_draft_id=DRAFT_ID, source_draft_version=1)
    accepted, receipt, _intent = _accepted_row(
        receipt_digest="a" * 64,
        commit_sha="b" * 40,
        event_type="publication",
        published_at=NOW,
        prior_receipt_digest=None,
        intent=intent,
    )
    receipt.envelope_json = {"receipt": {"merged_commit": "e" * 40}}
    binding = SimpleNamespace(
        mission_id=MISSION_ID,
        definition_id=DEFINITION_ID,
        source_draft_id=DRAFT_ID,
        source_draft_version=1,
    )
    repository = MissionRepository(  # type: ignore[arg-type]
        FakeSession(
            bindings=[binding],
            accepted_rows=[(accepted, receipt, intent)],
            stored_rows=[],
        )
    )
    checkpoint = SimpleNamespace(
        id=uuid4(),
        mission_id=MISSION_ID,
        definition_id=DEFINITION_ID,
    )

    assert not await repository.progress_is_current(checkpoint)  # type: ignore[arg-type]
