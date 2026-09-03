from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from opennosh_api.governance.contracts import ApprovedChangeSet, ApprovedFileChange
from opennosh_api.missions.contracts import (
    AcceptedMissionFact,
    MissionBindingFact,
)
from opennosh_api.missions.projector import project_mission_progress
from opennosh_api.missions.repository import (
    MissionRepository,
    _accepted_mission_fact,
    _activity_locale_proof,
)

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
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return self._rows

    def scalars(self) -> _ScalarRows:
        return _ScalarRows([row[0] if isinstance(row, tuple) else row for row in self._rows])


class FakeSession:
    def __init__(
        self,
        *,
        bindings: list[object],
        accepted_rows: list[tuple[object, object, object, object]],
        stored_rows: list[object],
    ) -> None:
        self.scalar_rows = [bindings, stored_rows]
        self.accepted_rows = accepted_rows
        self.execute_count = 0

    async def scalar(self, _statement: object) -> object:
        return SimpleNamespace(
            id=DEFINITION_ID,
            mission_id=MISSION_ID,
            target_pack_id="opennosh-starter",
        )

    async def scalars(self, _statement: object) -> _ScalarRows:
        return _ScalarRows(self.scalar_rows.pop(0))

    async def execute(self, _statement: object, _parameters: object | None = None) -> _ExecutedRows:
        self.execute_count += 1
        if self.execute_count == 1:
            return _ExecutedRows([(row[0].receipt_digest,) for row in self.accepted_rows])
        return _ExecutedRows(self.accepted_rows)


def _accepted_row(
    *,
    receipt_digest: str,
    commit_sha: str,
    event_type: str,
    published_at: datetime,
    prior_receipt_digest: str | None,
    intent: object | None,
) -> tuple[object, object, object | None, object | None]:
    publication_intent_id = uuid4()
    publication_id = uuid4()
    accepted = SimpleNamespace(
        id=uuid4(),
        schema_version="1.0",
        publication_intent_id=publication_intent_id,
        receipt_digest=receipt_digest,
        repository="github:RujitRaval/opennosh",
        commit_sha=commit_sha,
        pack_id="opennosh-starter",
        record_id="food-1",
        event_type={
            "publication": "record.published",
            "correction": "record.corrected",
            "revocation": "record.revoked",
        }[event_type],
        published_at=published_at,
    )
    receipt = SimpleNamespace(
        publication_id=publication_id,
        schema_version="1.0",
        publication_intent_id=publication_intent_id,
        receipt_digest=receipt_digest,
        prior_receipt_digest=prior_receipt_digest,
        pack_id=accepted.pack_id,
        record_id=accepted.record_id,
        event_type=event_type,
        published_at=published_at,
        reconciled_at=published_at,
        signature_key_id="test-key",
        envelope_json={
            "receipt": {
                "schema_version": "1.0",
                "publication_id": str(publication_id),
                "event_type": event_type,
                "prior_receipt_digest": prior_receipt_digest,
                "pack_id": accepted.pack_id,
                "record_id": accepted.record_id,
                "merged_commit": commit_sha,
                "published_at": published_at.isoformat(),
                "verified_steps": [
                    {
                        "step": "commit_record",
                        "destination": accepted.repository,
                        "external_reference": commit_sha,
                    }
                ],
            },
            "signature_key_id": "test-key",
        },
    )
    return accepted, receipt, intent, None


def _activity_proof_material() -> tuple[object, object, object, object, str]:
    changes = ApprovedChangeSet.build(
        pack_id="opennosh-starter",
        files=(
            ApprovedFileChange(
                path="packs/opennosh-starter/foods/food-1.yaml",
                content="- slug: food-1\n",
            ),
            ApprovedFileChange(
                path="packs/opennosh-starter/pack.yaml",
                content=("id: opennosh-starter\nversion: 1.2.3\nlocale: zh-cmn-Hans-CN\n"),
            ),
        ),
    )
    decision_id = uuid4()
    accepted, receipt, _intent, _decision = _accepted_row(
        receipt_digest="a" * 64,
        commit_sha="b" * 40,
        event_type="publication",
        published_at=NOW,
        prior_receipt_digest=None,
        intent=None,
    )
    intent = SimpleNamespace(
        reviewed_decision_id=decision_id,
        source_draft_id=DRAFT_ID,
        source_draft_version=1,
        pack_id=accepted.pack_id,
        record_id=accepted.record_id,
        approved_payload_digest=changes.digest,
    )
    decision = SimpleNamespace(
        id=decision_id,
        outcome="approved",
        source_draft_id=DRAFT_ID,
        source_draft_version=1,
        pack_id=accepted.pack_id,
        record_id=accepted.record_id,
        forge_target=accepted.repository,
        approved_payload_digest=changes.digest,
        approved_changes_json=changes.as_json(),
    )
    receipt.envelope_json["receipt"].update(
        {
            "reviewed_decision_id": str(decision_id),
            "approved_payload_digest": changes.digest,
        }
    )
    return accepted, receipt, intent, decision, changes.digest


def test_activity_locale_is_bound_to_exact_governed_repository_payload() -> None:
    accepted, receipt, intent, decision, digest = _activity_proof_material()

    fact = _accepted_mission_fact(
        accepted,  # type: ignore[arg-type]
        receipt,  # type: ignore[arg-type]
        intent,  # type: ignore[arg-type]
        decision,  # type: ignore[arg-type]
        target_pack_id="opennosh-starter",
    )

    assert fact.activity_locale == "zh-cmn-Hans-CN"
    assert fact.activity_pack_version == "1.2.3"
    assert fact.activity_source_digest == digest

    accepted.repository = "github:elsewhere/opennosh"
    receipt.envelope_json["receipt"]["verified_steps"][0]["destination"] = accepted.repository
    fact = _accepted_mission_fact(
        accepted,  # type: ignore[arg-type]
        receipt,  # type: ignore[arg-type]
        intent,  # type: ignore[arg-type]
        decision,  # type: ignore[arg-type]
        target_pack_id="opennosh-starter",
    )
    assert fact.activity_locale is None
    assert fact.activity_source_digest is None


@pytest.mark.parametrize(
    "failure",
    ["receipt_body", "approved_changes", "manifest_missing", "manifest_yaml", "manifest_fields"],
)
def test_activity_locale_proof_fails_closed_on_unusable_manifest_material(
    failure: str,
) -> None:
    accepted, receipt, intent, decision, _digest = _activity_proof_material()

    if failure == "receipt_body":
        receipt.envelope_json["receipt"] = "invalid"
    elif failure == "approved_changes":
        decision.approved_changes_json = []
    else:
        files = [
            ApprovedFileChange(
                path="packs/opennosh-starter/foods/food-1.yaml",
                content="- slug: food-1\n",
            )
        ]
        if failure != "manifest_missing":
            manifest = (
                "id: opennosh-starter\nversion: 1.2.3\nlocale: [\n"
                if failure == "manifest_yaml"
                else "id: opennosh-starter\nversion: invalid\nlocale: en-US\n"
            )
            files.append(
                ApprovedFileChange(
                    path="packs/opennosh-starter/pack.yaml",
                    content=manifest,
                )
            )
        changes = ApprovedChangeSet.build(pack_id="opennosh-starter", files=tuple(files))
        decision.approved_changes_json = changes.as_json()
        decision.approved_payload_digest = changes.digest
        intent.approved_payload_digest = changes.digest
        receipt.envelope_json["receipt"]["approved_payload_digest"] = changes.digest

    locale, pack_version, source_digest = _activity_locale_proof(
        accepted,  # type: ignore[arg-type]
        receipt,  # type: ignore[arg-type]
        intent,  # type: ignore[arg-type]
        decision,  # type: ignore[arg-type]
    )

    assert locale is None
    assert pack_version is None
    assert source_digest is None


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
            event_type=row[1].event_type,
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
        activity_locale=active.activity_locale,
        activity_pack_version=active.activity_pack_version,
        activity_source_digest=active.activity_source_digest,
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
    accepted, receipt, _intent, _decision = _accepted_row(
        receipt_digest="a" * 64,
        commit_sha="b" * 40,
        event_type="publication",
        published_at=NOW,
        prior_receipt_digest=None,
        intent=intent,
    )
    receipt.envelope_json["receipt"]["merged_commit"] = "e" * 40
    binding = SimpleNamespace(
        mission_id=MISSION_ID,
        definition_id=DEFINITION_ID,
        source_draft_id=DRAFT_ID,
        source_draft_version=1,
    )
    repository = MissionRepository(  # type: ignore[arg-type]
        FakeSession(
            bindings=[binding],
            accepted_rows=[(accepted, receipt, intent, None)],
            stored_rows=[],
        )
    )
    checkpoint = SimpleNamespace(
        id=uuid4(),
        mission_id=MISSION_ID,
        definition_id=DEFINITION_ID,
    )

    assert not await repository.progress_is_current(checkpoint)  # type: ignore[arg-type]
