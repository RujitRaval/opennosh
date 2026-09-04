from __future__ import annotations

from datetime import UTC, datetime

import pytest
from opennosh_api.public_operations.contracts import (
    ComponentObservationEvidence,
    PublicComponentStatus,
    PublicIncidentEventInput,
)
from pydantic import ValidationError

NOW = datetime(2026, 9, 4, 6, tzinfo=UTC)


def test_component_observation_is_digest_bound_version_aware_and_utc() -> None:
    evidence = ComponentObservationEvidence(
        component_id="api",
        state="operational",
        successful=True,
        observed_at=NOW,
        evidence_digest="a" * 64,
        affected_versions=("0.92.0.0",),
    )
    assert evidence.component_id == "api"
    assert evidence.affected_versions == ("0.92.0.0",)


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"state": "unknown"}, "projected"),
        ({"observed_at": datetime(2026, 9, 4, 6)}, "timezone"),
        ({"evidence_digest": "A" * 64}, "String should match pattern"),
        ({"affected_versions": ("latest",)}, "String should match pattern"),
        (
            {"affected_versions": ("0.92.0.0", "0.91.0.0")},
            "sorted and unique",
        ),
    ],
)
def test_component_observation_rejects_unproved_or_ambiguous_values(
    change: dict[str, object],
    match: str,
) -> None:
    values: dict[str, object] = {
        "component_id": "api",
        "state": "operational",
        "successful": True,
        "observed_at": NOW,
        "evidence_digest": "a" * 64,
        "affected_versions": ("0.92.0.0",),
    }
    values.update(change)
    with pytest.raises(ValidationError, match=match):
        ComponentObservationEvidence.model_validate(values)


def test_unknown_status_requires_one_safe_reason_and_no_fabricated_proof() -> None:
    missing = PublicComponentStatus(
        component_id="api",
        display_name="Public API",
        state="unknown",
        reason="missing_evidence",
        freshness_window_seconds=300,
    )
    assert missing.observed_at is None
    with pytest.raises(ValidationError, match="exactly one safe reason"):
        PublicComponentStatus(
            component_id="api",
            display_name="Public API",
            state="operational",
            reason="missing_evidence",
            observed_at=NOW,
            evidence_digest="a" * 64,
            freshness_window_seconds=300,
        )
    with pytest.raises(ValidationError, match="cannot carry proof"):
        PublicComponentStatus(
            component_id="api",
            display_name="Public API",
            state="unknown",
            reason="missing_evidence",
            observed_at=NOW,
            evidence_digest="a" * 64,
            freshness_window_seconds=300,
        )


def _incident_event(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "state": "investigating",
        "public_summary": "Search requests are failing for some visitors.",
        "affected_component_ids": ("api", "search"),
        "affected_versions": ("0.92.0.0",),
        "guidance": "Retry later; existing signed downloads remain available.",
        "occurred_at": NOW,
    }
    values.update(changes)
    return values


def test_incident_event_is_fixed_component_version_and_public_text_only() -> None:
    event = PublicIncidentEventInput.model_validate(_incident_event())
    assert event.affected_component_ids == ("api", "search")
    for change in (
        {"affected_component_ids": ("search", "api")},
        {"affected_versions": ("latest",)},
        {"public_summary": "internal\x00host"},
    ):
        with pytest.raises(ValidationError):
            PublicIncidentEventInput.model_validate(_incident_event(**change))


@pytest.mark.parametrize(
    "unsafe_text",
    (
        "Database at 10.0.0.7 is unavailable.",
        "Database at fd00::7 is unavailable.",
        "Database db-1.service.internal is unavailable.",
        "Upstream resource srv-c0123456789 is unavailable.",
        "Authorization: Bearer-private-value",
        "Request failed.\nTraceback: private stack detail",
    ),
)
def test_incident_text_rejects_private_infrastructure_credentials_and_logs(
    unsafe_text: str,
) -> None:
    with pytest.raises(ValidationError, match="private|multiline"):
        PublicIncidentEventInput.model_validate(_incident_event(public_summary=unsafe_text))


def test_resolved_incident_requires_verified_recovery_that_does_not_postdate_event() -> None:
    recovery = {
        "status": "verified",
        "observed_at": NOW,
        "content_sha256": "b" * 64,
    }
    resolved = PublicIncidentEventInput.model_validate(
        _incident_event(state="resolved", recovery_evidence=recovery)
    )
    assert resolved.recovery_evidence is not None
    with pytest.raises(ValidationError, match="require verified recovery"):
        PublicIncidentEventInput.model_validate(_incident_event(state="resolved"))
    with pytest.raises(ValidationError, match="postdate"):
        PublicIncidentEventInput.model_validate(
            _incident_event(
                state="resolved",
                recovery_evidence={
                    **recovery,
                    "observed_at": "2026-09-04T06:00:01Z",
                },
            )
        )
