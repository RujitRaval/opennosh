from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from uuid import UUID

import asyncpg  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.engine import make_url

from opennosh_api.capacity import ProcessRole
from opennosh_api.public.artifacts import HttpArtifactStore, PublicArtifactReadService
from opennosh_api.public_commons.manifests import ManifestKeyRing
from opennosh_api.publication.public_verifier import (
    NaturalPublicVerificationError,
    verify_natural_publication_artifacts,
)
from opennosh_api.publication.receipts import (
    PublicationReceiptKeyRing,
    SignedPublicationReceipt,
    signed_receipt_digest,
)
from opennosh_api.publication.state import publication_protocol
from opennosh_api.settings import Settings

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_IDENTIFIER = r"^[a-z0-9](?:[a-z0-9-]{0,158}[a-z0-9])?$"
_MAX_REQUEST_BYTES = 64 * 1024


class NaturalPublicationProofRequestError(ValueError):
    pass


class NaturalProofConnection(Protocol):
    async def fetchrow(self, query: str, *arguments: object) -> Mapping[str, Any] | None: ...

    async def fetch(self, query: str, *arguments: object) -> list[Mapping[str, Any]]: ...


class NaturalPublicationProofRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    draft_id: UUID
    draft_version: int = Field(gt=0)
    review_case_id: UUID
    decision_id: UUID
    publication_intent_id: UUID
    pack_id: str = Field(pattern=_IDENTIFIER)
    record_id: str = Field(pattern=_IDENTIFIER)
    browser_capture_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class NaturalPublicationStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    ordinal: int
    destination: str
    state: str
    acknowledgement_count: int = Field(ge=0)
    acknowledgement_content_digest: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )


class NaturalPublicationSnapshot(BaseModel):
    """Private database projection. Actor and row IDs never enter the public report."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    draft_id: UUID
    draft_version: int
    draft_state: str
    contributor_actor_id: UUID
    submitted_at: datetime | None
    review_case_id: UUID
    review_case_draft_id: UUID
    review_case_draft_version: int
    review_case_pack_id: str
    review_case_contributor_actor_id: UUID
    review_case_state: str
    assigned_steward_actor_id: UUID | None
    review_opened_at: datetime
    decision_id: UUID
    decision_draft_id: UUID
    decision_draft_version: int
    decision_pack_id: str
    decision_record_id: str
    decision_contributor_actor_id: UUID
    deciding_actor_id: UUID
    decision_outcome: str
    approved_payload_digest: str | None
    decision_forge_target: str | None
    decision_decided_at: datetime
    decision_successor_count: int = Field(ge=0)
    active_steward_count: int = Field(ge=0)
    recusal_count: int = Field(ge=0)
    intervention_count: int = Field(ge=0)
    publication_intent_id: UUID
    intent_draft_id: UUID
    intent_draft_version: int
    intent_decision_id: UUID
    intent_approving_actor_id: UUID
    intent_state: str
    intent_pack_id: str
    intent_record_id: str
    intent_payload_digest: str
    intent_forge_target: str
    intent_event_type: str
    intent_prior_receipt_digest: str | None
    intent_evidence_digests: tuple[str, ...]
    intent_created_at: datetime
    intent_published_at: datetime | None
    initial_intent_count: int = Field(ge=0)
    successor_intent_count: int = Field(ge=0)
    receipt_count: int = Field(ge=0)
    receipt_digest: str | None
    receipt_pack_id: str | None
    receipt_record_id: str | None
    receipt_event_type: str | None
    receipt_prior_digest: str | None
    receipt_envelope: dict[str, Any] | None
    receipt_published_at: datetime | None
    accepted_event_count: int = Field(ge=0)
    accepted_repository: str | None
    accepted_commit_sha: str | None
    accepted_pack_id: str | None
    accepted_record_id: str | None
    accepted_event_type: str | None
    accepted_receipt_digest: str | None
    accepted_published_at: datetime | None
    steps: tuple[NaturalPublicationStep, ...]

    @field_validator(
        "submitted_at",
        "review_opened_at",
        "decision_decided_at",
        "intent_created_at",
        "intent_published_at",
        "receipt_published_at",
        "accepted_published_at",
    )
    @classmethod
    def require_aware_times(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("Natural publication proof timestamps must include a timezone")
        return value.astimezone(UTC) if value is not None else None


def canonical_json(payload: Mapping[str, object]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def natural_proof_digest(payload: Mapping[str, object]) -> str:
    unsigned = dict(payload)
    unsigned.pop("proof_sha256", None)
    return hashlib.sha256(canonical_json(unsigned)).hexdigest()


def _lineage_digest(request: NaturalPublicationProofRequest) -> str:
    return hashlib.sha256(
        canonical_json(
            {
                "browser_capture_sha256": request.browser_capture_sha256,
                "decision_id": str(request.decision_id),
                "draft_id": str(request.draft_id),
                "draft_version": request.draft_version,
                "publication_intent_id": str(request.publication_intent_id),
                "review_case_id": str(request.review_case_id),
            }
        )
    ).hexdigest()


def build_natural_publication_proof(
    request: NaturalPublicationProofRequest,
    snapshot: NaturalPublicationSnapshot,
    *,
    observed_at: datetime,
) -> dict[str, object]:
    """Validate one canonical lineage and emit only redacted proof material."""

    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("Natural publication proof time must include a timezone")
    failures: list[str] = []

    def require(condition: bool, code: str) -> None:
        if not condition:
            failures.append(code)

    require(snapshot.draft_id == request.draft_id, "draft_identity_mismatch")
    require(snapshot.draft_version == request.draft_version, "draft_version_mismatch")
    require(snapshot.submitted_at is not None, "draft_not_submitted")
    require(
        snapshot.draft_state in {"publication_pending", "published"},
        "draft_not_publication_bound",
    )
    require(snapshot.review_case_id == request.review_case_id, "review_case_identity_mismatch")
    require(snapshot.review_case_draft_id == request.draft_id, "review_case_draft_mismatch")
    require(
        snapshot.review_case_draft_version == request.draft_version,
        "review_case_version_mismatch",
    )
    require(snapshot.review_case_pack_id == request.pack_id, "review_case_pack_mismatch")
    require(
        snapshot.review_case_contributor_actor_id == snapshot.contributor_actor_id,
        "review_case_contributor_mismatch",
    )
    require(snapshot.review_case_state in {"approved", "closed"}, "review_case_not_approved")
    require(snapshot.decision_id == request.decision_id, "decision_identity_mismatch")
    require(snapshot.decision_draft_id == request.draft_id, "decision_draft_mismatch")
    require(snapshot.decision_draft_version == request.draft_version, "decision_version_mismatch")
    require(snapshot.decision_pack_id == request.pack_id, "decision_pack_mismatch")
    require(snapshot.decision_record_id == request.record_id, "decision_record_mismatch")
    require(
        snapshot.decision_contributor_actor_id == snapshot.contributor_actor_id,
        "decision_contributor_mismatch",
    )
    require(snapshot.decision_outcome == "approved", "decision_not_approved")
    require(snapshot.approved_payload_digest is not None, "approved_payload_digest_missing")
    require(snapshot.decision_successor_count == 0, "decision_successor_present")
    require(snapshot.deciding_actor_id != snapshot.contributor_actor_id, "self_review_detected")
    require(
        snapshot.assigned_steward_actor_id == snapshot.deciding_actor_id,
        "decider_not_assigned_steward",
    )
    require(snapshot.active_steward_count == 1, "steward_role_not_active_at_decision")
    require(snapshot.recusal_count == 0, "steward_recusal_present")
    require(snapshot.intervention_count == 0, "publication_intervention_present")

    require(
        snapshot.publication_intent_id == request.publication_intent_id,
        "publication_intent_identity_mismatch",
    )
    require(snapshot.intent_draft_id == request.draft_id, "publication_draft_mismatch")
    require(snapshot.intent_draft_version == request.draft_version, "publication_version_mismatch")
    require(snapshot.intent_decision_id == request.decision_id, "publication_decision_mismatch")
    require(
        snapshot.intent_approving_actor_id == snapshot.deciding_actor_id,
        "publication_approver_mismatch",
    )
    require(snapshot.intent_state == "published", "publication_not_published")
    require(snapshot.intent_pack_id == request.pack_id, "publication_pack_mismatch")
    require(snapshot.intent_record_id == request.record_id, "publication_record_mismatch")
    require(
        snapshot.intent_payload_digest == snapshot.approved_payload_digest,
        "publication_payload_mismatch",
    )
    require(snapshot.intent_event_type == "publication", "publication_not_initial_event")
    require(snapshot.intent_prior_receipt_digest is None, "publication_has_prior_receipt")
    require(snapshot.initial_intent_count == 1, "publication_intent_count_not_one")
    require(snapshot.successor_intent_count == 0, "publication_successor_present")
    require(snapshot.receipt_count == 1, "publication_receipt_count_not_one")
    require(snapshot.accepted_event_count == 1, "accepted_event_count_not_one")
    require(snapshot.intent_published_at is not None, "publication_time_missing")
    require(
        snapshot.decision_forge_target == snapshot.intent_forge_target,
        "publication_forge_target_mismatch",
    )

    protocol = publication_protocol(snapshot.intent_forge_target)
    expected_steps = tuple((item.name.value, item.ordinal, item.destination) for item in protocol)
    actual_steps = tuple((item.name, item.ordinal, item.destination) for item in snapshot.steps)
    require(actual_steps == expected_steps, "publication_protocol_mismatch")
    require(
        all(item.state == "verified" for item in snapshot.steps),
        "publication_step_not_verified",
    )
    require(
        all(item.acknowledgement_count == 1 for item in snapshot.steps),
        "publication_step_acknowledgement_count_not_one",
    )

    envelope: SignedPublicationReceipt | None = None
    if snapshot.receipt_envelope is None:
        failures.append("signed_receipt_missing")
    else:
        try:
            envelope = SignedPublicationReceipt.model_validate(snapshot.receipt_envelope)
        except ValueError:
            failures.append("signed_receipt_invalid")
    if envelope is not None:
        receipt = envelope.receipt
        calculated_receipt_digest = signed_receipt_digest(envelope)
        require(snapshot.receipt_digest == calculated_receipt_digest, "receipt_digest_mismatch")
        require(
            receipt.publication_id == request.publication_intent_id,
            "receipt_publication_identity_mismatch",
        )
        require(receipt.reviewed_decision_id == request.decision_id, "receipt_decision_mismatch")
        require(
            receipt.approving_actor_id == snapshot.deciding_actor_id,
            "receipt_approver_mismatch",
        )
        require(receipt.pack_id == request.pack_id, "receipt_pack_mismatch")
        require(receipt.record_id == request.record_id, "receipt_record_mismatch")
        require(
            receipt.approved_payload_digest == snapshot.approved_payload_digest,
            "receipt_payload_mismatch",
        )
        require(
            tuple(receipt.evidence_manifest_digests) == snapshot.intent_evidence_digests,
            "receipt_evidence_mismatch",
        )
        require(snapshot.receipt_event_type == "publication", "receipt_not_initial_event")
        require(snapshot.receipt_prior_digest is None, "receipt_has_prior_digest")
        require(snapshot.receipt_pack_id == request.pack_id, "receipt_row_pack_mismatch")
        require(snapshot.receipt_record_id == request.record_id, "receipt_row_record_mismatch")
        require(snapshot.receipt_published_at == receipt.published_at, "receipt_time_mismatch")
        expected_acknowledgements = {
            proof.step.value: proof.content_digest for proof in receipt.verified_steps
        }
        expected_acknowledgements.update(
            {
                "sign_receipt": calculated_receipt_digest,
                "publish_receipt_registry": calculated_receipt_digest,
                "copy_receipt": calculated_receipt_digest,
            }
        )
        require(
            all(
                step.acknowledgement_content_digest
                == expected_acknowledgements.get(step.name)
                for step in snapshot.steps
            ),
            "publication_step_acknowledgement_digest_mismatch",
        )

    require(
        snapshot.accepted_receipt_digest == snapshot.receipt_digest,
        "accepted_event_receipt_mismatch",
    )
    require(snapshot.accepted_pack_id == request.pack_id, "accepted_event_pack_mismatch")
    require(snapshot.accepted_record_id == request.record_id, "accepted_event_record_mismatch")
    require(snapshot.accepted_event_type == "contribution", "accepted_event_type_mismatch")
    require(
        snapshot.accepted_repository == snapshot.intent_forge_target,
        "accepted_event_repository_mismatch",
    )
    require(
        snapshot.accepted_commit_sha is not None
        and _GIT_COMMIT.fullmatch(snapshot.accepted_commit_sha) is not None,
        "accepted_event_commit_invalid",
    )
    if envelope is not None:
        require(
            snapshot.accepted_commit_sha == envelope.receipt.merged_commit,
            "accepted_event_commit_mismatch",
        )
    require(
        snapshot.accepted_published_at == snapshot.receipt_published_at,
        "accepted_time_mismatch",
    )
    require(
        snapshot.intent_published_at == snapshot.receipt_published_at,
        "publication_time_mismatch",
    )

    ordered_times = (
        snapshot.submitted_at,
        snapshot.review_opened_at,
        snapshot.decision_decided_at,
        snapshot.intent_created_at,
        snapshot.receipt_published_at,
    )
    present_times = tuple(value for value in ordered_times if value is not None)
    if len(present_times) == len(ordered_times):
        require(
            all(
                left <= right for left, right in zip(present_times, present_times[1:], strict=False)
            ),
            "lineage_time_order_invalid",
        )

    report: dict[str, object] = {
        "schema_version": "1.0",
        "status": "verified" if not failures else "blocked",
        "observed_at": observed_at.astimezone(UTC).isoformat(),
        "lineage": {
            "browser_capture_sha256": request.browser_capture_sha256,
            "lineage_sha256": _lineage_digest(request),
            "draft_version": request.draft_version,
            "pack_id": request.pack_id,
            "record_id": request.record_id,
        },
        "publication": {
            "commit_sha": snapshot.accepted_commit_sha,
            "receipt_sha256": snapshot.receipt_digest,
            "published_at": (
                snapshot.receipt_published_at.isoformat()
                if snapshot.receipt_published_at is not None
                else None
            ),
            "protocol_steps": len(snapshot.steps),
        },
        "failures": sorted(set(failures)),
    }
    report["proof_sha256"] = natural_proof_digest(report)
    return report


async def collect_natural_publication_snapshot(
    connection: NaturalProofConnection,
    request: NaturalPublicationProofRequest,
) -> NaturalPublicationSnapshot:
    """Read the selected lineage once; the caller owns the read-only transaction."""

    row = await connection.fetchrow(_LINEAGE_QUERY, *request_query_arguments(request))
    if row is None:
        raise LookupError("natural_publication_lineage_not_found")
    steps = await connection.fetch(_STEPS_QUERY, request.publication_intent_id)
    payload = {key: row[key] for key in row}
    payload["intent_evidence_digests"] = tuple(_json_array(payload["intent_evidence_digests"]))
    payload["receipt_envelope"] = _json_object_or_none(payload["receipt_envelope"])
    payload["steps"] = tuple(
        NaturalPublicationStep(
            name=str(item["name"]),
            ordinal=int(item["ordinal"]),
            destination=str(item["destination"]),
            state=str(item["state"]),
            acknowledgement_count=int(item["acknowledgement_count"]),
            acknowledgement_content_digest=(
                str(item["acknowledgement_content_digest"])
                if item["acknowledgement_content_digest"] is not None
                else None
            ),
        )
        for item in steps
    )
    return NaturalPublicationSnapshot.model_validate(payload)


def load_natural_publication_proof_request(
    path: str | os.PathLike[str],
) -> NaturalPublicationProofRequest:
    request_path = os.fspath(path)
    try:
        metadata = os.lstat(request_path)
    except OSError as error:
        raise NaturalPublicationProofRequestError(
            "Natural proof request could not be inspected"
        ) from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_size <= 0
        or metadata.st_size > _MAX_REQUEST_BYTES
    ):
        raise NaturalPublicationProofRequestError(
            "Natural proof request must be a mode-0600 regular file within the size limit"
        )
    descriptor = os.open(request_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != metadata.st_dev
            or opened.st_ino != metadata.st_ino
            or opened.st_size != metadata.st_size
        ):
            raise NaturalPublicationProofRequestError("Natural proof request changed before open")
        payload = os.read(descriptor, opened.st_size + 1)
        if len(payload) != opened.st_size:
            raise NaturalPublicationProofRequestError("Natural proof request changed while reading")
    finally:
        os.close(descriptor)
    try:
        return NaturalPublicationProofRequest.model_validate_json(payload)
    except ValueError as error:
        raise NaturalPublicationProofRequestError("Natural proof request is invalid") from error


def _postgres_dsn(database_url: str) -> str:
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        raise ValueError("Natural publication proof requires PostgreSQL")
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


async def collect_natural_publication_proof(
    settings: Settings,
    request: NaturalPublicationProofRequest,
    *,
    observed_at: datetime | None = None,
) -> dict[str, object]:
    """Collect one proof under a repeatable-read, read-only database transaction."""

    if settings.publication_claims_enabled or settings.publication_continuous_claims_enabled:
        raise ValueError("Natural publication proof requires claims disabled")
    connection = await asyncpg.connect(
        _postgres_dsn(settings.process_database_url(ProcessRole.PUBLICATION))
    )
    try:
        async with connection.transaction(isolation="repeatable_read", readonly=True):
            snapshot = await collect_natural_publication_snapshot(connection, request)
        report = build_natural_publication_proof(
            request,
            snapshot,
            observed_at=observed_at or datetime.now(UTC),
        )
    finally:
        await connection.close()
    if report["status"] != "verified":
        return report
    if settings.public_artifact_base_url is None or snapshot.receipt_envelope is None:
        return _block_public_report(report, "public_artifact_origin_unconfigured")
    envelope = SignedPublicationReceipt.model_validate(snapshot.receipt_envelope)
    store = HttpArtifactStore(
        settings.public_artifact_base_url,
        timeout_seconds=settings.public_artifact_timeout_seconds,
    )
    receipt_keys = PublicationReceiptKeyRing.from_json(
        settings.publication_receipt_verifying_keys.get_secret_value()
    )
    reader = PublicArtifactReadService(
        store=store,
        manifest_keys=ManifestKeyRing.from_config(settings.public_commons_verifying_keys),
        receipt_keys=receipt_keys,
        max_cached_releases=0,
    )
    try:
        public = await verify_natural_publication_artifacts(
            reader=reader,
            store=store,
            receipt_keys=receipt_keys,
            pack_id=request.pack_id,
            record_id=request.record_id,
            expected_release_version=envelope.receipt.release_version,
            expected_manifest_sha256=envelope.receipt.signed_release_metadata_digest,
            expected_receipt_sha256=snapshot.receipt_digest or "",
        )
    except NaturalPublicVerificationError as error:
        return _block_public_report(report, error.code)
    finally:
        await reader.aclose()
    report["public_artifacts"] = public.to_dict()
    report["proof_sha256"] = natural_proof_digest(report)
    return report


def _block_public_report(report: dict[str, object], code: str) -> dict[str, object]:
    failures = report.get("failures")
    if not isinstance(failures, list) or any(not isinstance(item, str) for item in failures):
        raise ValueError("Natural publication proof failure list is invalid")
    report["status"] = "blocked"
    report["failures"] = sorted({*failures, code})
    report["proof_sha256"] = natural_proof_digest(report)
    return report


def request_query_arguments(request: NaturalPublicationProofRequest) -> tuple[object, ...]:
    return (
        request.draft_id,
        request.review_case_id,
        request.decision_id,
        request.publication_intent_id,
    )


def _json_array(value: object) -> list[object]:
    decoded = json.loads(value) if isinstance(value, str) else value
    if not isinstance(decoded, list):
        raise ValueError("Natural publication proof expected a JSON array")
    return decoded


def _json_object_or_none(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    decoded = json.loads(value) if isinstance(value, str) else value
    if not isinstance(decoded, dict):
        raise ValueError("Natural publication proof expected a JSON object")
    return decoded


_LINEAGE_QUERY = """
SELECT
    d.id AS draft_id,
    d.draft_version,
    d.review_state AS draft_state,
    d.user_id AS contributor_actor_id,
    d.submitted_at,
    c.id AS review_case_id,
    c.source_draft_id AS review_case_draft_id,
    c.source_draft_version AS review_case_draft_version,
    c.pack_id AS review_case_pack_id,
    c.contributor_actor_id AS review_case_contributor_actor_id,
    c.state AS review_case_state,
    c.assigned_steward_actor_id,
    c.opened_at AS review_opened_at,
    decision.id AS decision_id,
    decision.source_draft_id AS decision_draft_id,
    decision.source_draft_version AS decision_draft_version,
    decision.pack_id AS decision_pack_id,
    decision.record_id AS decision_record_id,
    decision.contributor_actor_id AS decision_contributor_actor_id,
    decision.deciding_actor_id,
    decision.outcome AS decision_outcome,
    decision.approved_payload_digest,
    decision.forge_target AS decision_forge_target,
    decision.decided_at AS decision_decided_at,
    (SELECT count(*) FROM governance_decisions successor
      WHERE successor.prior_decision_id = decision.id
    ) AS decision_successor_count,
    (SELECT count(*) FROM governance_role_assignments role
      WHERE role.pack_id = decision.pack_id
        AND role.actor_id = decision.deciding_actor_id
        AND role.role = 'steward'
        AND role.granted_at <= decision.decided_at
        AND (role.revoked_at IS NULL OR role.revoked_at > decision.decided_at)
    ) AS active_steward_count,
    (SELECT count(*) FROM governance_recusals recusal
      WHERE recusal.source_draft_id = decision.source_draft_id
        AND recusal.actor_id = decision.deciding_actor_id
        AND recusal.recused_at <= decision.decided_at
    ) AS recusal_count,
    (SELECT count(*) FROM governance_publication_interventions intervention
      WHERE intervention.publication_intent_id = intent.id
    ) AS intervention_count,
    intent.id AS publication_intent_id,
    intent.source_draft_id AS intent_draft_id,
    intent.source_draft_version AS intent_draft_version,
    intent.reviewed_decision_id AS intent_decision_id,
    intent.approving_actor_id AS intent_approving_actor_id,
    intent.state AS intent_state,
    intent.pack_id AS intent_pack_id,
    intent.record_id AS intent_record_id,
    intent.approved_payload_digest AS intent_payload_digest,
    intent.forge_target AS intent_forge_target,
    intent.event_type AS intent_event_type,
    intent.prior_receipt_digest AS intent_prior_receipt_digest,
    intent.evidence_manifest_digests_json AS intent_evidence_digests,
    intent.created_at AS intent_created_at,
    intent.published_at AS intent_published_at,
    (SELECT count(*) FROM publication_intents sibling
      WHERE sibling.source_draft_id = d.id
        AND sibling.source_draft_version = d.draft_version
        AND sibling.prior_publication_intent_id IS NULL
    ) AS initial_intent_count,
    (SELECT count(*) FROM publication_intents successor_intent
      WHERE successor_intent.prior_publication_intent_id = intent.id
    ) AS successor_intent_count,
    (SELECT count(*) FROM publication_receipts receipt_count
      WHERE receipt_count.publication_intent_id = intent.id
    ) AS receipt_count,
    receipt.receipt_digest,
    receipt.pack_id AS receipt_pack_id,
    receipt.record_id AS receipt_record_id,
    receipt.event_type AS receipt_event_type,
    receipt.prior_receipt_digest AS receipt_prior_digest,
    receipt.envelope_json AS receipt_envelope,
    receipt.published_at AS receipt_published_at,
    (SELECT count(*) FROM accepted_events event_count
      WHERE event_count.publication_intent_id = intent.id
    ) AS accepted_event_count,
    accepted.repository AS accepted_repository,
    accepted.commit_sha AS accepted_commit_sha,
    accepted.pack_id AS accepted_pack_id,
    accepted.record_id AS accepted_record_id,
    accepted.event_type AS accepted_event_type,
    accepted.receipt_digest AS accepted_receipt_digest,
    accepted.published_at AS accepted_published_at
FROM contribution_drafts d
JOIN governance_review_cases c ON c.id = $2
JOIN governance_decisions decision ON decision.id = $3
JOIN publication_intents intent ON intent.id = $4
LEFT JOIN publication_receipts receipt ON receipt.publication_intent_id = intent.id
LEFT JOIN accepted_events accepted ON accepted.publication_intent_id = intent.id
WHERE d.id = $1
"""

_STEPS_QUERY = """
SELECT step.step_name AS name,
       step.ordinal,
       step.destination,
       step.state,
       count(ack.id)::bigint AS acknowledgement_count,
       max(ack.content_digest) AS acknowledgement_content_digest
FROM publication_steps step
LEFT JOIN publication_durable_acknowledgements ack
  ON ack.publication_intent_id = step.publication_intent_id
 AND ack.acknowledgement_kind = step.step_name
 AND ack.destination = step.destination
WHERE step.publication_intent_id = $1
GROUP BY step.id, step.step_name, step.ordinal, step.destination, step.state
ORDER BY step.ordinal
"""


__all__ = [
    "NaturalPublicationProofRequest",
    "NaturalPublicationProofRequestError",
    "NaturalPublicationSnapshot",
    "NaturalPublicationStep",
    "build_natural_publication_proof",
    "collect_natural_publication_proof",
    "collect_natural_publication_snapshot",
    "load_natural_publication_proof_request",
    "natural_proof_digest",
]
