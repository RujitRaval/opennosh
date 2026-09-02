from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import bindparam, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.contributions.models import ContributionDraft
from opennosh_api.foodpacks.validation import parse_pack_manifest
from opennosh_api.governance.contracts import ApprovedChangeSet
from opennosh_api.governance.models import GovernanceDecision
from opennosh_api.missions.contracts import AcceptedMissionFact, MissionBindingFact
from opennosh_api.missions.models import (
    MissionContributionBinding,
    MissionDefinition,
    MissionLifecycleEvent,
    MissionProgressActivation,
    MissionProgressCheckpoint,
)
from opennosh_api.missions.models import (
    MissionProgressRecord as StoredMissionProgressRecord,
)
from opennosh_api.missions.projector import MissionProjectionError, project_mission_progress
from opennosh_api.publication.models import (
    AcceptedEvent,
    PublicationIntent,
    PublicationReceiptRecord,
)

_PACK_LOCALE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
_PACK_VERSION = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


@dataclass(frozen=True, slots=True)
class MissionProjectionInputs:
    bindings: tuple[MissionBindingFact, ...]
    accepted_events: tuple[AcceptedMissionFact, ...]


@dataclass(frozen=True, slots=True)
class PublicMissionSnapshot:
    definition: MissionDefinition
    lifecycle_event: MissionLifecycleEvent
    checkpoint: MissionProgressCheckpoint | None
    progress_is_current: bool


@dataclass(frozen=True, slots=True)
class MissionActivityLocaleCount:
    locale: str
    accepted_count: int


class MissionRepository:
    """Transaction-scoped persistence for governed mission lifecycle changes."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock_mission(self, mission_id: UUID) -> None:
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
            {"scope": f"opennosh:mission:{mission_id}"},
        )

    async def lock_contribution_version(self, draft_id: UUID, draft_version: int) -> None:
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
            {"scope": f"opennosh:mission-binding:{draft_id}:{draft_version}"},
        )

    async def definition(self, definition_id: UUID) -> MissionDefinition | None:
        return cast(
            MissionDefinition | None,
            await self._session.scalar(
                select(MissionDefinition).where(MissionDefinition.id == definition_id)
            ),
        )

    async def latest_definition(self, mission_id: UUID) -> MissionDefinition | None:
        return cast(
            MissionDefinition | None,
            await self._session.scalar(
                select(MissionDefinition)
                .where(MissionDefinition.mission_id == mission_id)
                .order_by(MissionDefinition.definition_version.desc())
                .limit(1)
            ),
        )

    async def _public_mission_snapshot_rows(
        self, limit: int
    ) -> tuple[
        tuple[MissionDefinition, MissionLifecycleEvent, MissionProgressCheckpoint | None], ...
    ]:
        """Load bounded moderated latest definitions without materializing progress proof."""

        latest_versions = (
            select(
                MissionDefinition.mission_id.label("mission_id"),
                func.max(MissionDefinition.definition_version).label("definition_version"),
            )
            .group_by(MissionDefinition.mission_id)
            .subquery()
        )
        latest_sequences = (
            select(
                MissionLifecycleEvent.mission_id.label("mission_id"),
                func.max(MissionLifecycleEvent.sequence).label("sequence"),
            )
            .group_by(MissionLifecycleEvent.mission_id)
            .subquery()
        )
        latest_event_definition = (
            select(MissionLifecycleEvent.definition_id)
            .where(MissionLifecycleEvent.mission_id == MissionDefinition.mission_id)
            .order_by(MissionLifecycleEvent.sequence.desc())
            .limit(1)
            .scalar_subquery()
        )
        invalid_definition = await self._session.scalar(
            select(MissionDefinition.id)
            .join(
                latest_versions,
                (latest_versions.c.mission_id == MissionDefinition.mission_id)
                & (latest_versions.c.definition_version == MissionDefinition.definition_version),
            )
            .where(
                (latest_event_definition.is_(None))
                | (latest_event_definition != MissionDefinition.id)
            )
            .limit(1)
        )
        if invalid_definition is not None:
            raise ValueError("public_mission_definition_proof_unavailable")

        rows = (
            await self._session.execute(
                select(
                    MissionDefinition,
                    MissionLifecycleEvent,
                    MissionProgressCheckpoint,
                )
                .join(
                    latest_versions,
                    (latest_versions.c.mission_id == MissionDefinition.mission_id)
                    & (
                        latest_versions.c.definition_version == MissionDefinition.definition_version
                    ),
                )
                .join(
                    latest_sequences,
                    latest_sequences.c.mission_id == MissionDefinition.mission_id,
                )
                .join(
                    MissionLifecycleEvent,
                    (MissionLifecycleEvent.mission_id == latest_sequences.c.mission_id)
                    & (MissionLifecycleEvent.sequence == latest_sequences.c.sequence)
                    & (MissionLifecycleEvent.definition_id == MissionDefinition.id),
                )
                .outerjoin(
                    MissionProgressActivation,
                    MissionProgressActivation.definition_id == MissionDefinition.id,
                )
                .outerjoin(
                    MissionProgressCheckpoint,
                    (MissionProgressCheckpoint.id == MissionProgressActivation.checkpoint_id)
                    & (MissionProgressCheckpoint.definition_id == MissionDefinition.id),
                )
                .where(MissionLifecycleEvent.action != "propose")
                .order_by(MissionDefinition.defined_at.desc(), MissionDefinition.id)
                .limit(limit)
            )
        ).all()
        return tuple(
            cast(
                tuple[
                    MissionDefinition,
                    MissionLifecycleEvent,
                    MissionProgressCheckpoint | None,
                ],
                row,
            )
            for row in rows
        )

    async def public_mission_snapshots(self, limit: int) -> tuple[PublicMissionSnapshot, ...]:
        """Load moderated latest definitions and verify progress with bounded query fanout."""

        rows = await self._public_mission_snapshot_rows(limit)
        currentness = await self._progress_currentness(
            tuple(
                (definition, checkpoint)
                for definition, _event, checkpoint in rows
                if checkpoint is not None
            )
        )
        return tuple(
            PublicMissionSnapshot(
                definition=definition,
                lifecycle_event=event,
                checkpoint=checkpoint,
                progress_is_current=(
                    currentness.get(checkpoint.id, False) if checkpoint is not None else False
                ),
            )
            for definition, event, checkpoint in rows
        )

    async def public_mission_activity_locales(
        self,
        max_missions: int,
        max_records: int,
        max_lineage_events: int,
    ) -> tuple[MissionActivityLocaleCount, ...]:
        """Aggregate current accepted mission records by proof-bound pack locale.

        The transaction snapshot, mission count, record count, and lineage count are bounded before
        proof material is loaded. Missing immutable locale proof fails the whole response closed.
        """

        await self._session.execute(
            text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        )
        await self._session.execute(text("SELECT set_config('statement_timeout', '3000ms', true)"))
        rows = await self._public_mission_snapshot_rows(max_missions + 1)
        if len(rows) > max_missions:
            raise ValueError("public_mission_activity_scope_too_large")
        if any(checkpoint is None for _definition, _event, checkpoint in rows):
            raise ValueError("public_mission_activity_proof_unavailable")
        checked_rows = tuple(
            (definition, checkpoint)
            for definition, _event, checkpoint in rows
            if checkpoint is not None
        )
        if sum(checkpoint.accepted_count for _definition, checkpoint in checked_rows) > max_records:
            raise ValueError("public_mission_activity_record_scope_too_large")
        if (
            sum(checkpoint.matched_event_count for _definition, checkpoint in checked_rows)
            > max_lineage_events
        ):
            raise ValueError("public_mission_activity_lineage_scope_too_large")
        definition_ids = tuple(definition.id for definition, _checkpoint in checked_rows)
        binding_count = (
            await self._session.scalar(
                select(func.count())
                .select_from(MissionContributionBinding)
                .where(MissionContributionBinding.definition_id.in_(definition_ids))
            )
            if definition_ids
            else 0
        )
        if int(binding_count or 0) > max_lineage_events:
            raise ValueError("public_mission_activity_binding_scope_too_large")
        currentness = await self._progress_currentness(
            checked_rows,
            max_lineage_events=max_lineage_events,
        )
        if any(
            not currentness.get(checkpoint.id, False) for _definition, checkpoint in checked_rows
        ):
            raise ValueError("public_mission_activity_proof_unavailable")
        checkpoint_ids = tuple(checkpoint.id for _definition, checkpoint in checked_rows)
        if not checkpoint_ids:
            return ()

        aggregate_rows = (
            await self._session.execute(
                select(
                    StoredMissionProgressRecord.activity_locale,
                    func.count(func.distinct(StoredMissionProgressRecord.accepted_event_id)),
                )
                .select_from(StoredMissionProgressRecord)
                .where(StoredMissionProgressRecord.checkpoint_id.in_(checkpoint_ids))
                .group_by(StoredMissionProgressRecord.activity_locale)
            )
        ).all()
        if any(locale is None for locale, _accepted_count in aggregate_rows):
            raise ValueError("public_mission_activity_locale_proof_unavailable")
        return tuple(
            MissionActivityLocaleCount(
                locale=cast(str, locale),
                accepted_count=int(accepted_count),
            )
            for locale, accepted_count in aggregate_rows
        )

    async def lifecycle_event(self, event_id: UUID) -> MissionLifecycleEvent | None:
        return cast(
            MissionLifecycleEvent | None,
            await self._session.scalar(
                select(MissionLifecycleEvent).where(MissionLifecycleEvent.id == event_id)
            ),
        )

    async def contribution_draft(self, draft_id: UUID) -> ContributionDraft | None:
        return cast(ContributionDraft | None, await self._session.get(ContributionDraft, draft_id))

    async def contribution_binding(self, binding_id: UUID) -> MissionContributionBinding | None:
        return cast(
            MissionContributionBinding | None,
            await self._session.get(MissionContributionBinding, binding_id),
        )

    async def contribution_binding_for_source(
        self, draft_id: UUID, draft_version: int
    ) -> MissionContributionBinding | None:
        return cast(
            MissionContributionBinding | None,
            await self._session.scalar(
                select(MissionContributionBinding).where(
                    MissionContributionBinding.source_draft_id == draft_id,
                    MissionContributionBinding.source_draft_version == draft_version,
                )
            ),
        )

    async def latest_lifecycle_event(self, mission_id: UUID) -> MissionLifecycleEvent | None:
        return cast(
            MissionLifecycleEvent | None,
            await self._session.scalar(
                select(MissionLifecycleEvent)
                .where(MissionLifecycleEvent.mission_id == mission_id)
                .order_by(MissionLifecycleEvent.sequence.desc())
                .limit(1)
            ),
        )

    async def active_progress(self, definition_id: UUID) -> MissionProgressCheckpoint | None:
        return cast(
            MissionProgressCheckpoint | None,
            await self._session.scalar(
                select(MissionProgressCheckpoint)
                .join(
                    MissionProgressActivation,
                    MissionProgressActivation.checkpoint_id == MissionProgressCheckpoint.id,
                )
                .where(MissionProgressActivation.definition_id == definition_id)
            ),
        )

    async def progress_activation(self, definition_id: UUID) -> MissionProgressActivation | None:
        return cast(
            MissionProgressActivation | None,
            await self._session.scalar(
                select(MissionProgressActivation).where(
                    MissionProgressActivation.definition_id == definition_id
                )
            ),
        )

    async def progress_checkpoint_for_digest(
        self, definition_id: UUID, event_set_digest: str
    ) -> MissionProgressCheckpoint | None:
        return cast(
            MissionProgressCheckpoint | None,
            await self._session.scalar(
                select(MissionProgressCheckpoint).where(
                    MissionProgressCheckpoint.definition_id == definition_id,
                    MissionProgressCheckpoint.event_set_digest == event_set_digest,
                )
            ),
        )

    async def progress_checkpoint(self, checkpoint_id: UUID) -> MissionProgressCheckpoint | None:
        return cast(
            MissionProgressCheckpoint | None,
            await self._session.get(MissionProgressCheckpoint, checkpoint_id),
        )

    async def progress_records(
        self, checkpoint_id: UUID
    ) -> tuple[StoredMissionProgressRecord, ...]:
        rows = (
            await self._session.scalars(
                select(StoredMissionProgressRecord)
                .where(StoredMissionProgressRecord.checkpoint_id == checkpoint_id)
                .order_by(
                    StoredMissionProgressRecord.repository,
                    StoredMissionProgressRecord.pack_id,
                    StoredMissionProgressRecord.record_id,
                )
            )
        ).all()
        return tuple(rows)

    async def receipt(self, digest: str) -> PublicationReceiptRecord | None:
        return cast(
            PublicationReceiptRecord | None,
            await self._session.scalar(
                select(PublicationReceiptRecord).where(
                    PublicationReceiptRecord.receipt_digest == digest
                )
            ),
        )

    async def _progress_currentness(
        self,
        items: tuple[tuple[MissionDefinition, MissionProgressCheckpoint], ...],
        *,
        max_lineage_events: int | None = None,
    ) -> dict[UUID, bool]:
        if not items:
            return {}

        definitions = {definition.id: definition for definition, _checkpoint in items}
        checkpoints = {checkpoint.id: checkpoint for _definition, checkpoint in items}
        binding_rows = (
            await self._session.scalars(
                select(MissionContributionBinding).where(
                    MissionContributionBinding.definition_id.in_(tuple(definitions))
                )
            )
        ).all()
        bindings_by_definition: dict[UUID, list[MissionBindingFact]] = {
            definition_id: [] for definition_id in definitions
        }
        for binding_row in binding_rows:
            bindings_by_definition[binding_row.definition_id].append(
                MissionBindingFact(
                    mission_id=binding_row.mission_id,
                    definition_id=binding_row.definition_id,
                    source_draft_id=binding_row.source_draft_id,
                    source_draft_version=binding_row.source_draft_version,
                )
            )

        lineage_query = text(
            """
            WITH RECURSIVE relevant_receipts(definition_id, receipt_digest) AS (
                SELECT binding.definition_id, accepted.receipt_digest
                FROM accepted_events AS accepted
                JOIN publication_intents AS intent
                  ON intent.id = accepted.publication_intent_id
                JOIN mission_contribution_bindings AS binding
                  ON binding.source_draft_id = intent.source_draft_id
                 AND binding.source_draft_version = intent.source_draft_version
                WHERE binding.definition_id IN :definition_ids
                UNION
                SELECT relevant.definition_id, linked.receipt_digest
                FROM relevant_receipts AS relevant
                CROSS JOIN LATERAL (
                    SELECT receipt.prior_receipt_digest AS receipt_digest
                    FROM publication_receipts AS receipt
                    WHERE receipt.receipt_digest = relevant.receipt_digest
                      AND receipt.prior_receipt_digest IS NOT NULL
                    UNION
                    SELECT receipt.receipt_digest
                    FROM publication_receipts AS receipt
                    WHERE receipt.prior_receipt_digest = relevant.receipt_digest
                ) AS linked
            )
            SELECT definition_id, receipt_digest
            FROM relevant_receipts
            ORDER BY definition_id, receipt_digest
            LIMIT :lineage_limit
            """
        ).bindparams(
            bindparam("definition_ids", expanding=True),
            bindparam("lineage_limit"),
        )
        lineage_rows = (
            await self._session.execute(
                lineage_query,
                {
                    "definition_ids": tuple(definitions),
                    "lineage_limit": (
                        max_lineage_events + 1
                        if max_lineage_events is not None
                        else 9_223_372_036_854_775_807
                    ),
                },
            )
        ).all()
        if max_lineage_events is not None and len(lineage_rows) > max_lineage_events:
            raise ValueError("public_mission_activity_lineage_scope_too_large")
        digests_by_definition: dict[UUID, list[str]] = {
            definition_id: [] for definition_id in definitions
        }
        all_digests: set[str] = set()
        for definition_id, digest in lineage_rows:
            digest_text = str(digest)
            digests_by_definition[definition_id].append(digest_text)
            all_digests.add(digest_text)

        accepted_rows = (
            (
                await self._session.execute(
                    select(
                        AcceptedEvent,
                        PublicationReceiptRecord,
                        PublicationIntent,
                        GovernanceDecision,
                    )
                    .outerjoin(
                        PublicationReceiptRecord,
                        PublicationReceiptRecord.receipt_digest == AcceptedEvent.receipt_digest,
                    )
                    .outerjoin(
                        PublicationIntent,
                        PublicationIntent.id == AcceptedEvent.publication_intent_id,
                    )
                    .outerjoin(
                        GovernanceDecision,
                        GovernanceDecision.id == PublicationIntent.reviewed_decision_id,
                    )
                    .where(AcceptedEvent.receipt_digest.in_(tuple(all_digests)))
                    .order_by(AcceptedEvent.published_at, AcceptedEvent.id)
                )
            ).all()
            if all_digests
            else []
        )
        accepted_by_digest = {row[0].receipt_digest: row for row in accepted_rows}

        stored_rows = (
            await self._session.scalars(
                select(StoredMissionProgressRecord).where(
                    StoredMissionProgressRecord.checkpoint_id.in_(tuple(checkpoints))
                )
            )
        ).all()
        stored_by_checkpoint: dict[UUID, list[StoredMissionProgressRecord]] = {
            checkpoint_id: [] for checkpoint_id in checkpoints
        }
        for stored_row in stored_rows:
            stored_by_checkpoint[stored_row.checkpoint_id].append(stored_row)

        results: dict[UUID, bool] = {}
        for definition, checkpoint in items:
            try:
                facts: list[AcceptedMissionFact] = []
                for digest in digests_by_definition[definition.id]:
                    accepted, receipt, intent, decision = accepted_by_digest[digest]
                    facts.append(
                        _accepted_mission_fact(
                            accepted,
                            receipt,
                            intent,
                            decision,
                            target_pack_id=definition.target_pack_id,
                        )
                    )
                projected = project_mission_progress(
                    mission_id=definition.mission_id,
                    definition_id=definition.id,
                    bindings=tuple(bindings_by_definition[definition.id]),
                    accepted_events=tuple(facts),
                )
            except (KeyError, MissionProjectionError, ValueError, TypeError):
                results[checkpoint.id] = False
                continue
            stored = stored_by_checkpoint[checkpoint.id]
            stored_material = {
                (
                    row.repository,
                    row.pack_id,
                    row.record_id,
                    row.accepted_event_id,
                    row.activity_locale,
                    row.activity_pack_version,
                    row.activity_source_digest,
                    row.published_at,
                )
                for row in stored
            }
            projected_material = {
                (
                    row.repository,
                    row.pack_id,
                    row.record_id,
                    row.accepted_event_id,
                    row.activity_locale,
                    row.activity_pack_version,
                    row.activity_source_digest,
                    row.published_at,
                )
                for row in projected.records
            }
            results[checkpoint.id] = (
                checkpoint.accepted_count == projected.accepted_count
                and checkpoint.matched_event_count == projected.matched_event_count
                and checkpoint.event_set_digest == projected.event_set_digest
                and stored_material == projected_material
                and len(stored) == len(stored_material)
            )
        return results

    async def progress_is_current(self, checkpoint: MissionProgressCheckpoint) -> bool:
        """Rebuild and compare the active proof before irreversible completion."""

        try:
            definition = await self.definition(checkpoint.definition_id)
            if definition is None or definition.mission_id != checkpoint.mission_id:
                return False
            inputs = await self.projection_inputs(
                mission_id=checkpoint.mission_id,
                definition_id=checkpoint.definition_id,
                target_pack_id=definition.target_pack_id,
            )
            projected = project_mission_progress(
                mission_id=checkpoint.mission_id,
                definition_id=checkpoint.definition_id,
                bindings=inputs.bindings,
                accepted_events=inputs.accepted_events,
            )
        except (MissionProjectionError, ValueError, TypeError):
            return False

        stored_rows = await self.progress_records(checkpoint.id)
        stored_material = {
            (
                row.repository,
                row.pack_id,
                row.record_id,
                row.accepted_event_id,
                row.activity_locale,
                row.activity_pack_version,
                row.activity_source_digest,
                row.published_at,
            )
            for row in stored_rows
        }
        projected_material = {
            (
                row.repository,
                row.pack_id,
                row.record_id,
                row.accepted_event_id,
                row.activity_locale,
                row.activity_pack_version,
                row.activity_source_digest,
                row.published_at,
            )
            for row in projected.records
        }
        return (
            checkpoint.accepted_count == projected.accepted_count
            and checkpoint.matched_event_count == projected.matched_event_count
            and checkpoint.event_set_digest == projected.event_set_digest
            and stored_material == projected_material
            and len(stored_rows) == len(stored_material)
        )

    async def projection_inputs(
        self,
        *,
        mission_id: UUID,
        definition_id: UUID,
        target_pack_id: str,
    ) -> MissionProjectionInputs:
        """Load and verify the canonical accepted-event lineage for one definition."""

        binding_rows = (
            await self._session.scalars(
                select(MissionContributionBinding).where(
                    MissionContributionBinding.definition_id == definition_id
                )
            )
        ).all()
        bindings = tuple(
            MissionBindingFact(
                mission_id=row.mission_id,
                definition_id=row.definition_id,
                source_draft_id=row.source_draft_id,
                source_draft_version=row.source_draft_version,
            )
            for row in binding_rows
        )
        relevant_digests = tuple(
            str(digest)
            for digest in (
                await self._session.execute(
                    text(
                        """
                        WITH RECURSIVE relevant_receipts(receipt_digest) AS (
                            SELECT accepted.receipt_digest
                            FROM accepted_events AS accepted
                            JOIN publication_intents AS intent
                              ON intent.id = accepted.publication_intent_id
                            JOIN mission_contribution_bindings AS binding
                              ON binding.source_draft_id = intent.source_draft_id
                             AND binding.source_draft_version = intent.source_draft_version
                            WHERE binding.definition_id = CAST(:definition_id AS uuid)
                            UNION
                            SELECT linked.receipt_digest
                            FROM relevant_receipts AS relevant
                            CROSS JOIN LATERAL (
                                SELECT receipt.prior_receipt_digest AS receipt_digest
                                FROM publication_receipts AS receipt
                                WHERE receipt.receipt_digest = relevant.receipt_digest
                                  AND receipt.prior_receipt_digest IS NOT NULL
                                UNION
                                SELECT receipt.receipt_digest
                                FROM publication_receipts AS receipt
                                WHERE receipt.prior_receipt_digest = relevant.receipt_digest
                            ) AS linked
                        )
                        SELECT receipt_digest
                        FROM relevant_receipts
                        ORDER BY receipt_digest
                        """
                    ),
                    {"definition_id": str(definition_id)},
                )
            )
            .scalars()
            .all()
        )
        accepted_rows = (
            await self._session.execute(
                select(
                    AcceptedEvent,
                    PublicationReceiptRecord,
                    PublicationIntent,
                    GovernanceDecision,
                )
                .outerjoin(
                    PublicationReceiptRecord,
                    PublicationReceiptRecord.receipt_digest == AcceptedEvent.receipt_digest,
                )
                .outerjoin(
                    PublicationIntent,
                    PublicationIntent.id == AcceptedEvent.publication_intent_id,
                )
                .outerjoin(
                    GovernanceDecision,
                    GovernanceDecision.id == PublicationIntent.reviewed_decision_id,
                )
                .where(AcceptedEvent.receipt_digest.in_(relevant_digests))
                .order_by(AcceptedEvent.published_at, AcceptedEvent.id)
            )
        ).all()

        accepted_facts = [
            _accepted_mission_fact(
                accepted,
                receipt,
                intent,
                decision,
                target_pack_id=target_pack_id,
            )
            for accepted, receipt, intent, decision in accepted_rows
        ]
        return MissionProjectionInputs(
            bindings=bindings,
            accepted_events=tuple(accepted_facts),
        )

    async def actor_is_active_human_steward(
        self,
        *,
        actor_id: UUID,
        pack_id: str,
        at: datetime,
    ) -> bool:
        role_id = await self._session.scalar(
            text(
                """
                SELECT role.id
                FROM users AS actor
                JOIN governance_role_assignments AS role
                  ON role.actor_id = actor.id
                WHERE actor.id = :actor_id
                  AND actor.actor_kind = 'person'
                  AND actor.login_disabled_at IS NULL
                  AND role.pack_id = :pack_id
                  AND role.role = 'steward'
                  AND role.granted_at <= :at
                  AND (role.revoked_at IS NULL OR role.revoked_at > :at)
                LIMIT 1
                FOR SHARE OF role
                """
            ),
            {"actor_id": actor_id, "pack_id": pack_id, "at": at},
        )
        return role_id is not None

    def add_definition(self, definition: MissionDefinition) -> None:
        self._session.add(definition)

    def add_lifecycle_event(self, event: MissionLifecycleEvent) -> None:
        self._session.add(event)

    def add_contribution_binding(self, binding: MissionContributionBinding) -> None:
        self._session.add(binding)

    def add_progress_checkpoint(self, checkpoint: MissionProgressCheckpoint) -> None:
        self._session.add(checkpoint)

    def add_progress_record(self, record: StoredMissionProgressRecord) -> None:
        self._session.add(record)

    def add_progress_activation(self, activation: MissionProgressActivation) -> None:
        self._session.add(activation)

    async def flush(self) -> None:
        await self._session.flush()


def _accepted_mission_fact(
    accepted: AcceptedEvent,
    receipt: PublicationReceiptRecord | None,
    intent: PublicationIntent | None,
    decision: GovernanceDecision | None,
    *,
    target_pack_id: str,
) -> AcceptedMissionFact:
    if receipt is None:
        raise MissionProjectionError("accepted_receipt_missing")
    envelope_repository, envelope_commit = _receipt_envelope_material(receipt)
    expected_event_type = _accepted_event_type(receipt.event_type)
    if (
        accepted.schema_version != "1.0"
        or receipt.schema_version != "1.0"
        or receipt.receipt_digest != accepted.receipt_digest
        or receipt.publication_intent_id != accepted.publication_intent_id
        or receipt.pack_id != accepted.pack_id
        or accepted.pack_id != target_pack_id
        or receipt.record_id != accepted.record_id
        or expected_event_type != accepted.event_type
        or receipt.published_at != accepted.published_at
        or receipt.reconciled_at < receipt.published_at
        or envelope_repository != accepted.repository
        or envelope_commit != accepted.commit_sha
    ):
        raise MissionProjectionError("accepted_receipt_invalid")
    activity_locale, activity_pack_version, activity_source_digest = _activity_locale_proof(
        accepted,
        receipt,
        intent,
        decision,
    )
    return AcceptedMissionFact(
        event_id=accepted.id,
        receipt_digest=accepted.receipt_digest,
        prior_receipt_digest=receipt.prior_receipt_digest,
        repository=accepted.repository,
        commit_sha=accepted.commit_sha,
        pack_id=accepted.pack_id,
        record_id=accepted.record_id,
        event_type=cast(Literal["publication", "correction", "revocation"], receipt.event_type),
        published_at=accepted.published_at,
        source_draft_id=intent.source_draft_id if intent is not None else UUID(int=0),
        source_draft_version=intent.source_draft_version if intent is not None else 1,
        activity_locale=activity_locale,
        activity_pack_version=activity_pack_version,
        activity_source_digest=activity_source_digest,
    )


def _activity_locale_proof(
    accepted: AcceptedEvent,
    receipt: PublicationReceiptRecord,
    intent: PublicationIntent | None,
    decision: GovernanceDecision | None,
) -> tuple[str | None, str | None, str | None]:
    """Resolve pack locale only from the immutable, receipt-bound approved change set."""

    if intent is None or decision is None or decision.approved_changes_json is None:
        return None, None, None
    body = receipt.envelope_json.get("receipt")
    if not isinstance(body, dict):
        return None, None, None
    try:
        changes = ApprovedChangeSet.from_json(decision.approved_changes_json)
    except (TypeError, ValueError):
        return None, None, None
    if (
        decision.outcome != "approved"
        or intent.reviewed_decision_id != decision.id
        or intent.source_draft_id != decision.source_draft_id
        or intent.source_draft_version != decision.source_draft_version
        or intent.pack_id != decision.pack_id
        or intent.record_id != decision.record_id
        or accepted.pack_id != decision.pack_id
        or accepted.record_id != decision.record_id
        or accepted.repository != decision.forge_target
        or decision.approved_payload_digest != changes.digest
        or intent.approved_payload_digest != changes.digest
        or body.get("reviewed_decision_id") != str(decision.id)
        or body.get("approved_payload_digest") != changes.digest
    ):
        return None, None, None
    manifest_path = f"packs/{changes.pack_id}/pack.yaml"
    manifests = [file.content for file in changes.files if file.path == manifest_path]
    if len(manifests) != 1:
        return None, None, None
    try:
        manifest = parse_pack_manifest(manifests[0])
    except (TypeError, ValueError):
        return None, None, None
    pack_id = manifest.get("id")
    pack_version = manifest.get("version")
    locale = manifest.get("locale")
    if (
        pack_id != changes.pack_id
        or not isinstance(pack_version, str)
        or not _PACK_VERSION.fullmatch(pack_version)
        or not isinstance(locale, str)
        or not _PACK_LOCALE.fullmatch(locale)
    ):
        return None, None, None
    return locale, pack_version, changes.digest


def _accepted_event_type(receipt_event_type: str) -> str:
    try:
        return {
            "publication": "record.published",
            "correction": "record.corrected",
            "revocation": "record.revoked",
        }[receipt_event_type]
    except KeyError as error:
        raise MissionProjectionError("accepted_receipt_event_type_invalid") from error


def _receipt_envelope_material(receipt: PublicationReceiptRecord) -> tuple[str, str]:
    envelope = receipt.envelope_json
    body = envelope.get("receipt")
    if not isinstance(body, dict) or envelope.get("signature_key_id") != receipt.signature_key_id:
        raise MissionProjectionError("accepted_receipt_invalid")
    steps = body.get("verified_steps")
    commit_steps = (
        [step for step in steps if isinstance(step, dict) and step.get("step") == "commit_record"]
        if isinstance(steps, list)
        else []
    )
    published_at = body.get("published_at")
    try:
        envelope_published_at = datetime.fromisoformat(str(published_at).replace("Z", "+00:00"))
    except ValueError as error:
        raise MissionProjectionError("accepted_receipt_invalid") from error
    if (
        len(commit_steps) != 1
        or body.get("schema_version") != receipt.schema_version
        or body.get("publication_id") != str(receipt.publication_id)
        or body.get("event_type") != receipt.event_type
        or body.get("prior_receipt_digest") != receipt.prior_receipt_digest
        or body.get("pack_id") != receipt.pack_id
        or body.get("record_id") != receipt.record_id
        or envelope_published_at != receipt.published_at
        or commit_steps[0].get("external_reference") != body.get("merged_commit")
    ):
        raise MissionProjectionError("accepted_receipt_invalid")
    repository = commit_steps[0].get("destination")
    merged_commit = body.get("merged_commit")
    if not isinstance(repository, str) or not isinstance(merged_commit, str):
        raise MissionProjectionError("accepted_receipt_invalid")
    return repository, merged_commit


__all__ = [
    "MissionActivityLocaleCount",
    "MissionProjectionInputs",
    "MissionRepository",
    "PublicMissionSnapshot",
]
