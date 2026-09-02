from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from opennosh_api.missions.repository import MissionActivityLocaleCount

MINIMUM_ACTIVITY_COHORT = 10
MAX_ACTIVITY_MISSIONS = 100
MAX_ACTIVITY_RECORDS = 10_000
MAX_ACTIVITY_LINEAGE_EVENTS = 20_000

_COUNTRY_REGION = re.compile(r"^[A-Za-z]{2}$")
_MACROREGION = re.compile(r"^[0-9]{3}$")


class MissionActivityState(StrEnum):
    UNAVAILABLE = "unavailable"
    ZERO = "zero"
    LIVE = "live"


class MissionActivityRegionLevel(StrEnum):
    COUNTRY = "country"
    MACROREGION = "macroregion"


class PublicMissionActivityCountry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    region_code: Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
    level: Literal[MissionActivityRegionLevel.COUNTRY]
    accepted_count: Annotated[int, Field(ge=MINIMUM_ACTIVITY_COHORT)]


class PublicMissionActivityMacroregion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    region_code: Annotated[str, Field(pattern=r"^[0-9]{3}$")]
    level: Literal[MissionActivityRegionLevel.MACROREGION]
    accepted_count: Annotated[int, Field(ge=MINIMUM_ACTIVITY_COHORT)]


PublicMissionActivityRegion = Annotated[
    PublicMissionActivityCountry | PublicMissionActivityMacroregion,
    Field(discriminator="level"),
]


class PublicMissionActivityMap(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    state: MissionActivityState
    reason: Literal["disabled", "proof_unavailable"] | None = None
    minimum_cohort: Literal[10] = 10
    regions: tuple[PublicMissionActivityRegion, ...] = ()

    @model_validator(mode="after")
    def validate_state_shape(self) -> PublicMissionActivityMap:
        unavailable = self.state is MissionActivityState.UNAVAILABLE
        if unavailable != (self.reason is not None):
            raise ValueError("Unavailable activity maps require exactly one safe reason")
        if unavailable and self.regions:
            raise ValueError("Unavailable activity maps cannot contain regions")
        if self.state is MissionActivityState.ZERO and self.regions:
            raise ValueError("Zero activity maps cannot contain regions")
        if self.state is MissionActivityState.LIVE and not self.regions:
            raise ValueError("Live activity maps require at least one region")
        return self


class MissionActivityStore(Protocol):
    async def public_mission_activity_locales(
        self,
        max_missions: int,
        max_records: int,
        max_lineage_events: int,
    ) -> tuple[MissionActivityLocaleCount, ...]: ...


async def public_mission_activity_map(
    store: MissionActivityStore,
    *,
    enabled: bool,
) -> PublicMissionActivityMap:
    """Publish only independently thresholded pack-region cohorts.

    The response intentionally has no filters, total, suppressed count, timestamp, or contributor
    dimensions. That keeps repeated queries from recovering a hidden cohort by subtraction.
    """

    if not enabled:
        return PublicMissionActivityMap(
            state=MissionActivityState.UNAVAILABLE,
            reason="disabled",
        )

    locale_counts = await store.public_mission_activity_locales(
        MAX_ACTIVITY_MISSIONS,
        MAX_ACTIVITY_RECORDS,
        MAX_ACTIVITY_LINEAGE_EVENTS,
    )
    by_region: dict[tuple[str, MissionActivityRegionLevel], int] = {}
    for locale_count in locale_counts:
        region = _region_from_pack_locale(locale_count.locale)
        if region is None:
            continue
        by_region[region] = by_region.get(region, 0) + locale_count.accepted_count

    regions = tuple(
        (
            PublicMissionActivityCountry(
                region_code=region_code,
                level=MissionActivityRegionLevel.COUNTRY,
                accepted_count=accepted_count,
            )
            if level is MissionActivityRegionLevel.COUNTRY
            else PublicMissionActivityMacroregion(
                region_code=region_code,
                level=MissionActivityRegionLevel.MACROREGION,
                accepted_count=accepted_count,
            )
        )
        for (region_code, level), accepted_count in sorted(
            by_region.items(),
            key=lambda item: (-item[1], item[0][0]),
        )
        if accepted_count >= MINIMUM_ACTIVITY_COHORT
    )
    return PublicMissionActivityMap(
        state=MissionActivityState.LIVE if regions else MissionActivityState.ZERO,
        regions=regions,
    )


def unavailable_public_mission_activity_map() -> PublicMissionActivityMap:
    return PublicMissionActivityMap(
        state=MissionActivityState.UNAVAILABLE,
        reason="proof_unavailable",
    )


def _region_from_pack_locale(
    locale: str,
) -> tuple[str, MissionActivityRegionLevel] | None:
    """Extract only an explicit BCP 47 region; language alone is not a location claim."""

    for candidate in locale.split("-")[1:]:
        if _COUNTRY_REGION.fullmatch(candidate):
            return candidate.upper(), MissionActivityRegionLevel.COUNTRY
        if _MACROREGION.fullmatch(candidate):
            return candidate, MissionActivityRegionLevel.MACROREGION
    return None


__all__ = [
    "MAX_ACTIVITY_MISSIONS",
    "MAX_ACTIVITY_RECORDS",
    "MAX_ACTIVITY_LINEAGE_EVENTS",
    "MINIMUM_ACTIVITY_COHORT",
    "MissionActivityRegionLevel",
    "MissionActivityState",
    "PublicMissionActivityMap",
    "PublicMissionActivityCountry",
    "PublicMissionActivityMacroregion",
    "PublicMissionActivityRegion",
    "public_mission_activity_map",
    "unavailable_public_mission_activity_map",
]
