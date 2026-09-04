from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest
from opennosh_api.impact.contracts import (
    ImpactRegion,
    ImpactRegionLevel,
    ImpactState,
    PublicImpactSnapshot,
    impact_digest,
    signed_impact_snapshot,
    unavailable_impact_snapshot,
)
from opennosh_api.impact.models import ImpactSnapshot
from opennosh_api.impact.service import (
    ImpactContributionFact,
    ImpactDeclarationFact,
    ImpactOperationalProof,
    aggregate_impact_snapshot,
    latest_impact_snapshot,
    persist_impact_snapshot,
)

NOW = datetime(2026, 9, 4, 4, tzinfo=UTC)


def _uuid(value: int) -> UUID:
    return UUID(f"{value:08x}-0000-4000-8000-{value:012x}")


def _declaration(
    value: int,
    *,
    organization: str | None = None,
    state: str = "community_declared",
    region_code: str | None = "US",
    accessible: bool = False,
    fresh: bool = False,
) -> ImpactDeclarationFact:
    return ImpactDeclarationFact(
        declaration_id=_uuid(value),
        owner_actor_id=_uuid(value + 1000),
        organization_key=(f"organization-{value}" if organization is None else organization),
        state=state,  # type: ignore[arg-type]
        evidence_accessible=accessible,
        evidence_fresh=fresh,
        region_level=(ImpactRegionLevel.COUNTRY if region_code else None),
        region_code=region_code,
    )


def _operational(**overrides: int) -> ImpactOperationalProof:
    return ImpactOperationalProof(
        source_checkpoint_id="impact-checkpoint-1",
        pack_installs=overrides.get("pack_installs", 0),
        api_reads=overrides.get("api_reads", 0),
        artifact_downloads=overrides.get("artifact_downloads", 0),
    )


def test_k9_is_suppressed_and_k10_is_published_without_suppression_metadata() -> None:
    hidden = aggregate_impact_snapshot(
        declarations=tuple(_declaration(index) for index in range(1, 10)),
        contributions=(),
        operational=_operational(),
        observed_at=NOW,
    )
    released = aggregate_impact_snapshot(
        declarations=tuple(_declaration(index) for index in range(1, 11)),
        contributions=(),
        operational=_operational(),
        observed_at=NOW,
    )

    assert hidden.regions == ()
    assert len(released.regions) == 1
    assert released.regions[0].community_declarations == 10
    serialized = released.model_dump(mode="json", by_alias=True)
    assert "suppressed" not in str(serialized).lower()
    assert "cohort_count" not in serialized["regions"][0]


def test_verified_adopters_are_distinct_current_organizations_with_fresh_accessible_proof() -> None:
    declarations = (
        _declaration(1, organization="same org", state="verified", accessible=True, fresh=True),
        _declaration(2, organization="same org", state="verified", accessible=True, fresh=True),
        _declaration(3, state="verified", accessible=False, fresh=True),
        _declaration(4, state="verified", accessible=True, fresh=False),
        _declaration(5, state="withdrawn", accessible=True, fresh=True),
    )

    result = aggregate_impact_snapshot(
        declarations=declarations,
        contributions=(),
        operational=_operational(),
        observed_at=NOW,
    )

    assert result.global_.verified_adopters == 1
    assert result.global_.community_declarations == 0
    assert result.regions == ()


def test_pending_records_and_repeated_actor_declarations_cannot_manufacture_a_cohort() -> None:
    actor = _uuid(9000)
    repeated = tuple(
        ImpactDeclarationFact(
            declaration_id=_uuid(index),
            owner_actor_id=actor,
            organization_key=f"organization-{index}",
            state="community_declared" if index < 11 else "verification_pending",
            region_level=ImpactRegionLevel.COUNTRY,
            region_code="GB",
        )
        for index in range(1, 20)
    )

    result = aggregate_impact_snapshot(
        declarations=repeated,
        contributions=(),
        operational=_operational(),
        observed_at=NOW,
    )

    assert result.global_.community_declarations == 10
    assert result.regions == ()


def test_contributions_deduplicate_events_and_operational_metrics_remain_global_only() -> None:
    contribution = ImpactContributionFact(
        accepted_event_id=_uuid(50),
        actor_id=_uuid(150),
        region_level=ImpactRegionLevel.COUNTRY,
        region_code="CA",
    )
    result = aggregate_impact_snapshot(
        declarations=(),
        contributions=(contribution, contribution),
        operational=_operational(pack_installs=7, api_reads=8, artifact_downloads=9),
        observed_at=NOW,
    )

    assert result.global_.accepted_contributions == 1
    assert result.global_.pack_installs == 7
    assert result.global_.api_reads == 8
    assert result.global_.artifact_downloads == 9
    assert result.regions == ()


def test_snapshot_digest_is_canonical_and_tampering_is_rejected() -> None:
    snapshot = aggregate_impact_snapshot(
        declarations=(), contributions=(), operational=_operational(), observed_at=NOW
    )
    assert snapshot.state is ImpactState.ZERO
    assert snapshot.digest == impact_digest(snapshot)
    payload = snapshot.model_dump(mode="json", by_alias=True)
    payload["state"] = "live"
    payload["global"]["api_reads"] = 1
    with pytest.raises(ValueError, match="digest does not match"):
        PublicImpactSnapshot.model_validate(payload)


def test_contract_rejects_non_utc_and_invalid_state_shapes() -> None:
    with pytest.raises(ValueError, match="include a timezone"):
        signed_impact_snapshot(
            state="zero",
            reason=None,
            observed_at=datetime(2026, 9, 4, 4),
            source_checkpoint_id="checkpoint",
            global_={},
            regions=(),
        )
    with pytest.raises(ValueError, match="must use UTC"):
        signed_impact_snapshot(
            state="zero",
            reason=None,
            observed_at=NOW.astimezone(timezone(timedelta(hours=1))),
            source_checkpoint_id="checkpoint",
            global_={},
            regions=(),
        )
    with pytest.raises(ValueError, match="require exactly one safe reason"):
        signed_impact_snapshot(
            state="unavailable",
            reason=None,
            observed_at=NOW,
            source_checkpoint_id=None,
            global_={},
            regions=(),
        )
    with pytest.raises(ValueError, match="cannot contain proof or activity"):
        signed_impact_snapshot(
            state="unavailable",
            reason="disabled",
            observed_at=NOW,
            source_checkpoint_id=None,
            global_={"api_reads": 1},
            regions=(),
        )
    with pytest.raises(ValueError, match="require a source checkpoint"):
        signed_impact_snapshot(
            state="zero",
            reason=None,
            observed_at=NOW,
            source_checkpoint_id=None,
            global_={},
            regions=(),
        )
    with pytest.raises(ValueError, match="Zero impact snapshots"):
        signed_impact_snapshot(
            state="zero",
            reason=None,
            observed_at=NOW,
            source_checkpoint_id="checkpoint",
            global_={"api_reads": 1},
            regions=(),
        )
    with pytest.raises(ValueError, match="Live impact snapshots"):
        signed_impact_snapshot(
            state="live",
            reason=None,
            observed_at=NOW,
            source_checkpoint_id="checkpoint",
            global_={},
            regions=(),
        )


def test_disabled_and_proof_unavailable_snapshots_are_deterministic_and_empty() -> None:
    disabled = unavailable_impact_snapshot("disabled")
    unavailable = unavailable_impact_snapshot("proof_unavailable")

    assert disabled.state is ImpactState.UNAVAILABLE
    assert disabled.observed_at == datetime(1970, 1, 1, tzinfo=UTC)
    assert disabled.global_.has_activity() is False
    assert disabled.regions == ()
    assert disabled.digest != unavailable.digest


def test_incomplete_region_and_negative_operational_proof_are_rejected() -> None:
    with pytest.raises(ValueError, match="region proof must be complete"):
        _declaration(1, region_code=None).__class__(
            declaration_id=_uuid(1),
            owner_actor_id=_uuid(2),
            organization_key="org",
            state="community_declared",
            region_level=ImpactRegionLevel.COUNTRY,
            region_code=None,
        )
    with pytest.raises(ValueError, match="state is unsupported"):
        _declaration(2, state="invented")
    with pytest.raises(ValueError, match="uppercase ISO"):
        _declaration(3, region_code="us")
    with pytest.raises(ValueError, match="three-digit UN M49"):
        ImpactRegion(
            level=ImpactRegionLevel.MACROREGION,
            region_code="US",
            community_declarations=10,
        )
    with pytest.raises(ValueError, match="require activity"):
        ImpactRegion(level=ImpactRegionLevel.COUNTRY, region_code="US")
    with pytest.raises(ValueError, match="organization key is invalid"):
        _declaration(4, organization="")
    with pytest.raises(ValueError, match="checkpoint ID is required"):
        ImpactOperationalProof(
            source_checkpoint_id="",
            pack_installs=0,
            api_reads=0,
            artifact_downloads=0,
        )
    with pytest.raises(ValueError, match="cannot be negative"):
        ImpactOperationalProof(
            source_checkpoint_id="checkpoint",
            pack_installs=-1,
            api_reads=0,
            artifact_downloads=0,
        )


class FakeDatabase:
    def __init__(self, result: object = None) -> None:
        self.result = result
        self.added: object | None = None
        self.flushed = False

    async def scalar(self, _statement: object) -> object:
        return self.result

    def add(self, value: object) -> None:
        self.added = value

    async def flush(self) -> None:
        self.flushed = True


@pytest.mark.asyncio
async def test_snapshot_persistence_accepts_releases_and_reuses_existing_digest() -> None:
    snapshot = aggregate_impact_snapshot(
        declarations=(), contributions=(), operational=_operational(), observed_at=NOW
    )
    database = FakeDatabase()
    created = await persist_impact_snapshot(database, snapshot)  # type: ignore[arg-type]

    assert isinstance(created, ImpactSnapshot)
    assert database.added is created
    assert database.flushed
    assert created.snapshot_json == snapshot.model_dump(mode="json", by_alias=True)

    existing = ImpactSnapshot()
    replay_database = FakeDatabase(existing)
    replay = await persist_impact_snapshot(replay_database, snapshot)  # type: ignore[arg-type]
    assert replay is existing
    assert replay_database.added is None

    with pytest.raises(ValueError, match="Only released"):
        await persist_impact_snapshot(  # type: ignore[arg-type]
            FakeDatabase(), unavailable_impact_snapshot("disabled")
        )


@pytest.mark.asyncio
async def test_latest_snapshot_rejects_metadata_drift_and_handles_empty_store() -> None:
    assert await latest_impact_snapshot(FakeDatabase()) is None  # type: ignore[arg-type]
    snapshot = aggregate_impact_snapshot(
        declarations=(), contributions=(), operational=_operational(), observed_at=NOW
    )
    record = ImpactSnapshot(
        schema_version=snapshot.schema_version,
        metric_definition_version=snapshot.metric_definition_version,
        state=snapshot.state.value,
        observed_at=snapshot.observed_at,
        source_checkpoint_id=snapshot.source_checkpoint_id,
        snapshot_json=snapshot.model_dump(mode="json", by_alias=True),
        digest=snapshot.digest,
    )
    assert await latest_impact_snapshot(FakeDatabase(record)) == snapshot  # type: ignore[arg-type]
    record.digest = "f" * 64
    with pytest.raises(ValueError, match="metadata_mismatch"):
        await latest_impact_snapshot(FakeDatabase(record))  # type: ignore[arg-type]


def test_dictionary_digest_normalizes_internal_global_alias() -> None:
    payload = {
        "schema_version": "1.0",
        "state": "zero",
        "reason": None,
        "metric_definition_version": "1.0",
        "observed_at": NOW,
        "source_checkpoint_id": "checkpoint",
        "minimum_cohort": 10,
        "global_": {},
        "regions": (),
    }
    assert impact_digest(payload) == impact_digest({**payload, "global": payload["global_"]})
