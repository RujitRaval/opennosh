from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from opennosh_api.evidence.contracts import (
    DocumentRightsState,
    PublicDocumentManifest,
    RedactionState,
)
from opennosh_api.evidence.service import attach_sanitized_upload, preservation_request
from opennosh_api.evidence.uploads import (
    EvidenceUploadConflictError,
    EvidenceUploadNotFoundError,
    EvidenceUploadState,
)
from opennosh_api.jobs import JobLane


def test_preservation_request_contains_only_typed_identity_and_digest() -> None:
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    manifest = PublicDocumentManifest(
        evidence_id=uuid4(),
        canonical_uri="https://example.test/reference",
        publisher="Publisher",
        license="CC-BY-4.0",
        title="Reference",
        observed_at=now,
        observed_digest=hashlib.sha256(b"observed source").hexdigest(),
        rights_state=DocumentRightsState.REFERENCE_ONLY,
    )

    request = preservation_request(manifest, run_after=now)

    assert request.message.lane is JobLane.EVIDENCE
    assert request.message.job_type == "evidence.preserve"
    assert request.message.subject_id == manifest.evidence_id
    assert request.message.idempotency_key == request.deduplication_key
    encoded = request.message.model_dump_json()
    assert "observed source" not in encoded
    assert "canonical_uri" not in encoded


class _Session:
    def __init__(self, *records: object) -> None:
        self.records = list(records)
        self.commits = 0

    async def scalar(self, statement: object) -> object | None:
        del statement
        return self.records.pop(0)

    async def commit(self) -> None:
        self.commits += 1


def _draft(draft_id: object, user_id: object) -> SimpleNamespace:
    return SimpleNamespace(
        id=draft_id,
        user_id=user_id,
        draft_version=2,
        review_state="draft",
    )


def _upload(upload_id: object, draft_id: object, user_id: object) -> SimpleNamespace:
    return SimpleNamespace(
        id=upload_id,
        draft_id=draft_id,
        user_id=user_id,
        source_draft_version=2,
        state=EvidenceUploadState.SANITIZED.value,
        sanitized_object_key="sanitized/" + "a" * 64 + ".png",
        sanitized_sha256="a" * 64,
        sanitized_at=datetime(2026, 9, 1, 17, 59, tzinfo=UTC),
        attached_evidence_id=None,
        attached_at=None,
        preserved_at=None,
        observed_byte_length=8,
        observed_sha256="b" * 64,
        declared_media_type="image/png",
        declared_byte_length=8,
        expires_at=datetime(2026, 9, 1, 18, 10, tzinfo=UTC),
        uploaded_at=datetime(2026, 9, 1, 17, 58, tzinfo=UTC),
        failure_code=None,
        version=4,
        updated_at=datetime(2026, 9, 1, 17, 59, tzinfo=UTC),
    )


async def _attach(session: Any, *, rights_acknowledged: bool = True):  # type: ignore[no-untyped-def]
    return await attach_sanitized_upload(
        session,
        object(),  # type: ignore[arg-type]
        upload_id=uuid4(),
        draft_id=uuid4(),
        user_id=uuid4(),
        source_draft_version=2,
        source_description="Front label",
        rights_acknowledged=rights_acknowledged,
        redaction_state=RedactionState.REVIEWED,
        now=datetime(2026, 9, 1, 18, 0, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_attach_sanitized_upload_rejects_untrusted_or_missing_inputs() -> None:
    with pytest.raises(EvidenceUploadConflictError):
        await _attach(_Session(), rights_acknowledged=False)
    with pytest.raises(EvidenceUploadNotFoundError):
        await _attach(_Session(None, None))


@pytest.mark.asyncio
async def test_attach_sanitized_upload_rejects_wrong_state_or_result() -> None:
    draft_id = uuid4()
    user_id = uuid4()
    upload_id = uuid4()
    draft = _draft(draft_id, user_id)
    draft.draft_version = 3
    with pytest.raises(EvidenceUploadConflictError):
        await _attach(_Session(draft, _upload(upload_id, draft_id, user_id)))

    upload = _upload(upload_id, draft_id, user_id)
    upload.state = EvidenceUploadState.SANITIZING.value
    with pytest.raises(EvidenceUploadConflictError):
        await _attach(_Session(_draft(draft_id, user_id), upload))

    upload = _upload(upload_id, draft_id, user_id)
    upload.sanitized_sha256 = None
    with pytest.raises(EvidenceUploadConflictError):
        await _attach(_Session(_draft(draft_id, user_id), upload))

    upload = _upload(upload_id, draft_id, user_id)
    upload.attached_evidence_id = uuid4()
    with pytest.raises(EvidenceUploadConflictError):
        await _attach(_Session(_draft(draft_id, user_id), upload))


@pytest.mark.asyncio
async def test_attach_sanitized_upload_transitions_exact_sanitized_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft_id = uuid4()
    user_id = uuid4()
    upload_id = uuid4()
    draft = _draft(draft_id, user_id)
    upload = _upload(upload_id, draft_id, user_id)
    session = _Session(draft, upload)
    created: list[object] = []

    async def create(*args: object, **kwargs: object) -> None:
        del args
        created.append(kwargs)

    monkeypatch.setattr("opennosh_api.evidence.service.create_manifest_and_enqueue", create)
    result = await attach_sanitized_upload(
        session,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        upload_id=upload_id,
        draft_id=draft_id,
        user_id=user_id,
        source_draft_version=2,
        source_description="Front label",
        rights_acknowledged=True,
        redaction_state=RedactionState.REVIEWED,
        now=datetime(2026, 9, 1, 18, 0, tzinfo=UTC),
    )

    assert result.state is EvidenceUploadState.ATTACHED
    assert upload.attached_evidence_id is not None
    assert session.commits == 1
    assert len(created) == 1
