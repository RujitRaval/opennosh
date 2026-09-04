from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.impact.contracts import (
    MINIMUM_IMPACT_COHORT,
    ImpactRegion,
    ImpactRegionLevel,
    ImpactState,
    ImpactTotals,
    PublicImpactSnapshot,
    signed_impact_snapshot,
)
from opennosh_api.impact.models import ImpactSnapshot

DeclarationState = Literal[
    "community_declared", "verification_pending", "verified", "withdrawn"
]
_DECLARATION_STATES = {
    "community_declared",
    "verification_pending",
    "verified",
    "withdrawn",
}


def _validate_fact_region(
    level: ImpactRegionLevel | None,
    code: str | None,
) -> None:
    if (level is None) != (code is None):
        raise ValueError("Impact fact region proof must be complete")
    if level is not None and code is not None:
        ImpactRegion(level=level, region_code=code, community_declarations=1)


@dataclass(frozen=True, slots=True)
class ImpactDeclarationFact:
    declaration_id: UUID
    owner_actor_id: UUID
    organization_key: str
    state: DeclarationState
    evidence_accessible: bool = False
    evidence_fresh: bool = False
    region_level: ImpactRegionLevel | None = None
    region_code: str | None = None

    def __post_init__(self) -> None:
        if self.state not in _DECLARATION_STATES:
            raise ValueError("Impact declaration state is unsupported")
        if not self.organization_key or len(self.organization_key) > 160:
            raise ValueError("Impact organization key is invalid")
        _validate_fact_region(self.region_level, self.region_code)


@dataclass(frozen=True, slots=True)
class ImpactContributionFact:
    accepted_event_id: UUID
    actor_id: UUID
    region_level: ImpactRegionLevel | None = None
    region_code: str | None = None

    def __post_init__(self) -> None:
        _validate_fact_region(self.region_level, self.region_code)


@dataclass(frozen=True, slots=True)
class ImpactOperationalProof:
    source_checkpoint_id: str
    pack_installs: int
    api_reads: int
    artifact_downloads: int

    def __post_init__(self) -> None:
        if not self.source_checkpoint_id or len(self.source_checkpoint_id) > 160:
            raise ValueError("Impact checkpoint ID is required")
        if min(self.pack_installs, self.api_reads, self.artifact_downloads) < 0:
            raise ValueError("Impact operational proof counts cannot be negative")


def aggregate_impact_snapshot(
    *,
    declarations: tuple[ImpactDeclarationFact, ...],
    contributions: tuple[ImpactContributionFact, ...],
    operational: ImpactOperationalProof,
    observed_at: datetime,
) -> PublicImpactSnapshot:
    """Build one non-queryable release snapshot and discard cardinality identifiers.

    Identifiers in the input are used only to deduplicate and enforce k-anonymity. They are never
    returned by this function or written by ``persist_impact_snapshot``.
    """

    current = tuple(fact for fact in declarations if fact.state != "withdrawn")
    verified_orgs = {
        fact.organization_key
        for fact in current
        if fact.state == "verified" and fact.evidence_accessible and fact.evidence_fresh
    }
    community_declaration_ids = {
        fact.declaration_id for fact in current if fact.state == "community_declared"
    }
    contribution_ids = {fact.accepted_event_id for fact in contributions}
    totals = ImpactTotals(
        verified_adopters=len(verified_orgs),
        community_declarations=len(community_declaration_ids),
        accepted_contributions=len(contribution_ids),
        pack_installs=operational.pack_installs,
        api_reads=operational.api_reads,
        artifact_downloads=operational.artifact_downloads,
    )

    region_buckets: dict[
        tuple[ImpactRegionLevel, str],
        dict[str, set[str]],
    ] = {}
    for declaration in current:
        if declaration.region_level is None or declaration.region_code is None:
            continue
        bucket = region_buckets.setdefault(
            (declaration.region_level, declaration.region_code),
            {"cohort": set(), "verified": set(), "declarations": set(), "contributions": set()},
        )
        if (
            declaration.state == "verified"
            and declaration.evidence_accessible
            and declaration.evidence_fresh
        ):
            bucket["verified"].add(declaration.organization_key)
            bucket["cohort"].add(f"organization:{declaration.organization_key}")
        elif declaration.state == "community_declared":
            bucket["declarations"].add(str(declaration.declaration_id))
            bucket["cohort"].add(f"actor:{declaration.owner_actor_id}")
    for contribution in contributions:
        if contribution.region_level is None or contribution.region_code is None:
            continue
        bucket = region_buckets.setdefault(
            (contribution.region_level, contribution.region_code),
            {"cohort": set(), "verified": set(), "declarations": set(), "contributions": set()},
        )
        bucket["cohort"].add(f"actor:{contribution.actor_id}")
        bucket["contributions"].add(str(contribution.accepted_event_id))

    regions = tuple(
        ImpactRegion(
            level=level,
            region_code=code,
            verified_adopters=len(bucket["verified"]),
            community_declarations=len(bucket["declarations"]),
            accepted_contributions=len(bucket["contributions"]),
        )
        for (level, code), bucket in sorted(
            region_buckets.items(), key=lambda item: (item[0][0].value, item[0][1])
        )
        if len(bucket["cohort"]) >= MINIMUM_IMPACT_COHORT
    )
    return signed_impact_snapshot(
        state=ImpactState.LIVE if totals.has_activity() or regions else ImpactState.ZERO,
        reason=None,
        observed_at=observed_at,
        source_checkpoint_id=operational.source_checkpoint_id,
        global_=totals,
        regions=regions,
    )


async def persist_impact_snapshot(
    database: AsyncSession,
    snapshot: PublicImpactSnapshot,
) -> ImpactSnapshot:
    if snapshot.state is ImpactState.UNAVAILABLE or snapshot.source_checkpoint_id is None:
        raise ValueError("Only released impact snapshots can be persisted")
    existing = await database.scalar(
        select(ImpactSnapshot).where(ImpactSnapshot.digest == snapshot.digest)
    )
    if existing is not None:
        return existing
    record = ImpactSnapshot(
        schema_version=snapshot.schema_version,
        metric_definition_version=snapshot.metric_definition_version,
        state=snapshot.state.value,
        observed_at=snapshot.observed_at,
        source_checkpoint_id=snapshot.source_checkpoint_id,
        snapshot_json=snapshot.model_dump(mode="json", by_alias=True),
        digest=snapshot.digest,
    )
    database.add(record)
    await database.flush()
    return record


async def latest_impact_snapshot(database: AsyncSession) -> PublicImpactSnapshot | None:
    record = await database.scalar(
        select(ImpactSnapshot)
        .order_by(ImpactSnapshot.observed_at.desc(), ImpactSnapshot.id)
        .limit(1)
    )
    if record is None:
        return None
    snapshot = PublicImpactSnapshot.model_validate(record.snapshot_json)
    if (
        snapshot.schema_version != record.schema_version
        or snapshot.metric_definition_version != record.metric_definition_version
        or snapshot.state.value != record.state
        or snapshot.observed_at != record.observed_at
        or snapshot.source_checkpoint_id != record.source_checkpoint_id
        or snapshot.digest != record.digest
    ):
        raise ValueError("impact_snapshot_metadata_mismatch")
    return snapshot


__all__ = [
    "ImpactContributionFact",
    "ImpactDeclarationFact",
    "ImpactOperationalProof",
    "aggregate_impact_snapshot",
    "latest_impact_snapshot",
    "persist_impact_snapshot",
]
