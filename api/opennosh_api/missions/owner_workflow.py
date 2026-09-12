from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from opennosh_api.capacity import ProcessRole, load_capacity_manifest
from opennosh_api.database import DatabaseIdentity, build_engine
from opennosh_api.missions.contracts import (
    MissionDefinitionSpec,
    MissionGapKind,
    MissionLifecycleAction,
)
from opennosh_api.missions.progress_service import (
    BindMissionContribution,
    RebuildMissionProgress,
    bind_mission_contribution,
    rebuild_mission_progress,
)
from opennosh_api.missions.repository import MissionRepository
from opennosh_api.missions.service import (
    ProposeMission,
    TransitionMission,
    propose_mission,
    transition_mission,
)
from opennosh_api.settings import Settings

_MAX_RECORDS = 10
_MISSION_KEY = re.compile(r"^[a-z0-9][a-z0-9-]{0,119}$")


class OwnerMissionSelectionError(RuntimeError):
    """The requested immutable owner publication set could not be proven exactly."""


class OwnerMissionReadinessError(RuntimeError):
    """The disabled production configuration is not ready for this exact mission."""


@dataclass(frozen=True, slots=True)
class OwnerMissionRecord:
    record_id: str
    source_draft_id: UUID
    source_draft_version: int
    accepted_event_id: UUID
    receipt_digest: str
    owner_authorization_id: UUID
    published_at: datetime


@dataclass(frozen=True, slots=True)
class OwnerMissionReadiness:
    schema_version: str
    status: str
    readiness_digest: str
    release_version: str
    deployed_commit: str | None
    mission_key: str
    pack_id: str
    acceptance_target: int
    definition: dict[str, object]
    actor_reference_sha256: str
    records: tuple[dict[str, object], ...]
    current_mission_flags: dict[str, bool]
    authorized_operations: tuple[str, ...]
    activation_changes: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class OwnerMissionReport:
    schema_version: str
    mission_id: str
    definition_id: str
    proposal_event_id: str
    approval_event_id: str
    approval_mode: str
    owner_authorization_id: str
    pack_id: str
    acceptance_target: int
    accepted_count: int
    matched_event_count: int
    event_set_digest: str
    checkpoint_id: str
    record_ids: tuple[str, ...]
    receipt_digests: tuple[str, ...]
    readiness_digest: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _mission_spec(pack_id: str, target: int) -> MissionDefinitionSpec:
    return MissionDefinitionSpec(
        gap_kind=MissionGapKind.DATASET,
        title=f"Expand the {pack_id} Commons pack",
        summary=(
            "Add verified foods with signed publication receipts to the public Commons."
        ),
        target_pack_id=pack_id,
        target_dataset="foods",
        acceptance_target=target,
        acceptance_criteria=(
            "Count distinct current published records in the target pack with verified signed "
            "receipts that were explicitly bound to this mission."
        ),
    )


def _require_disabled_flags(settings: Settings) -> dict[str, bool]:
    flags = {
        "MISSION_MUTATIONS_ENABLED": settings.mission_mutations_enabled,
        "MISSION_PROJECTION_ENABLED": settings.mission_projection_enabled,
        "MISSION_PUBLIC_ENABLED": settings.mission_public_enabled,
        "MISSION_ACTIVITY_MAP_ENABLED": settings.mission_activity_map_enabled,
        "MISSION_PACK_RELEASE_ENABLED": settings.mission_pack_release_enabled,
    }
    if any(flags.values()):
        raise OwnerMissionReadinessError("owner_mission_requires_disabled_feature_flags")
    return flags


async def _select_records(
    session: AsyncSession,
    *,
    actor_id: UUID,
    pack_id: str,
    record_ids: tuple[str, ...],
    now: datetime,
) -> tuple[OwnerMissionRecord, ...]:
    normalized = tuple(dict.fromkeys(item.strip() for item in record_ids if item.strip()))
    if not normalized or len(normalized) > _MAX_RECORDS or len(normalized) != len(record_ids):
        raise OwnerMissionSelectionError("owner_mission_record_scope_invalid")
    statement = text(
        """
        SELECT accepted.record_id,
               draft.id AS source_draft_id,
               draft.draft_version AS source_draft_version,
               accepted.id AS accepted_event_id,
               accepted.receipt_digest,
               owner_auth.id AS owner_authorization_id,
               accepted.published_at
          FROM accepted_events accepted
          JOIN publication_intents intent
            ON intent.id = accepted.publication_intent_id
           AND intent.state = 'published'
          JOIN contribution_drafts draft
            ON draft.id = intent.source_draft_id
           AND draft.draft_version = intent.source_draft_version
           AND draft.user_id = :actor_id
           AND draft.review_state IN ('publication_pending', 'published')
          JOIN governance_decisions decision
            ON decision.id = intent.reviewed_decision_id
           AND decision.source_draft_id = draft.id
           AND decision.source_draft_version = draft.draft_version
           AND decision.pack_id = :pack_id
           AND decision.record_id = accepted.record_id
           AND decision.outcome = 'approved'
           AND decision.approval_mode = 'owner'
           AND decision.contributor_actor_id = :actor_id
           AND decision.deciding_actor_id = :actor_id
          JOIN governance_owner_authorizations owner_auth
            ON owner_auth.id = decision.owner_authorization_id
           AND owner_auth.actor_id = :actor_id
           AND owner_auth.pack_id = :pack_id
           AND owner_auth.role = 'owner'
           AND owner_auth.granted_at <= :now
           AND (owner_auth.revoked_at IS NULL OR owner_auth.revoked_at > :now)
          JOIN publication_receipts receipt
            ON receipt.receipt_digest = accepted.receipt_digest
           AND receipt.publication_intent_id = intent.id
           AND receipt.pack_id = :pack_id
           AND receipt.record_id = accepted.record_id
           AND receipt.event_type != 'revocation'
           AND receipt.reconciled_at >= receipt.published_at
         WHERE accepted.pack_id = :pack_id
           AND accepted.event_type = 'record.published'
           AND accepted.record_id IN :record_ids
         ORDER BY accepted.record_id, accepted.published_at, accepted.id
        """
    ).bindparams(bindparam("record_ids", expanding=True))
    rows = (
        (
            await session.execute(
                statement,
                {
                    "actor_id": actor_id,
                    "pack_id": pack_id,
                    "record_ids": normalized,
                    "now": now,
                },
            )
        )
        .mappings()
        .all()
    )
    found = tuple(str(row["record_id"]) for row in rows)
    if len(rows) != len(normalized) or set(found) != set(normalized):
        raise OwnerMissionSelectionError("owner_mission_publication_set_not_exact")
    return tuple(
        OwnerMissionRecord(
            record_id=str(row["record_id"]),
            source_draft_id=UUID(str(row["source_draft_id"])),
            source_draft_version=int(row["source_draft_version"]),
            accepted_event_id=UUID(str(row["accepted_event_id"])),
            receipt_digest=str(row["receipt_digest"]),
            owner_authorization_id=UUID(str(row["owner_authorization_id"])),
            published_at=row["published_at"],
        )
        for row in rows
    )


def _readiness_payload(
    *,
    settings: Settings,
    actor_id: UUID,
    mission_key: str,
    pack_id: str,
    records: tuple[OwnerMissionRecord, ...],
    flags: dict[str, bool],
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "release_version": version("opennosh"),
        "deployed_commit": settings.render_git_commit,
        "mission_key": mission_key,
        "pack_id": pack_id,
        "acceptance_target": len(records),
        "definition": _mission_spec(pack_id, len(records)).model_dump(mode="json"),
        "actor_reference_sha256": hashlib.sha256(str(actor_id).encode()).hexdigest(),
        "records": [
            {
                "record_id": record.record_id,
                "source_draft_id": str(record.source_draft_id),
                "source_draft_version": record.source_draft_version,
                "accepted_event_id": str(record.accepted_event_id),
                "receipt_digest": record.receipt_digest,
                "owner_authorization_id": str(record.owner_authorization_id),
                "published_at": record.published_at.astimezone(UTC)
                .isoformat()
                .replace("+00:00", "Z"),
            }
            for record in records
        ],
        "current_mission_flags": flags,
        "authorized_operations": [
            "owner_propose",
            "owner_approve",
            "bind_exact_published_versions",
            "one_off_progress_rebuild",
        ],
        "activation_changes": {
            "opennosh-api.MISSION_PUBLIC_ENABLED": True,
            "opennosh-web.OPENNOSH_PUBLIC_NAV_FEATURES_add": "commons-missions",
        },
    }


def _digest(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def collect_owner_mission_readiness(
    settings: Settings,
    *,
    actor_id: UUID,
    mission_key: str,
    pack_id: str,
    record_ids: tuple[str, ...],
) -> OwnerMissionReadiness:
    if not _MISSION_KEY.fullmatch(mission_key):
        raise OwnerMissionReadinessError("owner_mission_key_invalid")
    flags = _require_disabled_flags(settings)
    now = datetime.now(UTC)
    engine = _web_engine(settings)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            records = await _select_records(
                session,
                actor_id=actor_id,
                pack_id=pack_id,
                record_ids=record_ids,
                now=now,
            )
        payload = _readiness_payload(
            settings=settings,
            actor_id=actor_id,
            mission_key=mission_key,
            pack_id=pack_id,
            records=records,
            flags=flags,
        )
        return OwnerMissionReadiness(
            schema_version="1.0",
            status="ready",
            readiness_digest=_digest(payload),
            release_version=version("opennosh"),
            deployed_commit=settings.render_git_commit,
            mission_key=mission_key,
            pack_id=pack_id,
            acceptance_target=len(records),
            definition=_mission_spec(pack_id, len(records)).model_dump(mode="json"),
            actor_reference_sha256=hashlib.sha256(str(actor_id).encode()).hexdigest(),
            records=tuple(
                {
                    "record_id": record.record_id,
                    "source_draft_id": str(record.source_draft_id),
                    "source_draft_version": record.source_draft_version,
                    "accepted_event_id": str(record.accepted_event_id),
                    "receipt_digest": record.receipt_digest,
                    "owner_authorization_id": str(record.owner_authorization_id),
                    "published_at": record.published_at.astimezone(UTC)
                    .isoformat()
                    .replace("+00:00", "Z"),
                }
                for record in records
            ),
            current_mission_flags=flags,
            authorized_operations=(
                "owner_propose",
                "owner_approve",
                "bind_exact_published_versions",
                "one_off_progress_rebuild",
            ),
            activation_changes={
                "opennosh-api.MISSION_PUBLIC_ENABLED": True,
                "opennosh-web.OPENNOSH_PUBLIC_NAV_FEATURES_add": "commons-missions",
            },
        )
    finally:
        await engine.dispose()


async def run_owner_mission(
    settings: Settings,
    *,
    actor_id: UUID,
    mission_key: str,
    pack_id: str,
    record_ids: tuple[str, ...],
    approved_readiness_digest: str,
) -> OwnerMissionReport:
    if not _MISSION_KEY.fullmatch(mission_key):
        raise OwnerMissionReadinessError("owner_mission_key_invalid")
    flags = _require_disabled_flags(settings)
    now = datetime.now(UTC)
    engine = _web_engine(settings)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session, session.begin():
            records = await _select_records(
                session,
                actor_id=actor_id,
                pack_id=pack_id,
                record_ids=record_ids,
                now=now,
            )
            readiness = _readiness_payload(
                settings=settings,
                actor_id=actor_id,
                mission_key=mission_key,
                pack_id=pack_id,
                records=records,
                flags=flags,
            )
            actual_digest = _digest(readiness)
            if actual_digest != approved_readiness_digest:
                raise OwnerMissionReadinessError("owner_mission_readiness_digest_mismatch")

            namespace = uuid5(NAMESPACE_URL, f"https://opennosh.org/missions/{mission_key}")
            mission_id = uuid5(namespace, "mission")
            definition_id = uuid5(namespace, "definition:1")
            proposal_id = uuid5(namespace, "event:propose")
            approval_id = uuid5(namespace, "event:approve")
            repository = MissionRepository(session)
            _definition, proposal = await propose_mission(
                repository,
                ProposeMission(
                    mission_id=mission_id,
                    definition_id=definition_id,
                    event_id=proposal_id,
                    actor_id=actor_id,
                    responsible_steward_actor_id=actor_id,
                    definition=_mission_spec(pack_id, len(records)),
                    public_reason=(
                        "Open a small owner-run mission to expand the target Commons pack."
                    ),
                ),
                now=now,
            )
            approval = await transition_mission(
                repository,
                TransitionMission(
                    mission_id=mission_id,
                    definition_id=definition_id,
                    event_id=approval_id,
                    expected_prior_event_id=proposal.id,
                    actor_id=actor_id,
                    action=MissionLifecycleAction.APPROVE,
                    public_reason=(
                        "Owner approval authorizes the measured pilot under the active pack grant."
                    ),
                ),
                now=now,
            )
            for record in records:
                await bind_mission_contribution(
                    repository,
                    BindMissionContribution(
                        binding_id=uuid5(namespace, f"binding:{record.record_id}"),
                        mission_id=mission_id,
                        definition_id=definition_id,
                        source_draft_id=record.source_draft_id,
                        source_draft_version=record.source_draft_version,
                        actor_id=actor_id,
                    ),
                    now=now,
                )
            current = await repository.progress_activation(definition_id)
            checkpoint_id = uuid5(
                namespace,
                "checkpoint:" + ",".join(record.receipt_digest for record in records),
            )
            build = await rebuild_mission_progress(
                repository,
                RebuildMissionProgress(
                    checkpoint_id=checkpoint_id,
                    activation_id=uuid5(namespace, "activation"),
                    mission_id=mission_id,
                    definition_id=definition_id,
                    expected_active_checkpoint_id=(
                        current.checkpoint_id if current is not None else None
                    ),
                ),
                now=now,
            )
            if build.progress.accepted_count != len(records):
                raise OwnerMissionSelectionError("owner_mission_target_not_met")
            if approval.approval_mode != "owner" or approval.owner_authorization_id is None:
                raise OwnerMissionSelectionError("owner_mission_approval_not_attributed")
            return OwnerMissionReport(
                schema_version="1.0",
                mission_id=str(mission_id),
                definition_id=str(definition_id),
                proposal_event_id=str(proposal.id),
                approval_event_id=str(approval.id),
                approval_mode=approval.approval_mode,
                owner_authorization_id=str(approval.owner_authorization_id),
                pack_id=pack_id,
                acceptance_target=len(records),
                accepted_count=build.progress.accepted_count,
                matched_event_count=build.progress.matched_event_count,
                event_set_digest=build.progress.event_set_digest,
                checkpoint_id=str(build.checkpoint.id),
                record_ids=tuple(record.record_id for record in records),
                receipt_digests=tuple(record.receipt_digest for record in records),
                readiness_digest=actual_digest,
            )
    finally:
        await engine.dispose()


def _web_engine(settings: Settings) -> AsyncEngine:
    manifest = load_capacity_manifest(settings.database_capacity_manifest_path)
    return build_engine(
        settings.process_database_url(ProcessRole.WEB),
        identity=DatabaseIdentity(
            deployment_id=manifest.deployment_id,
            role="owner-mission-one-off",
        ),
        budget=manifest.active_role_budget(ProcessRole.WEB),
    )


__all__ = [
    "OwnerMissionReadinessError",
    "OwnerMissionReadiness",
    "OwnerMissionReport",
    "OwnerMissionSelectionError",
    "collect_owner_mission_readiness",
    "run_owner_mission",
]
