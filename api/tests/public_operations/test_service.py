from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from opennosh_api.public_operations.contracts import PublicIncidentState
from opennosh_api.public_operations.manifest import load_public_status_manifest
from opennosh_api.public_operations.models import PublicComponentObservation
from opennosh_api.public_operations.service import (
    PublicOperationsError,
    project_public_status,
    require_incident_transition,
)

NOW = datetime(2026, 9, 4, 6, tzinfo=UTC)
MANIFEST = load_public_status_manifest()


def _observation(
    *,
    component_id: str = "api",
    state: str = "operational",
    successful: bool = True,
    observed_at: datetime = NOW,
    digest: str = "a" * 64,
) -> PublicComponentObservation:
    return PublicComponentObservation(
        id=UUID("70000000-0000-4000-8000-000000000001"),
        component_id=component_id,
        state=state,
        successful=successful,
        observed_at=observed_at,
        evidence_digest=digest,
        affected_versions=["0.92.0.0"],
        created_at=observed_at,
    )


def test_missing_component_evidence_is_unknown_for_the_complete_fixed_inventory() -> None:
    status = project_public_status(manifest=MANIFEST, observations=(), now=NOW)
    assert len(status.components) == 8
    assert tuple(component.component_id for component in status.components) == tuple(
        component.component_id for component in MANIFEST.components
    )
    assert {component.state.value for component in status.components} == {"unknown"}
    assert {component.reason.value for component in status.components if component.reason} == {
        "missing_evidence"
    }


def test_operational_requires_a_fresh_successful_monitor_observation() -> None:
    fresh = project_public_status(
        manifest=MANIFEST,
        observations=(_observation(),),
        now=NOW + timedelta(seconds=299),
    )
    api = fresh.components[0]
    assert api.state == "operational"
    assert api.reason is None
    assert api.evidence_digest == "a" * 64

    stale = project_public_status(
        manifest=MANIFEST,
        observations=(_observation(),),
        now=NOW + timedelta(seconds=301),
    )
    assert stale.components[0].state == "unknown"
    assert stale.components[0].reason == "stale_evidence"

    unsuccessful = project_public_status(
        manifest=MANIFEST,
        observations=(_observation(successful=False),),
        now=NOW,
    )
    assert unsuccessful.components[0].state == "unknown"
    assert unsuccessful.components[0].reason == "malformed_evidence"


def test_fresh_nonoperational_evidence_preserves_the_explicit_state() -> None:
    for state in ("degraded", "outage", "maintenance"):
        status = project_public_status(
            manifest=MANIFEST,
            observations=(_observation(state=state, successful=False),),
            now=NOW,
        )
        assert status.components[0].state == state
        assert status.components[0].reason is None


@pytest.mark.parametrize(
    "observation",
    [
        _observation(state="unknown"),
        _observation(digest="not-a-digest"),
        _observation(observed_at=NOW + timedelta(seconds=1)),
    ],
)
def test_malformed_or_future_monitor_evidence_never_projects_operational(
    observation: PublicComponentObservation,
) -> None:
    status = project_public_status(
        manifest=MANIFEST,
        observations=(observation,),
        now=NOW,
    )
    assert status.components[0].state == "unknown"
    assert status.components[0].reason == "malformed_evidence"
    assert status.components[0].evidence_digest is None


def test_incident_transition_table_is_explicit_and_resolution_is_terminal() -> None:
    for previous, next_state in (
        (PublicIncidentState.INVESTIGATING, PublicIncidentState.IDENTIFIED),
        (PublicIncidentState.INVESTIGATING, PublicIncidentState.MONITORING),
        (PublicIncidentState.INVESTIGATING, PublicIncidentState.RESOLVED),
        (PublicIncidentState.IDENTIFIED, PublicIncidentState.MONITORING),
        (PublicIncidentState.MONITORING, PublicIncidentState.IDENTIFIED),
        (PublicIncidentState.MONITORING, PublicIncidentState.RESOLVED),
    ):
        require_incident_transition(previous, next_state)
    for next_state in PublicIncidentState:
        with pytest.raises(PublicOperationsError, match="transition_invalid"):
            require_incident_transition(PublicIncidentState.RESOLVED, next_state)
    with pytest.raises(PublicOperationsError, match="transition_invalid"):
        require_incident_transition(
            PublicIncidentState.IDENTIFIED,
            PublicIncidentState.INVESTIGATING,
        )
