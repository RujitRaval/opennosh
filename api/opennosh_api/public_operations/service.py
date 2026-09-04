from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.public_operations.contracts import (
    ComponentObservationEvidence,
    IncidentRecoveryEvidence,
    PublicComponentState,
    PublicComponentStatus,
    PublicIncident,
    PublicIncidentEventInput,
    PublicIncidentListResponse,
    PublicIncidentState,
    PublicStatusResponse,
    PublicStatusUnknownReason,
    normalize_public_text,
)
from opennosh_api.public_operations.manifest import PublicStatusManifest
from opennosh_api.public_operations.models import (
    PublicComponentObservation,
    PublicIncidentEvent,
)
from opennosh_api.public_operations.models import (
    PublicIncident as PublicIncidentRecord,
)


class PublicOperationsError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


_INCIDENT_TRANSITIONS: dict[PublicIncidentState, frozenset[PublicIncidentState]] = {
    PublicIncidentState.INVESTIGATING: frozenset(
        {
            PublicIncidentState.IDENTIFIED,
            PublicIncidentState.MONITORING,
            PublicIncidentState.RESOLVED,
        }
    ),
    PublicIncidentState.IDENTIFIED: frozenset(
        {PublicIncidentState.MONITORING, PublicIncidentState.RESOLVED}
    ),
    PublicIncidentState.MONITORING: frozenset(
        {PublicIncidentState.IDENTIFIED, PublicIncidentState.RESOLVED}
    ),
    PublicIncidentState.RESOLVED: frozenset(),
}


def require_incident_transition(
    previous: PublicIncidentState,
    next_state: PublicIncidentState,
) -> None:
    if next_state not in _INCIDENT_TRANSITIONS[previous]:
        raise PublicOperationsError("public_incident_transition_invalid")


def _require_utc(value: datetime, *, code: str) -> datetime:
    if (
        value.tzinfo is None
        or value.utcoffset() is None
        or value.utcoffset() != UTC.utcoffset(value)
    ):
        raise PublicOperationsError(code)
    return value


def _known_components(manifest: PublicStatusManifest) -> frozenset[str]:
    return frozenset(component.component_id for component in manifest.components)


def _validate_incident_components(
    event: PublicIncidentEventInput,
    *,
    manifest: PublicStatusManifest,
) -> None:
    if not set(event.affected_component_ids).issubset(_known_components(manifest)):
        raise PublicOperationsError("public_incident_component_unknown")


def _incident_event_digest(event: PublicIncidentEventInput) -> str:
    payload = json.dumps(
        event.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


async def record_component_observation(
    database: AsyncSession,
    *,
    manifest: PublicStatusManifest,
    evidence: ComponentObservationEvidence,
    record_id_generator: Callable[[], UUID] = uuid4,
) -> PublicComponentObservation:
    if evidence.component_id not in _known_components(manifest):
        raise PublicOperationsError("public_status_component_unknown")
    existing = await database.scalar(
        select(PublicComponentObservation).where(
            PublicComponentObservation.component_id == evidence.component_id,
            PublicComponentObservation.observed_at == evidence.observed_at,
            PublicComponentObservation.evidence_digest == evidence.evidence_digest,
        )
    )
    if existing is not None:
        if (
            existing.state != evidence.state.value
            or existing.successful is not evidence.successful
            or tuple(existing.affected_versions) != evidence.affected_versions
        ):
            raise PublicOperationsError("public_status_observation_conflict")
        return existing
    record = PublicComponentObservation(
        id=record_id_generator(),
        component_id=evidence.component_id,
        state=evidence.state.value,
        successful=evidence.successful,
        observed_at=evidence.observed_at,
        evidence_digest=evidence.evidence_digest,
        affected_versions=list(evidence.affected_versions),
        created_at=evidence.observed_at,
    )
    database.add(record)
    await database.flush()
    return record


async def latest_component_observations(
    database: AsyncSession,
    *,
    manifest: PublicStatusManifest,
) -> tuple[PublicComponentObservation, ...]:
    component_ids = tuple(component.component_id for component in manifest.components)
    rank = (
        func.row_number()
        .over(
            partition_by=PublicComponentObservation.component_id,
            order_by=(
                PublicComponentObservation.observed_at.desc(),
                PublicComponentObservation.id.desc(),
            ),
        )
        .label("observation_rank")
    )
    ranked = (
        select(PublicComponentObservation.id.label("observation_id"), rank)
        .where(PublicComponentObservation.component_id.in_(component_ids))
        .subquery()
    )
    records = await database.scalars(
        select(PublicComponentObservation)
        .join(ranked, ranked.c.observation_id == PublicComponentObservation.id)
        .where(ranked.c.observation_rank == 1)
        .order_by(PublicComponentObservation.component_id)
    )
    return tuple(records)


def project_public_status(
    *,
    manifest: PublicStatusManifest,
    observations: tuple[PublicComponentObservation, ...],
    now: datetime,
) -> PublicStatusResponse:
    _require_utc(now, code="public_status_projection_time_invalid")
    by_component = {observation.component_id: observation for observation in observations}
    components: list[PublicComponentStatus] = []
    for definition in manifest.components:
        observation = by_component.get(definition.component_id)
        if observation is None:
            components.append(
                PublicComponentStatus(
                    component_id=definition.component_id,
                    display_name=definition.display_name,
                    state=PublicComponentState.UNKNOWN,
                    reason=PublicStatusUnknownReason.MISSING_EVIDENCE,
                    freshness_window_seconds=definition.freshness_window_seconds,
                )
            )
            continue
        try:
            evidence = ComponentObservationEvidence(
                component_id=observation.component_id,
                state=PublicComponentState(observation.state),
                successful=observation.successful,
                observed_at=observation.observed_at,
                evidence_digest=observation.evidence_digest,
                affected_versions=tuple(observation.affected_versions),
            )
        except (TypeError, ValueError, ValidationError):
            components.append(
                PublicComponentStatus(
                    component_id=definition.component_id,
                    display_name=definition.display_name,
                    state=PublicComponentState.UNKNOWN,
                    reason=PublicStatusUnknownReason.MALFORMED_EVIDENCE,
                    freshness_window_seconds=definition.freshness_window_seconds,
                )
            )
            continue
        age_seconds = (now - evidence.observed_at).total_seconds()
        if age_seconds < 0 or (
            evidence.state is PublicComponentState.OPERATIONAL and not evidence.successful
        ):
            components.append(
                PublicComponentStatus(
                    component_id=definition.component_id,
                    display_name=definition.display_name,
                    state=PublicComponentState.UNKNOWN,
                    reason=PublicStatusUnknownReason.MALFORMED_EVIDENCE,
                    freshness_window_seconds=definition.freshness_window_seconds,
                )
            )
            continue
        if age_seconds > definition.freshness_window_seconds:
            components.append(
                PublicComponentStatus(
                    component_id=definition.component_id,
                    display_name=definition.display_name,
                    state=PublicComponentState.UNKNOWN,
                    reason=PublicStatusUnknownReason.STALE_EVIDENCE,
                    observed_at=evidence.observed_at,
                    freshness_window_seconds=definition.freshness_window_seconds,
                    evidence_digest=evidence.evidence_digest,
                    affected_versions=evidence.affected_versions,
                )
            )
            continue
        components.append(
            PublicComponentStatus(
                component_id=definition.component_id,
                display_name=definition.display_name,
                state=evidence.state,
                observed_at=evidence.observed_at,
                freshness_window_seconds=definition.freshness_window_seconds,
                evidence_digest=evidence.evidence_digest,
                affected_versions=evidence.affected_versions,
            )
        )
    return PublicStatusResponse(
        configuration_digest=manifest.digest,
        components=tuple(components),
    )


async def current_public_status(
    database: AsyncSession,
    *,
    manifest: PublicStatusManifest,
    now: datetime,
) -> PublicStatusResponse:
    observations = await latest_component_observations(database, manifest=manifest)
    return project_public_status(manifest=manifest, observations=observations, now=now)


def _event_record(
    *,
    incident_id: UUID,
    sequence: int,
    event: PublicIncidentEventInput,
    event_id_generator: Callable[[], UUID],
) -> PublicIncidentEvent:
    return PublicIncidentEvent(
        id=event_id_generator(),
        incident_id=incident_id,
        sequence=sequence,
        state=event.state.value,
        public_summary=event.public_summary,
        affected_component_ids=list(event.affected_component_ids),
        affected_versions=list(event.affected_versions),
        guidance=event.guidance,
        recovery_evidence=(
            {}
            if event.recovery_evidence is None
            else event.recovery_evidence.model_dump(mode="json")
        ),
        event_digest=_incident_event_digest(event),
        occurred_at=event.occurred_at,
        created_at=event.occurred_at,
    )


async def create_public_incident(
    database: AsyncSession,
    *,
    manifest: PublicStatusManifest,
    incident_id: UUID,
    title: str,
    event: PublicIncidentEventInput,
    event_id_generator: Callable[[], UUID] = uuid4,
) -> tuple[PublicIncidentRecord, PublicIncidentEvent]:
    if event.state is not PublicIncidentState.INVESTIGATING:
        raise PublicOperationsError("public_incident_initial_state_invalid")
    _validate_incident_components(event, manifest=manifest)
    normalized_title = normalize_public_text(title, maximum=160)
    existing = await database.get(PublicIncidentRecord, incident_id)
    if existing is not None:
        existing_event = await database.scalar(
            select(PublicIncidentEvent).where(
                PublicIncidentEvent.incident_id == incident_id,
                PublicIncidentEvent.event_digest == _incident_event_digest(event),
            )
        )
        if existing.title == normalized_title and existing_event is not None:
            return existing, existing_event
        raise PublicOperationsError("public_incident_conflict")
    incident = PublicIncidentRecord(
        id=incident_id,
        title=normalized_title,
        opened_at=event.occurred_at,
        created_at=event.occurred_at,
    )
    record = _event_record(
        incident_id=incident_id,
        sequence=1,
        event=event,
        event_id_generator=event_id_generator,
    )
    database.add(incident)
    database.add(record)
    await database.flush()
    return incident, record


async def append_public_incident_event(
    database: AsyncSession,
    *,
    manifest: PublicStatusManifest,
    incident_id: UUID,
    event: PublicIncidentEventInput,
    event_id_generator: Callable[[], UUID] = uuid4,
) -> PublicIncidentEvent:
    _validate_incident_components(event, manifest=manifest)
    incident = await database.scalar(
        select(PublicIncidentRecord).where(PublicIncidentRecord.id == incident_id).with_for_update()
    )
    if incident is None:
        raise PublicOperationsError("public_incident_not_found")
    event_digest = _incident_event_digest(event)
    replay = await database.scalar(
        select(PublicIncidentEvent).where(
            PublicIncidentEvent.incident_id == incident_id,
            PublicIncidentEvent.event_digest == event_digest,
        )
    )
    if replay is not None:
        return replay
    latest = await database.scalar(
        select(PublicIncidentEvent)
        .where(PublicIncidentEvent.incident_id == incident_id)
        .order_by(PublicIncidentEvent.sequence.desc())
        .limit(1)
    )
    if latest is None:
        raise PublicOperationsError("public_incident_history_missing")
    previous = PublicIncidentState(latest.state)
    require_incident_transition(previous, event.state)
    if event.occurred_at < latest.occurred_at or event.occurred_at < incident.opened_at:
        raise PublicOperationsError("public_incident_time_invalid")
    record = _event_record(
        incident_id=incident_id,
        sequence=latest.sequence + 1,
        event=event,
        event_id_generator=event_id_generator,
    )
    database.add(record)
    await database.flush()
    return record


def _public_incident(
    incident: PublicIncidentRecord,
    event: PublicIncidentEvent,
) -> PublicIncident:
    input_event = PublicIncidentEventInput(
        state=PublicIncidentState(event.state),
        public_summary=event.public_summary,
        affected_component_ids=tuple(event.affected_component_ids),
        affected_versions=tuple(event.affected_versions),
        guidance=event.guidance,
        occurred_at=event.occurred_at,
        recovery_evidence=(
            None
            if not event.recovery_evidence
            else IncidentRecoveryEvidence.model_validate(event.recovery_evidence)
        ),
    )
    resolved = input_event.state is PublicIncidentState.RESOLVED
    return PublicIncident(
        incident_id=incident.id,
        title=incident.title,
        public_summary=input_event.public_summary,
        affected_component_ids=input_event.affected_component_ids,
        affected_versions=input_event.affected_versions,
        guidance=input_event.guidance,
        state=input_event.state,
        opened_at=incident.opened_at,
        updated_at=input_event.occurred_at,
        resolved_at=input_event.occurred_at if resolved else None,
        recovery_evidence=input_event.recovery_evidence,
    )


async def list_public_incidents(
    database: AsyncSession,
    *,
    limit: int = 100,
) -> PublicIncidentListResponse:
    if not 1 <= limit <= 100:
        raise ValueError("Public incident limit must be between one and 100")
    latest_sequences = (
        select(
            PublicIncidentEvent.incident_id.label("incident_id"),
            func.max(PublicIncidentEvent.sequence).label("latest_sequence"),
        )
        .group_by(PublicIncidentEvent.incident_id)
        .subquery()
    )
    rows = await database.execute(
        select(PublicIncidentRecord, PublicIncidentEvent)
        .join(PublicIncidentEvent, PublicIncidentEvent.incident_id == PublicIncidentRecord.id)
        .join(
            latest_sequences,
            (latest_sequences.c.incident_id == PublicIncidentEvent.incident_id)
            & (latest_sequences.c.latest_sequence == PublicIncidentEvent.sequence),
        )
        .order_by(PublicIncidentRecord.opened_at.desc(), PublicIncidentRecord.id)
        .limit(limit)
    )
    return PublicIncidentListResponse(
        incidents=tuple(_public_incident(incident, event) for incident, event in rows.all())
    )


__all__ = [
    "PublicOperationsError",
    "append_public_incident_event",
    "create_public_incident",
    "current_public_status",
    "latest_component_observations",
    "list_public_incidents",
    "project_public_status",
    "record_component_observation",
    "require_incident_transition",
]
