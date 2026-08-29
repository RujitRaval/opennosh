from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import asyncpg  # type: ignore[import-untyped]
from pgqueuer.db import AsyncpgDriver

from opennosh_api.federation.repository import federation_scope_allows_claim
from opennosh_api.jobs import JobLane, JobMessage
from opennosh_api.jobs.pgqueuer import (
    PUBLICATION_ENTRYPOINT,
    build_queries,
    decode_message,
    encode_message,
)
from opennosh_api.publication.reducer import AcceptedEventData, PublicationReduction
from opennosh_api.publication.state import (
    DurableAcknowledgementSnapshot,
    EffectIntent,
    ExternalObservation,
    PublicationSnapshot,
    PublicationState,
    PublicationStepName,
    PublicationStepSnapshot,
    PublicationStepState,
    publication_protocol,
)

DEFAULT_LEASE_DURATION = timedelta(seconds=60)


class PublicationLeaseLostError(RuntimeError):
    pass


class PublicationAcknowledgementConflictError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PublicationLease:
    token: UUID
    queue_job_id: int
    snapshot: PublicationSnapshot
    effect: EffectIntent


class PostgresPublicationRepository:
    """CAS repository using the publication worker's existing asyncpg pool."""

    def __init__(
        self,
        pool: Any,
        *,
        lease_duration: timedelta = DEFAULT_LEASE_DURATION,
    ) -> None:
        if lease_duration <= timedelta():
            raise ValueError("Publication lease duration must be positive")
        self._pool = pool
        self._lease_duration = lease_duration

    async def load_or_initialize(
        self,
        publication_id: UUID,
    ) -> PublicationSnapshot:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                intent = await connection.fetchrow(
                    """
                    SELECT id, forge_target
                    FROM publication_intents
                    WHERE id = $1
                    FOR UPDATE
                    """,
                    publication_id,
                )
                if intent is None:
                    raise LookupError(f"Unknown publication intent: {publication_id}")
                protocol = publication_protocol(str(intent["forge_target"]))
                await connection.executemany(
                    """
                    INSERT INTO publication_steps (
                        publication_intent_id,
                        workflow_version,
                        step_name,
                        ordinal,
                        destination,
                        step_version,
                        state
                    )
                    VALUES ($1, '1.0', $2, $3, $4, 1, 'pending')
                    ON CONFLICT (publication_intent_id, ordinal) DO NOTHING
                    """,
                    [
                        (
                            publication_id,
                            definition.name.value,
                            definition.ordinal,
                            definition.destination,
                        )
                        for definition in protocol
                    ],
                )
                return await self._load_snapshot(connection, publication_id)

    async def claim(
        self,
        effect: EffectIntent,
        *,
        queue_job_id: int,
        owner: str,
        now: datetime,
        token: UUID | None = None,
    ) -> PublicationLease | None:
        _require_aware(now)
        lease_token = token or uuid4()
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                intent = await connection.fetchrow(
                    """
                    SELECT workflow_revision, state, pack_id, forge_target
                    FROM publication_intents
                    WHERE id = $1
                    FOR UPDATE
                    """,
                    effect.publication_id,
                )
                if intent is None:
                    raise LookupError(f"Unknown publication intent: {effect.publication_id}")
                if int(intent["workflow_revision"]) != effect.workflow_revision:
                    return None
                if str(intent["state"]) in {
                    PublicationState.BLOCKED.value,
                    PublicationState.FAILED.value,
                    PublicationState.PUBLISHED.value,
                    PublicationState.PUBLISH_BLOCKED.value,
                    PublicationState.QUARANTINED.value,
                }:
                    return None
                forge_target = str(intent["forge_target"])
                if forge_target.startswith("github:") and not await federation_scope_allows_claim(
                    connection,
                    repository=forge_target.removeprefix("github:"),
                    pack_id=str(intent["pack_id"]),
                ):
                    await connection.execute(
                        """
                        UPDATE publication_intents
                        SET state = 'publish_blocked',
                            last_failure_code = 'federation_scope_not_active',
                            updated_at = $2
                        WHERE id = $1
                        """,
                        effect.publication_id,
                        now,
                    )
                    return None

                step = await connection.fetchrow(
                    """
                    SELECT id, state, lease_expires_at
                    FROM publication_steps
                    WHERE publication_intent_id = $1
                      AND step_name = $2
                      AND destination = $3
                    FOR UPDATE
                    """,
                    effect.publication_id,
                    effect.step.value,
                    effect.destination,
                )
                if step is None:
                    raise RuntimeError("Planned publication step is not persisted")
                if str(step["state"]) == PublicationStepState.VERIFIED.value:
                    return None
                lease_expires_at = step["lease_expires_at"]
                if (
                    str(step["state"]) == PublicationStepState.LEASED.value
                    and lease_expires_at is not None
                    and lease_expires_at > now
                ):
                    return None

                expires_at = now + self._lease_duration
                updated = await connection.fetchval(
                    """
                    UPDATE publication_steps
                    SET state = 'leased',
                        attempt_count = attempt_count + 1,
                        queue_job_id = $2,
                        lease_token = $3,
                        lease_owner = $4,
                        lease_expires_at = $5,
                        input_digest = $6,
                        updated_at = $7
                    WHERE id = $1
                    RETURNING id
                    """,
                    step["id"],
                    queue_job_id,
                    lease_token,
                    owner,
                    expires_at,
                    effect.idempotency_key,
                    now,
                )
                if updated is None:
                    return None
                await connection.execute(
                    """
                    UPDATE publication_intents
                    SET state = CASE
                            WHEN state IN ('pending', 'retrying', 'publish_retrying')
                            THEN 'running'
                            ELSE state
                        END,
                        workflow_revision = workflow_revision + 1,
                        attempt_count = attempt_count + 1,
                        updated_at = $2
                    WHERE id = $1
                    """,
                    effect.publication_id,
                    now,
                )
                snapshot = await self._load_snapshot(connection, effect.publication_id)
                return PublicationLease(
                    token=lease_token,
                    queue_job_id=queue_job_id,
                    snapshot=snapshot,
                    effect=effect,
                )

    async def apply(
        self,
        lease: PublicationLease,
        reduction: PublicationReduction,
        *,
        now: datetime,
    ) -> None:
        _require_aware(now)
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                revision = await connection.fetchval(
                    """
                    SELECT workflow_revision
                    FROM publication_intents
                    WHERE id = $1
                    FOR UPDATE
                    """,
                    lease.effect.publication_id,
                )
                if revision is None or int(revision) != reduction.expected_revision:
                    raise PublicationLeaseLostError("Publication revision changed before reduction")

                if reduction.step is not None:
                    step_id = await connection.fetchval(
                        """
                        SELECT id
                        FROM publication_steps
                        WHERE publication_intent_id = $1
                          AND step_name = $2
                          AND destination = $3
                          AND lease_token = $4
                          AND lease_expires_at > $5
                        FOR UPDATE
                        """,
                        lease.effect.publication_id,
                        reduction.step.value,
                        reduction.destination,
                        lease.token,
                        now,
                    )
                    if step_id is None:
                        raise PublicationLeaseLostError("Publication step lease expired or changed")
                    if reduction.acknowledgement is not None:
                        await self._insert_or_compare_acknowledgement(
                            connection,
                            lease.effect.publication_id,
                            reduction.acknowledgement,
                        )
                    await connection.execute(
                        """
                        UPDATE publication_steps
                        SET state = $2::varchar,
                            lease_token = NULL,
                            lease_owner = NULL,
                            lease_expires_at = NULL,
                            observation_json = $3::jsonb,
                            failure_code = $4,
                            failure_context_json = $5::jsonb,
                            next_attempt_at = COALESCE($6, next_attempt_at),
                            verified_at = CASE
                                WHEN $2::varchar = 'verified' THEN $7
                                ELSE verified_at
                            END,
                            updated_at = $7
                        WHERE id = $1
                        """,
                        step_id,
                        reduction.step_state.value if reduction.step_state is not None else None,
                        json.dumps(_observation_payload(reduction.observation)),
                        reduction.failure_code,
                        json.dumps(dict(reduction.failure_context)),
                        reduction.next_wake_at,
                        now,
                    )

                await connection.execute(
                    """
                    UPDATE publication_intents
                    SET state = $2::varchar,
                        workflow_revision = $3,
                        next_attempt_at = COALESCE($4, next_attempt_at),
                        last_failure_code = $5,
                        last_failure_context_json = $6::jsonb,
                        published_at = CASE
                            WHEN $2::varchar = 'published' THEN $7
                            ELSE published_at
                        END,
                        updated_at = $7
                    WHERE id = $1
                    """,
                    lease.effect.publication_id,
                    reduction.publication_state.value,
                    reduction.next_revision,
                    reduction.next_wake_at,
                    reduction.failure_code,
                    json.dumps(dict(reduction.failure_context)),
                    now,
                )
                if reduction.accepted_event is not None:
                    await self._insert_or_compare_receipt(
                        connection,
                        lease.effect.publication_id,
                        reduction.accepted_event,
                        reconciled_at=now,
                    )
                    await self._insert_or_compare_accepted_event(
                        connection,
                        lease.effect.publication_id,
                        reduction.accepted_event,
                    )
                if reduction.next_wake_at is not None:
                    await self._enqueue_revision_wakeup(
                        connection,
                        publication_id=lease.effect.publication_id,
                        revision=reduction.next_revision,
                        run_after=reduction.next_wake_at,
                        now=now,
                    )

    async def apply_unleased(
        self,
        publication_id: UUID,
        reduction: PublicationReduction,
        *,
        now: datetime,
    ) -> None:
        if reduction.step is not None or reduction.accepted_event is None:
            raise ValueError("Lease-free reduction is reserved for final receipt acceptance")
        _require_aware(now)
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                revision = await connection.fetchval(
                    """
                    SELECT workflow_revision
                    FROM publication_intents
                    WHERE id = $1
                    FOR UPDATE
                    """,
                    publication_id,
                )
                if revision is None or int(revision) != reduction.expected_revision:
                    raise PublicationLeaseLostError(
                        "Publication revision changed before final acceptance"
                    )
                proof = reduction.accepted_event
                await connection.execute(
                    """
                    UPDATE publication_intents
                    SET state = 'published',
                        workflow_revision = $2,
                        published_at = $3,
                        updated_at = $4
                    WHERE id = $1
                    """,
                    publication_id,
                    reduction.next_revision,
                    proof.published_at,
                    now,
                )
                await self._insert_or_compare_receipt(
                    connection,
                    publication_id,
                    proof,
                    reconciled_at=now,
                )
                await self._insert_or_compare_accepted_event(
                    connection,
                    publication_id,
                    proof,
                )

    async def _insert_or_compare_acknowledgement(
        self,
        connection: asyncpg.Connection,
        publication_id: UUID,
        acknowledgement: DurableAcknowledgementSnapshot,
    ) -> None:
        await connection.execute(
            """
            INSERT INTO publication_durable_acknowledgements (
                publication_intent_id,
                acknowledgement_kind,
                destination,
                content_digest,
                external_reference,
                context_json,
                verified_at
            )
            VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
            ON CONFLICT (
                publication_intent_id,
                acknowledgement_kind,
                destination
            ) DO NOTHING
            """,
            publication_id,
            acknowledgement.step.value,
            acknowledgement.destination,
            acknowledgement.content_digest,
            acknowledgement.external_reference,
            json.dumps(dict(acknowledgement.context)),
            acknowledgement.verified_at,
        )
        existing = await connection.fetchrow(
            """
            SELECT content_digest, external_reference, context_json
            FROM publication_durable_acknowledgements
            WHERE publication_intent_id = $1
              AND acknowledgement_kind = $2
              AND destination = $3
            """,
            publication_id,
            acknowledgement.step.value,
            acknowledgement.destination,
        )
        if existing is None:
            raise RuntimeError("Durable acknowledgement insert was not visible")
        if (
            str(existing["content_digest"]) != acknowledgement.content_digest
            or existing["external_reference"] != acknowledgement.external_reference
            or _json_object(existing["context_json"]) != dict(acknowledgement.context)
        ):
            raise PublicationAcknowledgementConflictError(
                "Durable acknowledgement already exists with different proof"
            )

    async def _insert_or_compare_receipt(
        self,
        connection: asyncpg.Connection,
        publication_id: UUID,
        proof: AcceptedEventData,
        *,
        reconciled_at: datetime,
    ) -> None:
        receipt = proof.envelope.get("receipt")
        if not isinstance(receipt, dict):
            raise ValueError("Signed receipt projection lacks its receipt body")
        await connection.execute(
            """
            INSERT INTO publication_receipts (
                publication_intent_id,
                publication_id,
                schema_version,
                receipt_digest,
                event_type,
                prior_receipt_digest,
                pack_id,
                record_id,
                envelope_json,
                signature_key_id,
                registry_reference,
                artifact_reference,
                published_at,
                reconciled_at
            )
            SELECT id, $1, '1.0', $2, $3, $4, pack_id, record_id,
                   $5::jsonb, $6, $7, $8, $9, $10
            FROM publication_intents
            WHERE id = $1
            ON CONFLICT DO NOTHING
            """,
            publication_id,
            proof.receipt_digest,
            proof.event_type,
            proof.prior_receipt_digest,
            json.dumps(dict(proof.envelope)),
            str(proof.envelope["signature_key_id"]),
            proof.registry_reference,
            proof.artifact_reference,
            proof.published_at,
            reconciled_at,
        )
        rows = await connection.fetch(
            """
            SELECT publication_id, receipt_digest, event_type, prior_receipt_digest,
                   envelope_json, registry_reference, artifact_reference, published_at
            FROM publication_receipts
            WHERE receipt_digest = $1 OR publication_id = $2
            """,
            proof.receipt_digest,
            publication_id,
        )
        if len(rows) != 1:
            raise PublicationAcknowledgementConflictError("Publication receipt identities conflict")
        existing = rows[0]
        if (
            existing["publication_id"] != publication_id
            or str(existing["receipt_digest"]) != proof.receipt_digest
            or str(existing["event_type"]) != proof.event_type
            or existing["prior_receipt_digest"] != proof.prior_receipt_digest
            or _json_object(existing["envelope_json"]) != dict(proof.envelope)
            or str(existing["registry_reference"]) != proof.registry_reference
            or str(existing["artifact_reference"]) != proof.artifact_reference
            or existing["published_at"] != proof.published_at
        ):
            raise PublicationAcknowledgementConflictError(
                "Publication receipt already exists with different canonical proof"
            )

    async def _insert_or_compare_accepted_event(
        self,
        connection: asyncpg.Connection,
        publication_id: UUID,
        proof: AcceptedEventData,
    ) -> None:
        await connection.execute(
            """
            INSERT INTO accepted_events (
                publication_intent_id,
                repository,
                commit_sha,
                pack_id,
                record_id,
                event_type,
                receipt_digest,
                published_at
            )
            SELECT id, $2, $3, pack_id, record_id, $4, $5, $6
            FROM publication_intents
            WHERE id = $1
            ON CONFLICT DO NOTHING
            """,
            publication_id,
            proof.repository,
            proof.commit_sha,
            _accepted_event_type(proof.event_type),
            proof.receipt_digest,
            proof.published_at,
        )
        rows = await connection.fetch(
            """
            SELECT publication_intent_id, repository, commit_sha, pack_id, record_id,
                   event_type, receipt_digest, published_at
            FROM accepted_events
            WHERE receipt_digest = $1
               OR (repository = $2 AND commit_sha = $3
                   AND pack_id = (
                       SELECT pack_id FROM publication_intents WHERE id = $4
                   )
                   AND record_id = (
                       SELECT record_id FROM publication_intents WHERE id = $4
                   ))
            """,
            proof.receipt_digest,
            proof.repository,
            proof.commit_sha,
            publication_id,
        )
        if len(rows) != 1:
            raise PublicationAcknowledgementConflictError("Accepted event identities conflict")
        existing = rows[0]
        intent = await connection.fetchrow(
            "SELECT pack_id, record_id FROM publication_intents WHERE id = $1",
            publication_id,
        )
        if intent is None:
            raise RuntimeError("Publication intent disappeared during accepted-event insert")
        if (
            existing["publication_intent_id"] != publication_id
            or str(existing["repository"]) != proof.repository
            or str(existing["commit_sha"]) != proof.commit_sha
            or str(existing["pack_id"]) != str(intent["pack_id"])
            or str(existing["record_id"]) != str(intent["record_id"])
            or str(existing["event_type"]) != _accepted_event_type(proof.event_type)
            or str(existing["receipt_digest"]) != proof.receipt_digest
            or existing["published_at"] != proof.published_at
        ):
            raise PublicationAcknowledgementConflictError(
                "Accepted event already exists with different receipt proof"
            )

    async def _enqueue_revision_wakeup(
        self,
        connection: asyncpg.Connection,
        *,
        publication_id: UUID,
        revision: int,
        run_after: datetime,
        now: datetime,
    ) -> None:
        key = f"publication:{publication_id}:revision:{revision}"
        message = JobMessage(
            lane=JobLane.PUBLICATION,
            job_type="publication.wake",
            subject_id=publication_id,
            idempotency_key=key,
            workflow_revision=revision,
        )
        delay = max(run_after - now, timedelta())
        queries = build_queries(AsyncpgDriver(connection))
        (job_id,) = await queries.enqueue(
            PUBLICATION_ENTRYPOINT,
            encode_message(message),
            execute_after=delay,
            dedupe_key=key,
            on_conflict="skip",
        )
        if job_id is not None:
            return
        existing_payload = await connection.fetchval(
            """
            SELECT payload
            FROM opennosh_pgqueuer
            WHERE dedupe_key = $1
              AND status IN ('queued', 'picked')
            """,
            key,
        )
        if existing_payload is None:
            raise RuntimeError("Revision wake-up was neither enqueued nor already present")
        existing_message = decode_message(bytes(existing_payload))
        if (
            existing_message.subject_id != publication_id
            or existing_message.workflow_revision != revision
        ):
            raise RuntimeError("Revision wake-up dedupe key is bound to another message")

    async def _load_snapshot(
        self,
        connection: asyncpg.Connection,
        publication_id: UUID,
    ) -> PublicationSnapshot:
        intent = await connection.fetchrow(
            """
            SELECT id, workflow_version, workflow_revision, state,
                   source_draft_id, source_draft_version, reviewed_decision_id,
                   approving_actor_id, pack_id, record_id, approved_payload_digest,
                   expected_base_commit, required_checks_json, forge_target,
                   idempotency_key_hash, event_type, prior_receipt_digest,
                   evidence_manifest_digests_json, evidence_acknowledgements_json
            FROM publication_intents
            WHERE id = $1
            """,
            publication_id,
        )
        if intent is None:
            raise LookupError(f"Unknown publication intent: {publication_id}")
        step_rows = await connection.fetch(
            """
            SELECT step_name, ordinal, destination, state, step_version, attempt_count,
                   queue_job_id, lease_token, lease_expires_at, next_attempt_at
            FROM publication_steps
            WHERE publication_intent_id = $1
            ORDER BY ordinal
            """,
            publication_id,
        )
        acknowledgement_rows = await connection.fetch(
            """
            SELECT acknowledgement_kind, destination, content_digest, external_reference,
                   context_json, verified_at
            FROM publication_durable_acknowledgements
            WHERE publication_intent_id = $1
            ORDER BY acknowledgement_kind, destination
            """,
            publication_id,
        )
        return PublicationSnapshot(
            publication_id=publication_id,
            workflow_version=str(intent["workflow_version"]),
            workflow_revision=int(intent["workflow_revision"]),
            state=PublicationState(str(intent["state"])),
            source_draft_id=intent["source_draft_id"],
            source_draft_version=int(intent["source_draft_version"]),
            reviewed_decision_id=intent["reviewed_decision_id"],
            approving_actor_id=intent["approving_actor_id"],
            pack_id=str(intent["pack_id"]),
            record_id=str(intent["record_id"]),
            approved_payload_digest=str(intent["approved_payload_digest"]),
            expected_base_commit=str(intent["expected_base_commit"]),
            required_checks=tuple(
                str(value) for value in _json_array(intent["required_checks_json"])
            ),
            forge_target=str(intent["forge_target"]),
            idempotency_key_hash=str(intent["idempotency_key_hash"]),
            event_type=str(intent["event_type"]),
            prior_receipt_digest=(
                str(intent["prior_receipt_digest"])
                if intent["prior_receipt_digest"] is not None
                else None
            ),
            evidence_manifest_digests=tuple(
                str(value) for value in _json_array(intent["evidence_manifest_digests_json"])
            ),
            evidence_acknowledgements=tuple(
                _json_object(value)
                for value in _json_array(intent["evidence_acknowledgements_json"])
            ),
            steps=tuple(
                PublicationStepSnapshot(
                    name=PublicationStepName(str(row["step_name"])),
                    ordinal=int(row["ordinal"]),
                    destination=str(row["destination"]),
                    state=PublicationStepState(str(row["state"])),
                    step_version=int(row["step_version"]),
                    attempt_count=int(row["attempt_count"]),
                    queue_job_id=(
                        int(row["queue_job_id"]) if row["queue_job_id"] is not None else None
                    ),
                    lease_token=row["lease_token"],
                    lease_expires_at=row["lease_expires_at"],
                    next_attempt_at=row["next_attempt_at"],
                )
                for row in step_rows
            ),
            acknowledgements=tuple(
                DurableAcknowledgementSnapshot(
                    step=PublicationStepName(str(row["acknowledgement_kind"])),
                    destination=str(row["destination"]),
                    content_digest=str(row["content_digest"]),
                    external_reference=(
                        str(row["external_reference"])
                        if row["external_reference"] is not None
                        else None
                    ),
                    verified_at=row["verified_at"],
                    context=_json_object(row["context_json"]),
                )
                for row in acknowledgement_rows
            ),
        )


def _observation_payload(observation: ExternalObservation | None) -> dict[str, object]:
    if observation is None:
        return {}
    return {
        "schema_version": "1.0",
        "step": observation.step.value,
        "status": observation.status.value,
        "observed_at": observation.observed_at.isoformat(),
        "destination": observation.destination,
        "effect_idempotency_key": observation.effect_idempotency_key,
        "adapter_identity": observation.adapter_identity,
        "adapter_version": observation.adapter_version,
        "content_digest": observation.content_digest,
        "external_reference": observation.external_reference,
        "retry_at": observation.retry_at.isoformat() if observation.retry_at is not None else None,
        "code": observation.code,
        "context": dict(observation.context),
    }


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Publication repository time must include a timezone")


def _json_object(value: object) -> dict[str, object]:
    parsed: object = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, dict):
        raise ValueError("Publication JSON value must be an object")
    return {str(key): item for key, item in parsed.items()}


def _json_array(value: object) -> list[object]:
    parsed: object = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, list):
        raise ValueError("Publication JSON value must be an array")
    return list(parsed)


def _accepted_event_type(receipt_event_type: str) -> str:
    values = {
        "publication": "record.published",
        "correction": "record.corrected",
        "revocation": "record.revoked",
    }
    try:
        return values[receipt_event_type]
    except KeyError as error:
        raise ValueError("Receipt event type is unsupported") from error
