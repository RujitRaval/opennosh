from __future__ import annotations

import hashlib
import heapq
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from uuid import UUID

from opennosh_api.missions.contracts import (
    AcceptedMissionFact,
    MissionBindingFact,
    MissionProgress,
    MissionProgressRecord,
)


class MissionProjectionError(ValueError):
    """Fail-closed mission projection error with a stable public-safe code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _lineage_order(
    events_by_id: dict[UUID, AcceptedMissionFact],
    events_by_digest: dict[str, AcceptedMissionFact],
    bindings_by_source: dict[tuple[UUID, int], MissionBindingFact],
) -> list[AcceptedMissionFact]:
    """Return a stable parent-before-child order and reject scoped cycles."""

    children: dict[str, list[AcceptedMissionFact]] = {}
    pending_parent: dict[UUID, int] = {}
    ready: list[tuple[datetime, str, str, AcceptedMissionFact]] = []
    for event in events_by_id.values():
        parent_exists = (
            event.prior_receipt_digest is not None
            and event.prior_receipt_digest in events_by_digest
        )
        pending_parent[event.event_id] = int(parent_exists)
        if parent_exists:
            assert event.prior_receipt_digest is not None
            children.setdefault(event.prior_receipt_digest, []).append(event)
        else:
            heapq.heappush(
                ready,
                (event.published_at, event.receipt_digest, str(event.event_id), event),
            )

    ordered: list[AcceptedMissionFact] = []
    while ready:
        _published_at, _receipt, _event_id, event = heapq.heappop(ready)
        ordered.append(event)
        for child in children.get(event.receipt_digest, ()):
            pending_parent[child.event_id] -= 1
            if pending_parent[child.event_id] == 0:
                heapq.heappush(
                    ready,
                    (
                        child.published_at,
                        child.receipt_digest,
                        str(child.event_id),
                        child,
                    ),
                )

    if len(ordered) != len(events_by_id):
        ordered_ids = {event.event_id for event in ordered}
        cyclic = [event for event in events_by_id.values() if event.event_id not in ordered_ids]
        if any(
            (event.source_draft_id, event.source_draft_version) in bindings_by_source
            for event in cyclic
        ):
            raise MissionProjectionError("lineage_cycle")
        ordered.extend(
            sorted(
                cyclic,
                key=lambda event: (
                    event.published_at,
                    event.receipt_digest,
                    str(event.event_id),
                ),
            )
        )
    return ordered


def project_mission_progress(
    *,
    mission_id: UUID,
    definition_id: UUID,
    bindings: Iterable[MissionBindingFact],
    accepted_events: Iterable[AcceptedMissionFact],
) -> MissionProgress:
    """Project one definition from canonical accepted-event lineages.

    Directly bound publications establish membership. Corrections and revocations
    inherit that membership only through their exact receipt lineage. The output is
    independent of input order and identical retries are collapsed.
    """

    binding_by_source: dict[tuple[UUID, int], MissionBindingFact] = {}
    for binding in bindings:
        if binding.mission_id != mission_id or binding.definition_id != definition_id:
            raise MissionProjectionError("binding_scope_mismatch")
        key = (binding.source_draft_id, binding.source_draft_version)
        existing = binding_by_source.get(key)
        if existing is not None and existing != binding:
            raise MissionProjectionError("binding_conflict")
        binding_by_source[key] = binding

    unique_by_id: dict[UUID, AcceptedMissionFact] = {}
    unique_by_digest: dict[str, AcceptedMissionFact] = {}
    for event in accepted_events:
        by_id = unique_by_id.get(event.event_id)
        by_digest = unique_by_digest.get(event.receipt_digest)
        if (by_id is not None and by_id != event) or (by_digest is not None and by_digest != event):
            raise MissionProjectionError("accepted_event_conflict")
        unique_by_id[event.event_id] = event
        unique_by_digest[event.receipt_digest] = event

    ordered = _lineage_order(
        unique_by_id,
        unique_by_digest,
        binding_by_source,
    )
    mission_by_receipt: dict[str, tuple[UUID, UUID]] = {}
    matched: list[AcceptedMissionFact] = []
    current_records: dict[tuple[str, str, str], AcceptedMissionFact] = {}

    for event in ordered:
        source_binding = binding_by_source.get((event.source_draft_id, event.source_draft_version))
        inherited_scope = (
            mission_by_receipt.get(event.prior_receipt_digest)
            if event.prior_receipt_digest is not None
            else None
        )
        direct_scope = (
            (source_binding.mission_id, source_binding.definition_id)
            if source_binding is not None
            else None
        )
        if (
            direct_scope is not None
            and inherited_scope is not None
            and direct_scope != inherited_scope
        ):
            raise MissionProjectionError("lineage_binding_conflict")
        scope = direct_scope or inherited_scope
        if scope != (mission_id, definition_id):
            continue
        if event.event_type != "publication" and inherited_scope is None:
            raise MissionProjectionError("lineage_missing")

        if event.prior_receipt_digest is not None:
            prior = unique_by_digest.get(event.prior_receipt_digest)
            if prior is None:
                raise MissionProjectionError("lineage_missing")
            if (prior.repository, prior.pack_id, prior.record_id) != (
                event.repository,
                event.pack_id,
                event.record_id,
            ):
                raise MissionProjectionError("lineage_identity_mismatch")
            if prior.published_at > event.published_at:
                raise MissionProjectionError("lineage_time_invalid")

        mission_by_receipt[event.receipt_digest] = (mission_id, definition_id)
        matched.append(event)
        record_key = (event.repository, event.pack_id, event.record_id)
        if event.event_type == "revocation":
            current_records.pop(record_key, None)
        else:
            current_records[record_key] = event

    records = tuple(
        MissionProgressRecord(
            repository=event.repository,
            pack_id=event.pack_id,
            record_id=event.record_id,
            accepted_event_id=event.event_id,
            receipt_digest=event.receipt_digest,
            published_at=event.published_at,
        )
        for _key, event in sorted(current_records.items())
    )
    event_material = [
        {
            "event_id": str(event.event_id),
            "receipt_digest": event.receipt_digest,
            "prior_receipt_digest": event.prior_receipt_digest,
            "repository": event.repository,
            "commit_sha": event.commit_sha,
            "pack_id": event.pack_id,
            "record_id": event.record_id,
            "event_type": event.event_type,
            "published_at": event.published_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        }
        for event in matched
    ]
    digest = hashlib.sha256(
        json.dumps(event_material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return MissionProgress(
        mission_id=mission_id,
        definition_id=definition_id,
        accepted_count=len(records),
        matched_event_count=len(matched),
        event_set_digest=digest,
        records=records,
    )
