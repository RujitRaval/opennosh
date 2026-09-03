from __future__ import annotations

from dataclasses import dataclass

import pytest
from opennosh_api.missions.activity_service import (
    MAX_ACTIVITY_LINEAGE_EVENTS,
    MAX_ACTIVITY_MISSIONS,
    MAX_ACTIVITY_RECORDS,
    MissionActivityRegionLevel,
    MissionActivityState,
    PublicMissionActivityCountry,
    PublicMissionActivityMacroregion,
    PublicMissionActivityMap,
    public_mission_activity_map,
)
from opennosh_api.missions.repository import MissionActivityLocaleCount


@dataclass
class FakeActivityStore:
    counts: tuple[MissionActivityLocaleCount, ...] = ()
    requested_max_missions: int | None = None
    requested_max_records: int | None = None
    requested_max_lineage_events: int | None = None

    async def public_mission_activity_locales(
        self, max_missions: int, max_records: int, max_lineage_events: int
    ) -> tuple[MissionActivityLocaleCount, ...]:
        self.requested_max_missions = max_missions
        self.requested_max_records = max_records
        self.requested_max_lineage_events = max_lineage_events
        return self.counts


@pytest.mark.asyncio
async def test_disabled_activity_map_never_reads_storage() -> None:
    store = FakeActivityStore()

    result = await public_mission_activity_map(store, enabled=False)

    assert result.state is MissionActivityState.UNAVAILABLE
    assert result.reason == "disabled"
    assert result.minimum_cohort == 10
    assert result.regions == ()
    assert store.requested_max_missions is None


@pytest.mark.asyncio
async def test_activity_map_merges_pack_locales_and_hides_small_cohorts() -> None:
    store = FakeActivityStore(
        counts=(
            MissionActivityLocaleCount(locale="en-US", accepted_count=6),
            MissionActivityLocaleCount(locale="es-US", accepted_count=5),
            MissionActivityLocaleCount(locale="fr-CA", accepted_count=9),
            MissionActivityLocaleCount(locale="es-419", accepted_count=12),
            MissionActivityLocaleCount(locale="zh-Hant-TW", accepted_count=10),
            MissionActivityLocaleCount(locale="zh-cmn-Hans-CN", accepted_count=10),
            MissionActivityLocaleCount(locale="zh-yue-HK", accepted_count=10),
            MissionActivityLocaleCount(locale="en", accepted_count=200),
        )
    )

    result = await public_mission_activity_map(store, enabled=True)

    assert result.state is MissionActivityState.LIVE
    assert result.reason is None
    assert store.requested_max_missions == MAX_ACTIVITY_MISSIONS
    assert store.requested_max_records == MAX_ACTIVITY_RECORDS
    assert store.requested_max_lineage_events == MAX_ACTIVITY_LINEAGE_EVENTS
    assert result.regions == (
        PublicMissionActivityMacroregion(
            region_code="419",
            level=MissionActivityRegionLevel.MACROREGION,
            accepted_count=12,
        ),
        PublicMissionActivityCountry(
            region_code="US",
            level=MissionActivityRegionLevel.COUNTRY,
            accepted_count=11,
        ),
        PublicMissionActivityCountry(
            region_code="CN",
            level=MissionActivityRegionLevel.COUNTRY,
            accepted_count=10,
        ),
        PublicMissionActivityCountry(
            region_code="HK",
            level=MissionActivityRegionLevel.COUNTRY,
            accepted_count=10,
        ),
        PublicMissionActivityCountry(
            region_code="TW",
            level=MissionActivityRegionLevel.COUNTRY,
            accepted_count=10,
        ),
    )
    assert all(region.region_code != "CA" for region in result.regions)


@pytest.mark.asyncio
async def test_only_hidden_or_unlocated_activity_is_honest_zero() -> None:
    store = FakeActivityStore(
        counts=(
            MissionActivityLocaleCount(locale="fr-CA", accepted_count=9),
            MissionActivityLocaleCount(locale="en", accepted_count=100),
        )
    )

    result = await public_mission_activity_map(store, enabled=True)

    assert result.state is MissionActivityState.ZERO
    assert result.reason is None
    assert result.regions == ()
    assert result.model_dump(mode="json") == {
        "schema_version": "1.0",
        "state": "zero",
        "reason": None,
        "minimum_cohort": 10,
        "regions": [],
    }


def test_activity_contract_rejects_ambiguous_or_small_public_cohorts() -> None:
    with pytest.raises(ValueError, match="require exactly one safe reason"):
        PublicMissionActivityMap(state=MissionActivityState.UNAVAILABLE)
    with pytest.raises(ValueError, match="greater than or equal to 10"):
        PublicMissionActivityCountry(
            region_code="US",
            level=MissionActivityRegionLevel.COUNTRY,
            accepted_count=9,
        )
    with pytest.raises(ValueError):
        PublicMissionActivityCountry(
            region_code="419",
            level=MissionActivityRegionLevel.COUNTRY,
            accepted_count=10,
        )
    with pytest.raises(ValueError):
        PublicMissionActivityMacroregion(
            region_code="US",
            level=MissionActivityRegionLevel.MACROREGION,
            accepted_count=10,
        )
    with pytest.raises(ValueError, match="Live activity maps require"):
        PublicMissionActivityMap(state=MissionActivityState.LIVE)
    with pytest.raises(ValueError, match="Unavailable activity maps cannot contain regions"):
        PublicMissionActivityMap(
            state=MissionActivityState.UNAVAILABLE,
            reason="disabled",
            regions=(
                PublicMissionActivityCountry(
                    region_code="US",
                    level=MissionActivityRegionLevel.COUNTRY,
                    accepted_count=10,
                ),
            ),
        )
    with pytest.raises(ValueError, match="Zero activity maps cannot contain regions"):
        PublicMissionActivityMap(
            state=MissionActivityState.ZERO,
            regions=(
                PublicMissionActivityCountry(
                    region_code="US",
                    level=MissionActivityRegionLevel.COUNTRY,
                    accepted_count=10,
                ),
            ),
        )
