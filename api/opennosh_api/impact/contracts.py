from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MINIMUM_IMPACT_COHORT = 10
_COUNTRY = re.compile(r"^[A-Z]{2}$")
_MACROREGION = re.compile(r"^[0-9]{3}$")


class ImpactState(StrEnum):
    UNAVAILABLE = "unavailable"
    ZERO = "zero"
    LIVE = "live"


class ImpactRegionLevel(StrEnum):
    COUNTRY = "country"
    MACROREGION = "macroregion"


class ImpactTotals(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    verified_adopters: Annotated[int, Field(ge=0)] = 0
    community_declarations: Annotated[int, Field(ge=0)] = 0
    accepted_contributions: Annotated[int, Field(ge=0)] = 0
    pack_installs: Annotated[int, Field(ge=0)] = 0
    api_reads: Annotated[int, Field(ge=0)] = 0
    artifact_downloads: Annotated[int, Field(ge=0)] = 0

    def has_activity(self) -> bool:
        return any(self.model_dump().values())


class ImpactRegion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    level: ImpactRegionLevel
    region_code: Annotated[str, Field(min_length=2, max_length=3)]
    verified_adopters: Annotated[int, Field(ge=0)] = 0
    community_declarations: Annotated[int, Field(ge=0)] = 0
    accepted_contributions: Annotated[int, Field(ge=0)] = 0

    @model_validator(mode="after")
    def validate_region(self) -> ImpactRegion:
        if self.level is ImpactRegionLevel.COUNTRY and not _COUNTRY.fullmatch(self.region_code):
            raise ValueError("Country impact regions require an uppercase ISO alpha-2 code")
        if self.level is ImpactRegionLevel.MACROREGION and not _MACROREGION.fullmatch(
            self.region_code
        ):
            raise ValueError("Macroregion impact regions require a three-digit UN M49 code")
        if not (
            self.verified_adopters
            or self.community_declarations
            or self.accepted_contributions
        ):
            raise ValueError("Published impact regions require activity")
        return self


class PublicImpactSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    schema_version: Literal["1.0"] = "1.0"
    state: ImpactState
    reason: Literal["disabled", "proof_unavailable"] | None = None
    metric_definition_version: Literal["1.0"] = "1.0"
    observed_at: datetime
    source_checkpoint_id: Annotated[str | None, Field(min_length=1, max_length=160)] = None
    minimum_cohort: Literal[10] = 10
    global_: ImpactTotals = Field(alias="global", serialization_alias="global")
    regions: tuple[ImpactRegion, ...] = ()
    digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

    @field_validator("observed_at")
    @classmethod
    def validate_observed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Impact observation time must include a timezone")
        if value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("Impact observation time must use UTC")
        return value

    @model_validator(mode="after")
    def validate_shape_and_digest(self) -> PublicImpactSnapshot:
        unavailable = self.state is ImpactState.UNAVAILABLE
        if unavailable != (self.reason is not None):
            raise ValueError("Unavailable impact snapshots require exactly one safe reason")
        if unavailable and (
            self.source_checkpoint_id is not None or self.global_.has_activity() or self.regions
        ):
            raise ValueError("Unavailable impact snapshots cannot contain proof or activity")
        if not unavailable and self.source_checkpoint_id is None:
            raise ValueError("Released impact snapshots require a source checkpoint")
        has_activity = self.global_.has_activity() or bool(self.regions)
        if self.state is ImpactState.ZERO and has_activity:
            raise ValueError("Zero impact snapshots cannot contain activity")
        if self.state is ImpactState.LIVE and not has_activity:
            raise ValueError("Live impact snapshots require activity")
        if self.digest != impact_digest(self):
            raise ValueError("Impact snapshot digest does not match canonical content")
        return self


def _canonical_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _canonical_value(value.model_dump(mode="python", by_alias=True))
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {key: _canonical_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    return value


def impact_digest(snapshot: PublicImpactSnapshot | dict[str, Any]) -> str:
    payload = (
        snapshot.model_dump(mode="python", by_alias=True)
        if isinstance(snapshot, PublicImpactSnapshot)
        else dict(snapshot)
    )
    if "global_" in payload:
        payload["global"] = payload.pop("global_")
    payload.pop("digest", None)
    encoded = json.dumps(
        _canonical_value(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def signed_impact_snapshot(**values: Any) -> PublicImpactSnapshot:
    payload = {
        "schema_version": "1.0",
        "metric_definition_version": "1.0",
        "minimum_cohort": MINIMUM_IMPACT_COHORT,
        **values,
        "digest": "0" * 64,
    }
    if "global_" in payload:
        payload["global"] = payload.pop("global_")
    payload["global"] = ImpactTotals.model_validate(payload["global"])
    payload["regions"] = tuple(ImpactRegion.model_validate(region) for region in payload["regions"])
    payload["digest"] = impact_digest(payload)
    return PublicImpactSnapshot.model_validate(payload)


def unavailable_impact_snapshot(
    reason: Literal["disabled", "proof_unavailable"],
) -> PublicImpactSnapshot:
    return signed_impact_snapshot(
        state=ImpactState.UNAVAILABLE,
        reason=reason,
        observed_at=datetime(1970, 1, 1, tzinfo=UTC),
        source_checkpoint_id=None,
        global_=ImpactTotals(),
        regions=(),
    )


__all__ = [
    "ImpactRegion",
    "ImpactRegionLevel",
    "ImpactState",
    "ImpactTotals",
    "MINIMUM_IMPACT_COHORT",
    "PublicImpactSnapshot",
    "impact_digest",
    "signed_impact_snapshot",
    "unavailable_impact_snapshot",
]
