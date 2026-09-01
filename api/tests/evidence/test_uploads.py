import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from opennosh_api.evidence.storage import MemoryEvidenceUploadBroker
from opennosh_api.evidence.uploads import (
    EVIDENCE_UPLOAD_MAX_BYTES,
    EvidenceUploadConflictError,
    EvidenceUploadPolicyError,
    EvidenceUploadState,
    _enforce_upload_quota,
    complete_upload_session,
    create_upload_session,
    hash_secret,
    issue_upload_capability,
    require_t34_1_transition,
    upload_request_hash,
    validate_upload_declaration,
)

USER_ID = UUID("00000000-0000-4000-8000-000000000001")
DRAFT_ID = UUID("00000000-0000-4000-8000-000000000002")


@pytest.mark.parametrize("media_type", ["image/jpeg", "image/png", "image/webp"])
def test_supported_upload_declarations_are_canonical(media_type: str) -> None:
    assert validate_upload_declaration(media_type.upper(), 1) == media_type


@pytest.mark.parametrize(
    ("media_type", "byte_length", "code"),
    [
        ("image/gif", 1, "media_type_unsupported"),
        ("image/png", 0, "byte_length_out_of_range"),
        ("image/png", EVIDENCE_UPLOAD_MAX_BYTES + 1, "byte_length_out_of_range"),
    ],
)
def test_upload_declaration_rejects_untrusted_bounds(
    media_type: str, byte_length: int, code: str
) -> None:
    with pytest.raises(EvidenceUploadPolicyError, match=code):
        validate_upload_declaration(media_type, byte_length)

    with pytest.raises(EvidenceUploadPolicyError, match="upload_max_bytes_out_of_range"):
        validate_upload_declaration("image/png", 1, max_bytes=0)


def test_upload_capability_is_opaque_and_only_its_digest_is_stable() -> None:
    first = issue_upload_capability()
    second = issue_upload_capability()

    assert first.value != second.value
    assert len(first.value) == 43
    assert first.digest == hash_secret(first.value)
    assert len(first.digest) == 64
    assert first.value not in first.digest
    with pytest.raises(EvidenceUploadPolicyError, match="secret_empty"):
        hash_secret("")


def test_upload_request_hash_is_canonical_and_scope_bound() -> None:
    first = upload_request_hash(
        user_id=USER_ID,
        draft_id=DRAFT_ID,
        source_draft_version=1,
        media_type=" IMAGE/PNG ",
        byte_length=25,
    )
    replay = upload_request_hash(
        user_id=USER_ID,
        draft_id=DRAFT_ID,
        source_draft_version=1,
        media_type="image/png",
        byte_length=25,
    )
    changed = upload_request_hash(
        user_id=USER_ID,
        draft_id=DRAFT_ID,
        source_draft_version=2,
        media_type="image/png",
        byte_length=25,
    )

    assert first == replay
    assert first != changed
    with pytest.raises(EvidenceUploadPolicyError, match="source_draft_version_invalid"):
        upload_request_hash(
            user_id=USER_ID,
            draft_id=DRAFT_ID,
            source_draft_version=0,
            media_type="image/png",
            byte_length=25,
        )


@pytest.mark.parametrize(
    "target",
    [EvidenceUploadState.UPLOADED, EvidenceUploadState.EXPIRED, EvidenceUploadState.FAILED],
)
def test_t34_1_only_allows_initiated_terminal_transitions(
    target: EvidenceUploadState,
) -> None:
    require_t34_1_transition(EvidenceUploadState.INITIATED, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (EvidenceUploadState.UPLOADED, EvidenceUploadState.SANITIZING),
        (EvidenceUploadState.EXPIRED, EvidenceUploadState.FAILED),
        (EvidenceUploadState.FAILED, EvidenceUploadState.INITIATED),
        (EvidenceUploadState.INITIATED, EvidenceUploadState.INITIATED),
    ],
)
def test_t34_1_rejects_later_or_revival_transitions(
    current: EvidenceUploadState, target: EvidenceUploadState
) -> None:
    with pytest.raises(EvidenceUploadPolicyError, match="state_transition_invalid"):
        require_t34_1_transition(current, target)


@pytest.mark.asyncio
async def test_upload_quota_configuration_must_be_ordered_and_positive() -> None:
    with pytest.raises(EvidenceUploadPolicyError, match="upload_quota_out_of_range"):
        await _enforce_upload_quota(  # type: ignore[arg-type]
            object(),
            user_id=USER_ID,
            draft_id=DRAFT_ID,
            account_limit=1,
            draft_limit=2,
        )


class _ScalarResult:
    def __init__(self, value: object | None) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object | None:
        return self.value


class _Database:
    def __init__(self, results: list[object | None] | None = None) -> None:
        self.results = list(results or [])
        self.rollbacks = 0

    async def execute(self, statement: object) -> _ScalarResult:
        del statement
        return _ScalarResult(self.results.pop(0))

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def commit(self) -> None:
        return None


def _draft(version: int = 1) -> SimpleNamespace:
    return SimpleNamespace(draft_version=version, review_state="draft")


def _record(
    *,
    state: EvidenceUploadState,
    now: datetime,
    capability: str,
) -> SimpleNamespace:
    upload_id = uuid4()
    return SimpleNamespace(
        id=upload_id,
        draft_id=DRAFT_ID,
        user_id=USER_ID,
        source_draft_version=1,
        state=state.value,
        object_key=f"quarantine/{upload_id}",
        declared_media_type="image/png",
        declared_byte_length=8,
        observed_byte_length=8 if state is EvidenceUploadState.UPLOADED else None,
        observed_sha256="a" * 64 if state is EvidenceUploadState.UPLOADED else None,
        capability_hash=hash_secret(capability),
        expires_at=now + timedelta(minutes=5),
        uploaded_at=now if state is EvidenceUploadState.UPLOADED else None,
        failure_code=None,
        attached_evidence_id=None,
        sanitized_at=None,
        attached_at=None,
        preserved_at=None,
        version=2 if state is EvidenceUploadState.UPLOADED else 1,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_upload_creation_rechecks_draft_after_provider_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _Database([None])
    drafts = [_draft(1), _draft(2)]

    async def owned(*args: object, **kwargs: object) -> SimpleNamespace:
        del args, kwargs
        return drafts.pop(0)

    async def lock(*args: object, **kwargs: object) -> None:
        del args, kwargs

    monkeypatch.setattr("opennosh_api.evidence.uploads._owned_draft", owned)
    monkeypatch.setattr("opennosh_api.evidence.uploads._lock_upload_quota", lock)
    with pytest.raises(EvidenceUploadConflictError):
        await create_upload_session(
            database,  # type: ignore[arg-type]
            MemoryEvidenceUploadBroker(),
            draft_id=DRAFT_ID,
            user_id=USER_ID,
            source_draft_version=1,
            media_type="image/png",
            byte_length=8,
            idempotency_key="stale-after-provider",
            now=datetime(2026, 9, 1, 18, 0, tzinfo=UTC),
        )
    assert database.rollbacks == 2


@pytest.mark.asyncio
async def test_upload_creation_detects_idempotent_winner_after_provider_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 9, 1, 18, 0, tzinfo=UTC)
    request_hash = upload_request_hash(
        user_id=USER_ID,
        draft_id=DRAFT_ID,
        source_draft_version=1,
        media_type="image/png",
        byte_length=8,
    )
    existing = _record(state=EvidenceUploadState.INITIATED, now=now, capability="x" * 43)
    existing.request_hash = request_hash
    database = _Database([None, existing])

    async def owned(*args: object, **kwargs: object) -> SimpleNamespace:
        del args, kwargs
        return _draft(1)

    async def lock(*args: object, **kwargs: object) -> None:
        del args, kwargs

    monkeypatch.setattr("opennosh_api.evidence.uploads._owned_draft", owned)
    monkeypatch.setattr("opennosh_api.evidence.uploads._lock_upload_quota", lock)
    created = await create_upload_session(
        database,  # type: ignore[arg-type]
        MemoryEvidenceUploadBroker(),
        draft_id=DRAFT_ID,
        user_id=USER_ID,
        source_draft_version=1,
        media_type="image/png",
        byte_length=8,
        idempotency_key="idempotent-winner",
        now=now,
    )
    assert created.replayed is True
    assert created.session.upload_id == existing.id


@pytest.mark.asyncio
async def test_upload_creation_rejects_conflicting_winner_after_provider_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 9, 1, 18, 0, tzinfo=UTC)
    existing = _record(state=EvidenceUploadState.INITIATED, now=now, capability="x" * 43)
    existing.request_hash = "0" * 64
    database = _Database([None, existing])

    async def owned(*args: object, **kwargs: object) -> SimpleNamespace:
        del args, kwargs
        return _draft(1)

    async def lock(*args: object, **kwargs: object) -> None:
        del args, kwargs

    monkeypatch.setattr("opennosh_api.evidence.uploads._owned_draft", owned)
    monkeypatch.setattr("opennosh_api.evidence.uploads._lock_upload_quota", lock)
    with pytest.raises(EvidenceUploadConflictError):
        await create_upload_session(
            database,  # type: ignore[arg-type]
            MemoryEvidenceUploadBroker(),
            draft_id=DRAFT_ID,
            user_id=USER_ID,
            source_draft_version=1,
            media_type="image/png",
            byte_length=8,
            idempotency_key="conflicting-winner",
            now=now,
        )
    assert database.rollbacks == 2


@pytest.mark.asyncio
async def test_completion_returns_concurrent_uploaded_state_inside_expiry_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 9, 1, 18, 0, tzinfo=UTC)
    capability = "c" * 43
    initial = _record(state=EvidenceUploadState.INITIATED, now=now, capability=capability)
    initial.expires_at = now
    uploaded = _record(state=EvidenceUploadState.UPLOADED, now=now, capability=capability)
    uploaded.id = initial.id
    uploads = [initial, uploaded]

    async def owned_upload(*args: object, **kwargs: object) -> SimpleNamespace:
        del args, kwargs
        return uploads.pop(0)

    monkeypatch.setattr("opennosh_api.evidence.uploads._owned_upload", owned_upload)
    result = await complete_upload_session(
        _Database(),  # type: ignore[arg-type]
        MemoryEvidenceUploadBroker(),
        upload_id=initial.id,
        draft_id=DRAFT_ID,
        user_id=USER_ID,
        completion_capability=capability,
        now=now,
    )
    assert result.state is EvidenceUploadState.UPLOADED


@pytest.mark.asyncio
async def test_completion_bounds_observation_with_semaphore_and_accepts_concurrent_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 9, 1, 18, 0, tzinfo=UTC)
    capability = "c" * 43
    initial = _record(state=EvidenceUploadState.INITIATED, now=now, capability=capability)
    uploaded = _record(state=EvidenceUploadState.UPLOADED, now=now, capability=capability)
    uploaded.id = initial.id
    uploads = [initial, uploaded]
    drafts = [_draft(1), _draft(1)]

    async def owned_upload(*args: object, **kwargs: object) -> SimpleNamespace:
        del args, kwargs
        return uploads.pop(0)

    async def owned_draft(*args: object, **kwargs: object) -> SimpleNamespace:
        del args, kwargs
        return drafts.pop(0)

    monkeypatch.setattr("opennosh_api.evidence.uploads._owned_upload", owned_upload)
    monkeypatch.setattr("opennosh_api.evidence.uploads._owned_draft", owned_draft)
    broker = MemoryEvidenceUploadBroker()
    broker.put_for_test(initial.object_key, b"evidence", media_type="image/png")
    result = await complete_upload_session(
        _Database(),  # type: ignore[arg-type]
        broker,
        upload_id=initial.id,
        draft_id=DRAFT_ID,
        user_id=USER_ID,
        completion_capability=capability,
        now=now,
        observation_semaphore=asyncio.Semaphore(1),
    )
    assert result.state is EvidenceUploadState.UPLOADED
    assert broker.operations.count(("observe", initial.object_key)) == 2
