from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from opennosh_api.governance.contracts import ApprovedChangeSet
from opennosh_api.governance.policy import GovernanceBinding


class GovernanceGate(Protocol):
    async def binding_for(self, publication_id: UUID) -> GovernanceBinding: ...

    async def authorize_merge(
        self,
        publication_id: UUID,
        *,
        head_commit: str,
        expected_payload_digest: str,
        now: datetime,
    ) -> GovernanceBinding: ...


class PostgresGovernanceGate:
    """Loads immutable approval evidence through the publication worker's pool."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def binding_for(self, publication_id: UUID) -> GovernanceBinding:
        async with self._pool.acquire() as connection:
            return await self._binding_for(connection, publication_id)

    async def authorize_merge(
        self,
        publication_id: UUID,
        *,
        head_commit: str,
        expected_payload_digest: str,
        now: datetime,
    ) -> GovernanceBinding:
        """Commit irreversible merge authority in one short database transaction."""

        _validate_hash(head_commit, lengths={40, 64}, label="head")
        _validate_hash(expected_payload_digest, lengths={64}, label="payload digest")
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                pack_id = await connection.fetchval(
                    """
                    SELECT d.pack_id
                    FROM publication_intents p
                    JOIN governance_decisions d ON d.id = p.reviewed_decision_id
                    WHERE p.id = $1
                    """,
                    publication_id,
                )
                if pack_id is None:
                    raise LookupError(f"Unknown governed publication: {publication_id}")
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended("
                    "'opennosh.governance-pack:' || $1::text, 0))",
                    pack_id,
                )
                await self._require_current_evidence(connection, publication_id)
                binding = await self._binding_for(connection, publication_id)
                if binding.approved_changes.digest != expected_payload_digest:
                    raise ValueError("Merge authorization payload digest changed")
                if binding.merge_authorized_at is not None:
                    if binding.merge_authorized_head_commit != head_commit:
                        raise ValueError("Merge authorization head changed")
                    return binding
                binding.authorize_at(now)
                await connection.execute(
                    """
                    INSERT INTO governance_merge_authorizations (
                        publication_intent_id,
                        decision_id,
                        pack_id,
                        head_commit,
                        approved_payload_digest,
                        authorized_at
                    ) VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                    publication_id,
                    binding.decision_id,
                    binding.pack_id,
                    head_commit,
                    expected_payload_digest,
                    now,
                )
                return replace(
                    binding,
                    merge_authorized_at=now,
                    merge_authorized_head_commit=head_commit,
                    merge_authorized_payload_digest=expected_payload_digest,
                )

    @staticmethod
    async def _require_current_evidence(connection: Any, publication_id: UUID) -> None:
        """Serialize merge authorization with governed evidence removal."""

        row = await connection.fetchrow(
            """
            SELECT em.public_state, t.evidence_id AS tombstone_id
            FROM publication_intents p
            JOIN evidence_manifests em
              ON em.source_draft_id = p.source_draft_id
             AND em.source_draft_version = p.source_draft_version
            LEFT JOIN evidence_removal_tombstones t ON t.evidence_id = em.id
            WHERE p.id = $1
            FOR SHARE OF em
            """,
            publication_id,
        )
        if row is None:
            raise ValueError("Merge authorization requires exact-version evidence")
        if row["tombstone_id"] is not None or row["public_state"] in {None, "tombstoned"}:
            raise ValueError("Merge authorization requires current durable evidence")

    @staticmethod
    async def _binding_for(connection: Any, publication_id: UUID) -> GovernanceBinding:
        row = await connection.fetchrow(
                """
                SELECT p.id AS publication_id,
                       d.id AS decision_id,
                       d.pack_id,
                       d.contributor_actor_id,
                       d.deciding_actor_id,
                       d.decided_at,
                       d.approved_changes_json,
                       d.expected_base_commit,
                       d.required_checks_json,
                       d.forge_target,
                       r.granted_at,
                       r.revoked_at,
                       rec.recused_at,
                       intervention.action AS intervention_action,
                       intervention.intervened_at,
                       merge_auth.authorized_at AS merge_authorized_at,
                       merge_auth.head_commit AS merge_authorized_head_commit,
                       merge_auth.approved_payload_digest
                           AS merge_authorized_payload_digest
                FROM publication_intents p
                JOIN governance_decisions d ON d.id = p.reviewed_decision_id
                JOIN governance_role_assignments r
                  ON r.pack_id = d.pack_id
                 AND r.actor_id = d.deciding_actor_id
                 AND r.role = 'steward'
                LEFT JOIN governance_recusals rec
                  ON rec.source_draft_id = d.source_draft_id
                 AND rec.actor_id = d.deciding_actor_id
                LEFT JOIN governance_publication_interventions intervention
                  ON intervention.publication_intent_id = p.id
                LEFT JOIN governance_merge_authorizations merge_auth
                  ON merge_auth.publication_intent_id = p.id
                WHERE p.id = $1
                """,
            publication_id,
        )
        if row is None:
            raise LookupError(f"Unknown governed publication: {publication_id}")
        pauses = await connection.fetch(
            """
            SELECT paused_at, resumed_at
            FROM governance_publication_pauses
            WHERE pack_id = $1
            ORDER BY paused_at, id
            """,
            row["pack_id"],
        )
        raw_changes = row["approved_changes_json"]
        if isinstance(raw_changes, str):
            raw_changes = json.loads(raw_changes)
        raw_checks = row["required_checks_json"]
        if isinstance(raw_checks, str):
            raw_checks = json.loads(raw_checks)
        return GovernanceBinding(
            publication_id=row["publication_id"],
            decision_id=row["decision_id"],
            pack_id=str(row["pack_id"]),
            contributor_actor_id=row["contributor_actor_id"],
            approving_actor_id=row["deciding_actor_id"],
            approved_at=row["decided_at"],
            approved_changes=ApprovedChangeSet.from_json(raw_changes),
            expected_base_commit=str(row["expected_base_commit"]),
            required_checks=tuple(str(item) for item in raw_checks),
            forge_target=str(row["forge_target"]),
            role_granted_at=row["granted_at"],
            role_revoked_at=row["revoked_at"],
            recused_at=row["recused_at"],
            intervention_action=row["intervention_action"],
            intervened_at=row["intervened_at"],
            pause_intervals=tuple((pause["paused_at"], pause["resumed_at"]) for pause in pauses),
            merge_authorized_at=row["merge_authorized_at"],
            merge_authorized_head_commit=row["merge_authorized_head_commit"],
            merge_authorized_payload_digest=row["merge_authorized_payload_digest"],
        )


def _validate_hash(value: str, *, lengths: set[int], label: str) -> None:
    if len(value) not in lengths or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError(
            f"Merge authorization {label} must be bounded lowercase hexadecimal"
        )
