from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import opennosh_api.publication.natural_proof as natural_proof
import pytest
from opennosh_api.publication.natural_proof import (
    NaturalPublicationProofRequest,
    NaturalPublicationSnapshot,
    NaturalPublicationStep,
    build_natural_publication_proof,
    collect_natural_publication_snapshot,
    load_natural_publication_proof_request,
    natural_proof_digest,
)
from opennosh_api.publication.public_verifier import NaturalPublicVerification
from opennosh_api.publication.receipts import SignedPublicationReceipt, signed_receipt_digest
from opennosh_api.publication.state import PublicationStepName
from pydantic import SecretStr
from tests.publication.test_planner import NOW, PUBLICATION_ID, snapshot

DRAFT_ID = UUID("33333333-3333-4333-8333-333333333333")
CASE_ID = UUID("22222222-2222-4222-8222-222222222222")
DECISION_ID = UUID("44444444-4444-4444-8444-444444444444")
CONTRIBUTOR_ID = UUID("77777777-7777-4777-8777-777777777777")
STEWARD_ID = UUID("55555555-5555-4555-8555-555555555555")
CAPTURE_DIGEST = "1" * 64


def request() -> NaturalPublicationProofRequest:
    return NaturalPublicationProofRequest(
        draft_id=DRAFT_ID,
        draft_version=1,
        review_case_id=CASE_ID,
        decision_id=DECISION_ID,
        publication_intent_id=PUBLICATION_ID,
        pack_id="commons",
        record_id="lentils",
        browser_capture_sha256=CAPTURE_DIGEST,
    )


def proof_snapshot() -> NaturalPublicationSnapshot:
    publication = snapshot(current=len(PublicationStepName))
    signed_ack = next(
        item
        for item in publication.acknowledgements
        if item.step is PublicationStepName.SIGN_RECEIPT
    )
    envelope = SignedPublicationReceipt.model_validate(signed_ack.context["signed_receipt"])
    receipt_digest = signed_receipt_digest(envelope)
    acknowledgement_digests = {
        item.step.value: item.content_digest for item in publication.acknowledgements
    }
    return NaturalPublicationSnapshot(
        draft_id=DRAFT_ID,
        draft_version=1,
        draft_state="published",
        contributor_actor_id=CONTRIBUTOR_ID,
        submitted_at=NOW - timedelta(minutes=4),
        review_case_id=CASE_ID,
        review_case_draft_id=DRAFT_ID,
        review_case_draft_version=1,
        review_case_pack_id="commons",
        review_case_contributor_actor_id=CONTRIBUTOR_ID,
        review_case_state="approved",
        assigned_steward_actor_id=STEWARD_ID,
        review_opened_at=NOW - timedelta(minutes=3),
        decision_id=DECISION_ID,
        decision_draft_id=DRAFT_ID,
        decision_draft_version=1,
        decision_pack_id="commons",
        decision_record_id="lentils",
        decision_contributor_actor_id=CONTRIBUTOR_ID,
        deciding_actor_id=STEWARD_ID,
        decision_outcome="approved",
        approved_payload_digest="a" * 64,
        decision_forge_target=publication.forge_target,
        decision_decided_at=NOW - timedelta(minutes=2),
        decision_successor_count=0,
        active_steward_count=1,
        recusal_count=0,
        intervention_count=0,
        publication_intent_id=PUBLICATION_ID,
        intent_draft_id=DRAFT_ID,
        intent_draft_version=1,
        intent_decision_id=DECISION_ID,
        intent_approving_actor_id=STEWARD_ID,
        intent_state="published",
        intent_pack_id="commons",
        intent_record_id="lentils",
        intent_payload_digest="a" * 64,
        intent_forge_target=publication.forge_target,
        intent_event_type="publication",
        intent_prior_receipt_digest=None,
        intent_evidence_digests=("f" * 64,),
        intent_created_at=NOW - timedelta(minutes=1),
        intent_published_at=NOW,
        initial_intent_count=1,
        successor_intent_count=0,
        receipt_count=1,
        receipt_digest=receipt_digest,
        receipt_pack_id="commons",
        receipt_record_id="lentils",
        receipt_event_type="publication",
        receipt_prior_digest=None,
        receipt_envelope=envelope.model_dump(mode="json"),
        receipt_published_at=NOW,
        accepted_event_count=1,
        accepted_repository=publication.forge_target,
        accepted_commit_sha="b" * 40,
        accepted_pack_id="commons",
        accepted_record_id="lentils",
        accepted_event_type="contribution",
        accepted_receipt_digest=receipt_digest,
        accepted_published_at=NOW,
        steps=tuple(
            NaturalPublicationStep(
                name=item.name.value,
                ordinal=item.ordinal,
                destination=item.destination,
                state="verified",
                acknowledgement_count=1,
                acknowledgement_content_digest=acknowledgement_digests[item.name.value],
            )
            for item in publication.steps
        ),
    )


def test_verified_proof_is_deterministic_redacted_and_digest_bound() -> None:
    first = build_natural_publication_proof(request(), proof_snapshot(), observed_at=NOW)
    second = build_natural_publication_proof(request(), proof_snapshot(), observed_at=NOW)

    assert first == second
    assert first["status"] == "verified"
    assert first["failures"] == []
    assert first["proof_sha256"] == natural_proof_digest(first)
    serialized = json.dumps(first, sort_keys=True)
    for private_value in (str(DRAFT_ID), str(CASE_ID), str(DECISION_ID), str(STEWARD_ID)):
        assert private_value not in serialized


def test_every_trust_boundary_blocks_with_stable_safe_codes() -> None:
    unsafe = proof_snapshot().model_copy(
        update={
            "draft_version": 2,
            "review_case_state": "disputed",
            "assigned_steward_actor_id": CONTRIBUTOR_ID,
            "deciding_actor_id": CONTRIBUTOR_ID,
            "active_steward_count": 0,
            "recusal_count": 1,
            "intervention_count": 1,
            "initial_intent_count": 2,
            "successor_intent_count": 1,
            "decision_successor_count": 1,
            "receipt_count": 2,
            "accepted_event_count": 0,
            "accepted_commit_sha": "not-a-commit",
        }
    )

    report = build_natural_publication_proof(request(), unsafe, observed_at=NOW)

    assert report["status"] == "blocked"
    assert report["failures"] == sorted(report["failures"])
    assert {
        "draft_version_mismatch",
        "review_case_not_approved",
        "self_review_detected",
        "steward_role_not_active_at_decision",
        "steward_recusal_present",
        "publication_intervention_present",
        "publication_intent_count_not_one",
        "publication_successor_present",
        "decision_successor_present",
        "publication_receipt_count_not_one",
        "accepted_event_count_not_one",
        "accepted_event_commit_invalid",
    }.issubset(set(report["failures"]))


def test_receipt_tamper_and_timestamp_reversal_fail_closed() -> None:
    source = proof_snapshot()
    assert source.receipt_envelope is not None
    tampered = json.loads(json.dumps(source.receipt_envelope))
    tampered["receipt"]["record_id"] = "another-record"
    unsafe = source.model_copy(
        update={
            "receipt_envelope": tampered,
            "review_opened_at": NOW + timedelta(minutes=1),
        }
    )

    report = build_natural_publication_proof(request(), unsafe, observed_at=NOW)

    assert report["status"] == "blocked"
    assert "receipt_digest_mismatch" in report["failures"]
    assert "receipt_record_mismatch" in report["failures"]
    assert "lineage_time_order_invalid" in report["failures"]


def test_effect_and_accepted_event_binding_fail_closed() -> None:
    source = proof_snapshot()
    first = source.steps[0].model_copy(
        update={"acknowledgement_content_digest": "0" * 64}
    )
    unsafe = source.model_copy(
        update={
            "steps": (first, *source.steps[1:]),
            "accepted_repository": "github.com/example/other",
            "accepted_commit_sha": "c" * 40,
            "intent_published_at": NOW - timedelta(seconds=1),
        }
    )

    report = build_natural_publication_proof(request(), unsafe, observed_at=NOW)

    assert {
        "publication_step_acknowledgement_digest_mismatch",
        "accepted_event_repository_mismatch",
        "accepted_event_commit_mismatch",
        "publication_time_mismatch",
    }.issubset(set(report["failures"]))


class FakeConnection:
    def __init__(self, source: NaturalPublicationSnapshot) -> None:
        self.source = source
        self.arguments: tuple[object, ...] | None = None

    async def fetchrow(self, _query: str, *arguments: object) -> dict[str, object]:
        self.arguments = arguments
        payload = self.source.model_dump(mode="python", exclude={"steps"})
        payload["intent_evidence_digests"] = json.dumps(payload["intent_evidence_digests"])
        payload["receipt_envelope"] = json.dumps(payload["receipt_envelope"])
        return payload

    async def fetch(self, _query: str, *arguments: object) -> list[dict[str, object]]:
        assert arguments == (PUBLICATION_ID,)
        return [item.model_dump(mode="python") for item in self.source.steps]


@pytest.mark.asyncio
async def test_collector_uses_exact_identifiers_and_decodes_json() -> None:
    connection = FakeConnection(proof_snapshot())

    collected = await collect_natural_publication_snapshot(connection, request())

    assert collected == proof_snapshot()
    assert connection.arguments == (DRAFT_ID, CASE_ID, DECISION_ID, PUBLICATION_ID)


def test_naive_observation_is_rejected() -> None:
    try:
        build_natural_publication_proof(
            request(), proof_snapshot(), observed_at=datetime(2026, 9, 1)
        )
    except ValueError as error:
        assert str(error) == "Natural publication proof time must include a timezone"
    else:
        raise AssertionError("naive proof observation unexpectedly passed")


def test_request_rejects_noncanonical_identity_and_capture_digest() -> None:
    payload = request().model_dump(mode="python")
    payload["pack_id"] = "Not Canonical"
    payload["browser_capture_sha256"] = "short"

    try:
        NaturalPublicationProofRequest.model_validate(payload)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid proof request unexpectedly passed")


def test_report_uses_utc_for_offset_observation() -> None:
    observed = datetime.fromisoformat("2026-09-01T19:00:00-04:00")

    report = build_natural_publication_proof(request(), proof_snapshot(), observed_at=observed)

    assert report["observed_at"] == "2026-09-01T23:00:00+00:00"
    assert report["proof_sha256"] == natural_proof_digest(report)


def test_snapshot_rejects_naive_timestamp() -> None:
    payload = proof_snapshot().model_dump(mode="python")
    payload["submitted_at"] = datetime(2026, 9, 1)

    with pytest.raises(ValueError, match="timestamps must include a timezone"):
        NaturalPublicationSnapshot.model_validate(payload)


def test_missing_and_invalid_receipt_envelopes_block() -> None:
    missing = proof_snapshot().model_copy(update={"receipt_envelope": None})
    invalid = proof_snapshot().model_copy(update={"receipt_envelope": {"schema_version": "bad"}})

    assert "signed_receipt_missing" in build_natural_publication_proof(
        request(), missing, observed_at=NOW
    )["failures"]
    assert "signed_receipt_invalid" in build_natural_publication_proof(
        request(), invalid, observed_at=NOW
    )["failures"]


@pytest.mark.asyncio
async def test_snapshot_collector_rejects_missing_lineage() -> None:
    class MissingConnection:
        async def fetchrow(self, _query: str, *_arguments: object) -> None:
            return None

        async def fetch(self, _query: str, *_arguments: object) -> list[object]:
            raise AssertionError("steps must not be read")

    with pytest.raises(LookupError, match="lineage_not_found"):
        await collect_natural_publication_snapshot(MissingConnection(), request())


def test_request_file_loader_accepts_only_private_canonical_json(tmp_path: Path) -> None:
    path = tmp_path / "proof.json"
    path.write_text(request().model_dump_json(), encoding="utf-8")
    os.chmod(path, 0o600)

    assert load_natural_publication_proof_request(path) == request()

    os.chmod(path, 0o644)
    with pytest.raises(natural_proof.NaturalPublicationProofRequestError, match="mode-0600"):
        load_natural_publication_proof_request(path)
    with pytest.raises(natural_proof.NaturalPublicationProofRequestError, match="inspected"):
        load_natural_publication_proof_request(tmp_path / "missing.json")

    os.chmod(path, 0o600)
    path.write_text("not-json", encoding="utf-8")
    with pytest.raises(natural_proof.NaturalPublicationProofRequestError, match="invalid"):
        load_natural_publication_proof_request(path)


def test_request_file_loader_detects_open_and_read_races(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "proof.json"
    path.write_text(request().model_dump_json(), encoding="utf-8")
    os.chmod(path, 0o600)
    metadata = os.lstat(path)
    monkeypatch.setattr(
        natural_proof.os,
        "fstat",
        lambda _descriptor: SimpleNamespace(
            st_mode=stat.S_IFREG | 0o600,
            st_dev=metadata.st_dev + 1,
            st_ino=metadata.st_ino,
            st_size=metadata.st_size,
        ),
    )
    with pytest.raises(natural_proof.NaturalPublicationProofRequestError, match="before open"):
        load_natural_publication_proof_request(path)

    monkeypatch.undo()
    monkeypatch.setattr(natural_proof.os, "read", lambda _descriptor, _size: b"")
    with pytest.raises(natural_proof.NaturalPublicationProofRequestError, match="while reading"):
        load_natural_publication_proof_request(path)


def test_private_json_and_database_helpers_fail_closed() -> None:
    with pytest.raises(ValueError, match="JSON array"):
        natural_proof._json_array("{}")
    with pytest.raises(ValueError, match="JSON object"):
        natural_proof._json_object_or_none("[]")
    assert natural_proof._json_object_or_none(None) is None
    with pytest.raises(ValueError, match="requires PostgreSQL"):
        natural_proof._postgres_dsn("sqlite:///tmp/test.db")
    with pytest.raises(ValueError, match="failure list"):
        natural_proof._block_public_report({"failures": "unsafe"}, "safe_code")


class _Transaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_arguments: object) -> None:
        return None


class _RuntimeConnection:
    def __init__(self) -> None:
        self.closed = False
        self.transaction_options: dict[str, object] = {}

    def transaction(self, **options: object) -> _Transaction:
        self.transaction_options = options
        return _Transaction()

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_runtime_collector_is_read_only_and_blocks_unconfigured_public_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _RuntimeConnection()

    async def connect(_dsn: str) -> _RuntimeConnection:
        return connection

    async def collect(_connection: object, _request: object) -> NaturalPublicationSnapshot:
        return proof_snapshot()

    monkeypatch.setattr(natural_proof.asyncpg, "connect", connect)
    monkeypatch.setattr(natural_proof, "collect_natural_publication_snapshot", collect)
    runtime_settings = SimpleNamespace(
        publication_claims_enabled=False,
        publication_continuous_claims_enabled=False,
        public_artifact_base_url=None,
        process_database_url=lambda _role: "postgresql://proof@db.example/test",
    )

    report = await natural_proof.collect_natural_publication_proof(
        runtime_settings, request(), observed_at=NOW  # type: ignore[arg-type]
    )

    assert report["status"] == "blocked"
    assert report["failures"] == ["public_artifact_origin_unconfigured"]
    assert connection.transaction_options == {"isolation": "repeatable_read", "readonly": True}
    assert connection.closed is True


@pytest.mark.asyncio
async def test_runtime_collector_returns_blocked_lineage_before_public_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _RuntimeConnection()

    async def connect(_dsn: str) -> _RuntimeConnection:
        return connection

    async def collect(_connection: object, _request: object) -> NaturalPublicationSnapshot:
        return proof_snapshot().model_copy(update={"decision_outcome": "rejected"})

    monkeypatch.setattr(natural_proof.asyncpg, "connect", connect)
    monkeypatch.setattr(natural_proof, "collect_natural_publication_snapshot", collect)
    runtime_settings = SimpleNamespace(
        publication_claims_enabled=False,
        publication_continuous_claims_enabled=False,
        public_artifact_base_url="https://must-not-be-read.example",
        process_database_url=lambda _role: "postgresql://proof@db.example/test",
    )

    report = await natural_proof.collect_natural_publication_proof(
        runtime_settings, request(), observed_at=NOW  # type: ignore[arg-type]
    )

    assert report["status"] == "blocked"
    assert "decision_not_approved" in report["failures"]


@pytest.mark.asyncio
async def test_runtime_collector_refuses_enabled_claims() -> None:
    runtime_settings = SimpleNamespace(
        publication_claims_enabled=True,
        publication_continuous_claims_enabled=False,
    )

    with pytest.raises(ValueError, match="claims disabled"):
        await natural_proof.collect_natural_publication_proof(  # type: ignore[arg-type]
            runtime_settings, request(), observed_at=NOW
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("public_failure", [False, True])
async def test_runtime_collector_verifies_and_safely_blocks_public_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    public_failure: bool,
) -> None:
    connection = _RuntimeConnection()

    async def connect(_dsn: str) -> _RuntimeConnection:
        return connection

    async def collect(_connection: object, _request: object) -> NaturalPublicationSnapshot:
        return proof_snapshot()

    class Reader:
        closed = False

        async def aclose(self) -> None:
            self.closed = True

    reader = Reader()
    verification = NaturalPublicVerification(
        release_version="0.71.0.0",
        manifest_sha256="a" * 64,
        receipt_sha256="b" * 64,
        record_sha256="c" * 64,
        provenance_sha256="d" * 64,
    )

    async def verify(**_arguments: object) -> object:
        if public_failure:
            raise natural_proof.NaturalPublicVerificationError("public_safe_failure")
        return verification

    monkeypatch.setattr(natural_proof.asyncpg, "connect", connect)
    monkeypatch.setattr(natural_proof, "collect_natural_publication_snapshot", collect)
    monkeypatch.setattr(natural_proof, "HttpArtifactStore", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        natural_proof.PublicationReceiptKeyRing,
        "from_json",
        lambda _value: object(),
    )
    monkeypatch.setattr(
        natural_proof.ManifestKeyRing,
        "from_config",
        lambda _value: object(),
    )
    monkeypatch.setattr(
        natural_proof,
        "PublicArtifactReadService",
        lambda **_kwargs: reader,
    )
    monkeypatch.setattr(natural_proof, "verify_natural_publication_artifacts", verify)
    runtime_settings = SimpleNamespace(
        publication_claims_enabled=False,
        publication_continuous_claims_enabled=False,
        public_artifact_base_url="https://public.example",
        public_artifact_timeout_seconds=2.0,
        publication_receipt_verifying_keys=SecretStr("{}"),
        public_commons_verifying_keys={},
        process_database_url=lambda _role: "postgresql://proof@db.example/test",
    )

    report = await natural_proof.collect_natural_publication_proof(
        runtime_settings, request(), observed_at=NOW  # type: ignore[arg-type]
    )

    assert reader.closed is True
    if public_failure:
        assert report["status"] == "blocked"
        assert report["failures"] == ["public_safe_failure"]
    else:
        assert report["status"] == "verified"
        assert report["public_artifacts"] == verification.to_dict()
