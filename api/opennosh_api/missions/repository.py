from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.contributions.models import ContributionDraft
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


@dataclass(frozen=True, slots=True)
class MissionProjectionInputs:
    bindings: tuple[MissionBindingFact, ...]
    accepted_events: tuple[AcceptedMissionFact, ...]


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

    async def active_progress(
        self, definition_id: UUID
    ) -> MissionProgressCheckpoint | None:
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

    async def progress_activation(
        self, definition_id: UUID
    ) -> MissionProgressActivation | None:
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
        accepted_rows = (
            await self._session.execute(
                select(AcceptedEvent, PublicationReceiptRecord, PublicationIntent)
                .outerjoin(
                    PublicationReceiptRecord,
                    PublicationReceiptRecord.receipt_digest == AcceptedEvent.receipt_digest,
                )
                .outerjoin(
                    PublicationIntent,
                    PublicationIntent.id == AcceptedEvent.publication_intent_id,
                )
                .order_by(AcceptedEvent.published_at, AcceptedEvent.id)
            )
        ).all()
        binding_sources = {
            (binding.source_draft_id, binding.source_draft_version) for binding in bindings
        }
        relevant_indexes = {
            index
            for index, (_accepted, _receipt, intent) in enumerate(accepted_rows)
            if intent is not None
            and (intent.source_draft_id, intent.source_draft_version) in binding_sources
        }
        relevant_digests = {
            accepted_rows[index][0].receipt_digest for index in relevant_indexes
        }
        changed = True
        while changed:
            changed = False
            digest_indexes = {
                accepted.receipt_digest: index
                for index, (accepted, _receipt, _intent) in enumerate(accepted_rows)
            }
            for index in tuple(relevant_indexes):
                _accepted, receipt, _intent = accepted_rows[index]
                if receipt is None or receipt.prior_receipt_digest is None:
                    continue
                parent_index = digest_indexes.get(receipt.prior_receipt_digest)
                if parent_index is not None and parent_index not in relevant_indexes:
                    relevant_indexes.add(parent_index)
                    relevant_digests.add(accepted_rows[parent_index][0].receipt_digest)
                    changed = True
            for index, (_accepted, receipt, _intent) in enumerate(accepted_rows):
                if (
                    index not in relevant_indexes
                    and receipt is not None
                    and receipt.prior_receipt_digest in relevant_digests
                ):
                    relevant_indexes.add(index)
                    relevant_digests.add(receipt.receipt_digest)
                    changed = True

        accepted_facts: list[AcceptedMissionFact] = []
        for index in sorted(relevant_indexes):
            accepted, receipt, intent = accepted_rows[index]
            if receipt is None:
                raise MissionProjectionError("accepted_receipt_missing")
            envelope_receipt = receipt.envelope_json.get("receipt")
            expected_event_type = _accepted_event_type(receipt.event_type)
            if not isinstance(envelope_receipt, dict) or (
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
                or envelope_receipt.get("merged_commit") != accepted.commit_sha
            ):
                raise MissionProjectionError("accepted_receipt_invalid")
            accepted_facts.append(
                AcceptedMissionFact(
                    event_id=accepted.id,
                    receipt_digest=accepted.receipt_digest,
                    prior_receipt_digest=receipt.prior_receipt_digest,
                    repository=accepted.repository,
                    commit_sha=accepted.commit_sha,
                    pack_id=accepted.pack_id,
                    record_id=accepted.record_id,
                    event_type=receipt.event_type,
                    published_at=accepted.published_at,
                    source_draft_id=(intent.source_draft_id if intent is not None else UUID(int=0)),
                    source_draft_version=(
                        intent.source_draft_version if intent is not None else 1
                    ),
                )
            )
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
        return bool(
            await self._session.scalar(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
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
                    )
                    """
                ),
                {"actor_id": actor_id, "pack_id": pack_id, "at": at},
            )
        )

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


def _accepted_event_type(receipt_event_type: str) -> str:
    try:
        return {
            "publication": "record.published",
            "correction": "record.corrected",
            "revocation": "record.revoked",
        }[receipt_event_type]
    except KeyError as error:
        raise MissionProjectionError("accepted_receipt_event_type_invalid") from error


__all__ = ["MissionProjectionInputs", "MissionRepository"]
