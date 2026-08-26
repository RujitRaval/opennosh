from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from opennosh_api.evidence.contracts import EvidenceAcknowledgement
from opennosh_api.publication.receipts import (
    PublicationReceiptKeyRing,
    PublicationReceiptStore,
    ReceiptEventType,
    ReceiptVerificationError,
    SignedPublicationReceipt,
    parse_signed_receipt,
    signed_receipt_digest,
)
from opennosh_api.publication.state import PublicationStepName, publication_protocol


@dataclass(frozen=True, slots=True)
class ReceiptReconciliationIssue:
    object_key: str
    code: str


@dataclass(frozen=True, slots=True)
class ReceiptReconciliationResult:
    reconstructed: int
    already_current: int
    pending: tuple[ReceiptReconciliationIssue, ...]
    quarantined: tuple[ReceiptReconciliationIssue, ...]


@dataclass(frozen=True, slots=True)
class _VerifiedExternalReceipt:
    object_key: str
    envelope: SignedPublicationReceipt
    digest: str
    registry_reference: str
    artifact_reference: str


class PublicationReceiptReconciler:
    """Rebuild receipt-backed PostgreSQL projections without repeating effects.

    Registry receipts + immutable artifacts
                  | exact canonical bytes + trusted signature
                  v
       publication_receipts (append-only projection)
                  |
             +----+--------------------+
             v                         v
      publication ledger        accepted_events
      steps + acknowledgements   activity/search input
    """

    def __init__(
        self,
        pool: Any,
        *,
        registry: PublicationReceiptStore,
        artifacts: PublicationReceiptStore,
        key_ring: PublicationReceiptKeyRing,
    ) -> None:
        if registry.destination == artifacts.destination:
            raise ValueError("Receipt reconciliation requires independent destinations")
        self._pool = pool
        self._registry = registry
        self._artifacts = artifacts
        self._key_ring = key_ring

    async def run(self, *, now: datetime | None = None) -> ReceiptReconciliationResult:
        observed_at = now or datetime.now(UTC)
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("Receipt reconciliation time must include a timezone")
        registry_keys = set(await self._registry.list_keys())
        artifact_keys = set(await self._artifacts.list_keys())
        pending: list[ReceiptReconciliationIssue] = []
        quarantined: list[ReceiptReconciliationIssue] = []
        candidates: list[_VerifiedExternalReceipt] = []
        for object_key in sorted(registry_keys | artifact_keys):
            if object_key not in registry_keys or object_key not in artifact_keys:
                pending.append(
                    ReceiptReconciliationIssue(
                        object_key=object_key,
                        code="receipt_destination_acknowledgement_missing",
                    )
                )
                continue
            candidate = await self._verify_external_pair(object_key)
            if isinstance(candidate, ReceiptReconciliationIssue):
                quarantined.append(candidate)
            else:
                candidates.append(candidate)

        candidates.sort(
            key=lambda item: (
                item.envelope.receipt.published_at,
                item.digest,
            )
        )
        reconstructed = 0
        already_current = 0
        remaining = candidates
        while remaining:
            deferred: list[_VerifiedExternalReceipt] = []
            progressed = False
            for candidate in remaining:
                try:
                    outcome = await self._project(candidate, now=observed_at)
                except ReceiptVerificationError as error:
                    quarantined.append(
                        ReceiptReconciliationIssue(
                            object_key=candidate.object_key,
                            code=str(error),
                        )
                    )
                    progressed = True
                    continue
                if outcome == "lineage_pending":
                    deferred.append(candidate)
                elif outcome == "reconstructed":
                    reconstructed += 1
                    progressed = True
                else:
                    already_current += 1
                    progressed = True
            if not deferred:
                break
            if not progressed:
                pending.extend(
                    ReceiptReconciliationIssue(
                        object_key=item.object_key,
                        code="prior_receipt_not_available",
                    )
                    for item in deferred
                )
                break
            remaining = deferred
        return ReceiptReconciliationResult(
            reconstructed=reconstructed,
            already_current=already_current,
            pending=tuple(pending),
            quarantined=tuple(quarantined),
        )

    async def _verify_external_pair(
        self, object_key: str
    ) -> _VerifiedExternalReceipt | ReceiptReconciliationIssue:
        registry_payload = await self._registry.read(object_key)
        artifact_payload = await self._artifacts.read(object_key)
        if registry_payload is None or artifact_payload is None:
            return ReceiptReconciliationIssue(
                object_key=object_key,
                code="receipt_disappeared_after_enumeration",
            )
        if registry_payload != artifact_payload:
            return ReceiptReconciliationIssue(
                object_key=object_key,
                code="receipt_destination_digest_conflict",
            )
        try:
            envelope = parse_signed_receipt(registry_payload)
            self._key_ring.verify(envelope)
        except ReceiptVerificationError as error:
            return ReceiptReconciliationIssue(object_key=object_key, code=str(error))
        expected_key = f"receipts/v1/{envelope.receipt.publication_id}.json"
        if object_key != expected_key:
            return ReceiptReconciliationIssue(
                object_key=object_key,
                code="receipt_object_identity_conflict",
            )
        registry_observation = await self._registry.observe(object_key)
        artifact_observation = await self._artifacts.observe(object_key)
        if registry_observation is None or artifact_observation is None:
            return ReceiptReconciliationIssue(
                object_key=object_key,
                code="receipt_destination_acknowledgement_missing",
            )
        digest = signed_receipt_digest(envelope)
        if (
            registry_observation.receipt_digest != digest
            or artifact_observation.receipt_digest != digest
        ):
            return ReceiptReconciliationIssue(
                object_key=object_key,
                code="receipt_observation_digest_conflict",
            )
        return _VerifiedExternalReceipt(
            object_key=object_key,
            envelope=envelope,
            digest=digest,
            registry_reference=registry_observation.external_reference,
            artifact_reference=artifact_observation.external_reference,
        )

    async def _project(
        self,
        candidate: _VerifiedExternalReceipt,
        *,
        now: datetime,
    ) -> str:
        receipt = candidate.envelope.receipt
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                if receipt.prior_receipt_digest is not None:
                    prior = await connection.fetchrow(
                        """
                        SELECT pack_id, record_id
                        FROM publication_receipts
                        WHERE receipt_digest = $1
                        """,
                        receipt.prior_receipt_digest,
                    )
                    if prior is None:
                        return "lineage_pending"
                    if (
                        str(prior["pack_id"]) != receipt.pack_id
                        or str(prior["record_id"]) != receipt.record_id
                    ):
                        raise ReceiptVerificationError("receipt_lineage_record_conflict")

                intent = await connection.fetchrow(
                    """
                    SELECT id, source_draft_id, source_draft_version, reviewed_decision_id,
                           approving_actor_id, pack_id, record_id, approved_payload_digest,
                           expected_base_commit, forge_target, idempotency_key_hash,
                           event_type, prior_receipt_digest,
                           evidence_manifest_digests_json,
                           evidence_acknowledgements_json, state, published_at
                    FROM publication_intents
                    WHERE id = $1
                    FOR UPDATE
                    """,
                    receipt.publication_id,
                )
                if intent is not None:
                    await self._validate_intent(candidate.envelope, intent)

                inserted_receipt = await connection.fetchval(
                    """
                    INSERT INTO publication_receipts (
                        publication_intent_id, publication_id, schema_version,
                        receipt_digest, event_type, prior_receipt_digest, pack_id,
                        record_id, envelope_json, signature_key_id, registry_reference,
                        artifact_reference, published_at, reconciled_at
                    )
                    VALUES ($1, $2, '1.0', $3, $4, $5, $6, $7, $8::jsonb,
                            $9, $10, $11, $12, $13)
                    ON CONFLICT DO NOTHING
                    RETURNING receipt_digest
                    """,
                    intent["id"] if intent is not None else None,
                    receipt.publication_id,
                    candidate.digest,
                    receipt.event_type.value,
                    receipt.prior_receipt_digest,
                    receipt.pack_id,
                    receipt.record_id,
                    json.dumps(candidate.envelope.model_dump(mode="json")),
                    candidate.envelope.signature_key_id,
                    candidate.registry_reference,
                    candidate.artifact_reference,
                    receipt.published_at,
                    now,
                )
                receipt_rows = await connection.fetch(
                    """
                    SELECT publication_id, receipt_digest, event_type,
                           prior_receipt_digest, pack_id, record_id, envelope_json,
                           signature_key_id, registry_reference, artifact_reference,
                           published_at
                    FROM publication_receipts
                    WHERE receipt_digest = $1 OR publication_id = $2
                    """,
                    candidate.digest,
                    receipt.publication_id,
                )
                if len(receipt_rows) != 1:
                    raise ReceiptVerificationError("receipt_projection_identity_conflict")
                self._compare_existing(candidate, receipt_rows[0])

                changed = inserted_receipt is not None
                if intent is not None:
                    changed = (
                        await self._restore_ledger(
                            connection,
                            candidate,
                            intent=intent,
                            reconciled_at=now,
                        )
                        or changed
                    )

                commit = next(
                    proof
                    for proof in receipt.verified_steps
                    if proof.step is PublicationStepName.COMMIT_RECORD
                )
                if commit.external_reference is None:
                    raise ReceiptVerificationError("receipt_commit_reference_missing")
                inserted_event = await connection.fetchval(
                    """
                    INSERT INTO accepted_events (
                        publication_intent_id, repository, commit_sha, pack_id,
                        record_id, event_type, receipt_digest, published_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    ON CONFLICT DO NOTHING
                    RETURNING id
                    """,
                    intent["id"] if intent is not None else None,
                    commit.destination,
                    commit.external_reference,
                    receipt.pack_id,
                    receipt.record_id,
                    _accepted_event_type(receipt.event_type),
                    candidate.digest,
                    receipt.published_at,
                )
                event_rows = await connection.fetch(
                    """
                    SELECT publication_intent_id, repository, commit_sha, pack_id,
                           record_id, event_type, receipt_digest, published_at
                    FROM accepted_events
                    WHERE receipt_digest = $1
                       OR (repository = $2 AND commit_sha = $3
                           AND pack_id = $4 AND record_id = $5)
                    """,
                    candidate.digest,
                    commit.destination,
                    commit.external_reference,
                    receipt.pack_id,
                    receipt.record_id,
                )
                if len(event_rows) != 1:
                    raise ReceiptVerificationError("accepted_event_identity_conflict")
                event = event_rows[0]
                expected_intent_id = intent["id"] if intent is not None else None
                if (
                    event["publication_intent_id"] != expected_intent_id
                    or str(event["repository"]) != commit.destination
                    or str(event["commit_sha"]) != commit.external_reference
                    or str(event["pack_id"]) != receipt.pack_id
                    or str(event["record_id"]) != receipt.record_id
                    or str(event["event_type"]) != _accepted_event_type(receipt.event_type)
                    or str(event["receipt_digest"]) != candidate.digest
                    or event["published_at"] != receipt.published_at
                ):
                    raise ReceiptVerificationError("accepted_event_projection_conflict")
                changed = inserted_event is not None or changed
                return "reconstructed" if changed else "already_current"

    async def _validate_intent(
        self,
        envelope: SignedPublicationReceipt,
        intent: Any,
    ) -> None:
        receipt = envelope.receipt
        actual = (
            intent["reviewed_decision_id"],
            intent["approving_actor_id"],
            str(intent["pack_id"]),
            str(intent["record_id"]),
            str(intent["approved_payload_digest"]),
            str(intent["expected_base_commit"]),
            str(intent["idempotency_key_hash"]),
            str(intent["event_type"]),
            intent["prior_receipt_digest"],
            tuple(str(value) for value in _json_array(intent["evidence_manifest_digests_json"])),
            tuple(
                EvidenceAcknowledgement.model_validate(_json_object(value)).model_dump(mode="json")
                for value in _json_array(intent["evidence_acknowledgements_json"])
            ),
            str(intent["forge_target"]),
        )
        commit = next(
            proof
            for proof in receipt.verified_steps
            if proof.step is PublicationStepName.COMMIT_RECORD
        )
        expected = (
            receipt.reviewed_decision_id,
            receipt.approving_actor_id,
            receipt.pack_id,
            receipt.record_id,
            receipt.approved_payload_digest,
            receipt.expected_base_commit,
            receipt.idempotency_key_hash,
            receipt.event_type.value,
            receipt.prior_receipt_digest,
            receipt.evidence_manifest_digests,
            tuple(item.model_dump(mode="json") for item in receipt.evidence_acknowledgements),
            commit.destination,
        )
        if actual != expected:
            raise ReceiptVerificationError("receipt_conflicts_with_publication_intent")

    async def _restore_ledger(
        self,
        connection: Any,
        candidate: _VerifiedExternalReceipt,
        *,
        intent: Any,
        reconciled_at: datetime,
    ) -> bool:
        receipt = candidate.envelope.receipt
        definitions = publication_protocol(str(intent["forge_target"]))
        proof_by_step = {proof.step: proof for proof in receipt.verified_steps}
        receipt_references = {
            PublicationStepName.SIGN_RECEIPT: (
                f"key:{candidate.envelope.signature_key_id}",
                receipt.publisher_adapter_identity,
                receipt.publisher_adapter_version,
            ),
            PublicationStepName.PUBLISH_RECEIPT_REGISTRY: (
                candidate.registry_reference,
                self._registry.identity,
                self._registry.version,
            ),
            PublicationStepName.COPY_RECEIPT: (
                candidate.artifact_reference,
                self._artifacts.identity,
                self._artifacts.version,
            ),
        }
        changed = False
        for definition in definitions:
            inserted_step = await connection.fetchval(
                """
                INSERT INTO publication_steps (
                    publication_intent_id, workflow_version, step_name, ordinal,
                    destination, step_version, state, verified_at
                )
                VALUES ($1, '1.0', $2, $3, $4, 1, 'verified', $5)
                ON CONFLICT DO NOTHING
                RETURNING id
                """,
                receipt.publication_id,
                definition.name.value,
                definition.ordinal,
                definition.destination,
                receipt.published_at,
            )
            step = await connection.fetchrow(
                """
                SELECT step_name, ordinal, destination, step_version, state, verified_at
                FROM publication_steps
                WHERE publication_intent_id = $1 AND ordinal = $2
                """,
                receipt.publication_id,
                definition.ordinal,
            )
            if step is None or (
                str(step["step_name"]) != definition.name.value
                or int(step["ordinal"]) != definition.ordinal
                or str(step["destination"]) != definition.destination
                or int(step["step_version"]) != 1
            ):
                raise ReceiptVerificationError("publication_step_identity_conflict")

            if definition.name in proof_by_step:
                proof = proof_by_step[definition.name]
                digest = proof.content_digest
                reference = proof.external_reference
                adapter_identity = proof.adapter_identity
                adapter_version = proof.adapter_version
                verified_at = proof.verified_at
            else:
                reference, adapter_identity, adapter_version = receipt_references[definition.name]
                digest = candidate.digest
                verified_at = receipt.published_at
            context = {
                "adapter_identity": adapter_identity,
                "adapter_version": adapter_version,
                **(
                    {"merged_tree_digest": receipt.merged_tree_digest}
                    if definition.name is PublicationStepName.COMMIT_RECORD
                    else {}
                ),
                **(
                    {"release_version": receipt.release_version}
                    if definition.name is PublicationStepName.SIGN_RELEASE
                    else {}
                ),
                **(
                    {"registry_result": receipt.registry_result}
                    if definition.name is PublicationStepName.CONFIRM_REGISTRY
                    else {}
                ),
                **(
                    {"signed_receipt": candidate.envelope.model_dump(mode="json")}
                    if definition.name is PublicationStepName.SIGN_RECEIPT
                    else {}
                ),
            }
            inserted_ack = await connection.fetchval(
                """
                INSERT INTO publication_durable_acknowledgements (
                    publication_intent_id, acknowledgement_kind, destination,
                    content_digest, external_reference, context_json, verified_at
                )
                VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
                ON CONFLICT DO NOTHING
                RETURNING id
                """,
                receipt.publication_id,
                definition.name.value,
                definition.destination,
                digest,
                reference,
                json.dumps(context),
                verified_at,
            )
            acknowledgement = await connection.fetchrow(
                """
                SELECT content_digest, external_reference, context_json, verified_at
                FROM publication_durable_acknowledgements
                WHERE publication_intent_id = $1
                  AND acknowledgement_kind = $2
                  AND destination = $3
                """,
                receipt.publication_id,
                definition.name.value,
                definition.destination,
            )
            if acknowledgement is None or (
                str(acknowledgement["content_digest"]) != digest
                or acknowledgement["external_reference"] != reference
                or any(
                    _json_object(acknowledgement["context_json"]).get(key) != value
                    for key, value in context.items()
                )
                or acknowledgement["verified_at"] != verified_at
            ):
                raise ReceiptVerificationError("publication_acknowledgement_conflict")

            if str(step["state"]) != "verified" or step["verified_at"] != verified_at:
                await connection.execute(
                    """
                    UPDATE publication_steps
                    SET state = 'verified', verified_at = $3,
                        lease_token = NULL, lease_owner = NULL,
                        lease_expires_at = NULL
                    WHERE publication_intent_id = $1 AND ordinal = $2
                    """,
                    receipt.publication_id,
                    definition.ordinal,
                    verified_at,
                )
                changed = True
            changed = inserted_step is not None or inserted_ack is not None or changed

        if intent["published_at"] is not None and intent["published_at"] != receipt.published_at:
            raise ReceiptVerificationError("publication_intent_published_at_conflict")
        if str(intent["state"]) != "published" or intent["published_at"] is None:
            await connection.execute(
                """
                UPDATE publication_intents
                SET state = 'published',
                    workflow_revision = GREATEST(workflow_revision, $2),
                    published_at = $3,
                    updated_at = $4,
                    last_failure_code = NULL,
                    last_failure_context_json = '{}'::jsonb
                WHERE id = $1
                """,
                receipt.publication_id,
                len(definitions) + 1,
                receipt.published_at,
                reconciled_at,
            )
            changed = True
        return changed

    @staticmethod
    def _compare_existing(candidate: _VerifiedExternalReceipt, existing: Any) -> None:
        receipt = candidate.envelope.receipt
        if (
            existing["publication_id"] != receipt.publication_id
            or str(existing["receipt_digest"]) != candidate.digest
            or str(existing["event_type"]) != receipt.event_type.value
            or existing["prior_receipt_digest"] != receipt.prior_receipt_digest
            or str(existing["pack_id"]) != receipt.pack_id
            or str(existing["record_id"]) != receipt.record_id
            or _json_object(existing["envelope_json"]) != candidate.envelope.model_dump(mode="json")
            or str(existing["signature_key_id"]) != candidate.envelope.signature_key_id
            or str(existing["registry_reference"]) != candidate.registry_reference
            or str(existing["artifact_reference"]) != candidate.artifact_reference
            or existing["published_at"] != receipt.published_at
        ):
            raise ReceiptVerificationError("receipt_projection_conflict")


def _accepted_event_type(event_type: ReceiptEventType) -> str:
    return {
        ReceiptEventType.PUBLICATION: "record.published",
        ReceiptEventType.CORRECTION: "record.corrected",
        ReceiptEventType.REVOCATION: "record.revoked",
    }[event_type]


def _json_array(value: object) -> tuple[object, ...]:
    parsed: object = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, list):
        raise ValueError("Receipt projection JSON must be an array")
    return tuple(parsed)


def _json_object(value: object) -> dict[str, object]:
    parsed: object = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, dict):
        raise ValueError("Receipt projection JSON must be an object")
    return {str(key): item for key, item in parsed.items()}
