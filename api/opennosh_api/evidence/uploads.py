from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.contributions.models import ContributionDraft
from opennosh_api.evidence.models import EvidenceUploadSession
from opennosh_api.evidence.storage import (
    EvidenceUploadBroker,
    EvidenceUploadInstruction,
    EvidenceUploadObjectTooLargeError,
    EvidenceUploadStorageError,
    QuarantinedEvidenceObservation,
)
from opennosh_api.jobs import JobLane, JobMessage, JobQueue, JobRequest

EVIDENCE_UPLOAD_MAX_BYTES = 10_485_760
EVIDENCE_UPLOAD_TTL_SECONDS = 600
EVIDENCE_UPLOAD_OUTSTANDING_ACCOUNT_LIMIT = 5
EVIDENCE_UPLOAD_OUTSTANDING_DRAFT_LIMIT = 2
SUPPORTED_EVIDENCE_UPLOAD_MEDIA_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})


class EvidenceUploadState(StrEnum):
    INITIATED = "initiated"
    UPLOADED = "uploaded"
    SANITIZING = "sanitizing"
    SANITIZED = "sanitized"
    ATTACHED = "attached"
    PRESERVED = "preserved"
    EXPIRED = "expired"
    FAILED = "failed"


_OUTSTANDING_STATES = (
    EvidenceUploadState.INITIATED,
    EvidenceUploadState.UPLOADED,
    EvidenceUploadState.SANITIZING,
    EvidenceUploadState.SANITIZED,
    EvidenceUploadState.ATTACHED,
)


class EvidenceUploadFailureCode(StrEnum):
    OBJECT_MISSING = "object_missing"
    SIZE_MISMATCH = "size_mismatch"
    SIZE_EXCEEDED = "size_exceeded"
    MEDIA_TYPE_MISMATCH = "media_type_mismatch"
    OBJECT_CHANGED = "object_changed"
    CAPABILITY_INVALID = "capability_invalid"
    EXPIRED = "expired"
    STORAGE_UNAVAILABLE = "storage_unavailable"
    SIGNATURE_MISMATCH = "signature_mismatch"
    DECODE_FAILED = "decode_failed"
    PIXEL_LIMIT_EXCEEDED = "pixel_limit_exceeded"
    ANIMATION_UNSUPPORTED = "animation_unsupported"
    METADATA_REWRITE_FAILED = "metadata_rewrite_failed"
    SANITIZED_SIZE_EXCEEDED = "sanitized_size_exceeded"
    MALWARE_DETECTED = "malware_detected"
    SCANNER_UNAVAILABLE = "scanner_unavailable"
    SANITIZED_STORAGE_UNAVAILABLE = "sanitized_storage_unavailable"
    SANITIZED_STORAGE_CONFLICT = "sanitized_storage_conflict"


class EvidenceUploadPolicyError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class UploadCapability:
    value: str
    digest: str


@dataclass(frozen=True, slots=True)
class EvidenceUploadSessionView:
    upload_id: UUID
    draft_id: UUID
    source_draft_version: int
    state: EvidenceUploadState
    declared_media_type: str
    declared_byte_length: int
    observed_byte_length: int | None
    observed_sha256: str | None
    expires_at: datetime
    uploaded_at: datetime | None
    failure_code: EvidenceUploadFailureCode | None
    evidence_id: UUID | None
    sanitized_at: datetime | None
    attached_at: datetime | None
    preserved_at: datetime | None


@dataclass(frozen=True, slots=True)
class EvidenceUploadCreation:
    session: EvidenceUploadSessionView
    instruction: EvidenceUploadInstruction | None
    completion_capability: str | None
    replayed: bool


class EvidenceUploadNotFoundError(Exception):
    pass


class EvidenceUploadConflictError(Exception):
    pass


class EvidenceUploadExpiredError(Exception):
    pass


class EvidenceUploadUnavailableError(Exception):
    pass


class EvidenceUploadQuotaError(Exception):
    pass


_T34_1_TRANSITIONS = {
    EvidenceUploadState.INITIATED: frozenset(
        {
            EvidenceUploadState.UPLOADED,
            EvidenceUploadState.EXPIRED,
            EvidenceUploadState.FAILED,
        }
    )
}


def validate_upload_declaration(
    media_type: str,
    byte_length: int,
    *,
    max_bytes: int = EVIDENCE_UPLOAD_MAX_BYTES,
) -> str:
    normalized_media_type = media_type.strip().lower()
    if normalized_media_type not in SUPPORTED_EVIDENCE_UPLOAD_MEDIA_TYPES:
        raise EvidenceUploadPolicyError("media_type_unsupported")
    if not 1 <= max_bytes <= EVIDENCE_UPLOAD_MAX_BYTES:
        raise EvidenceUploadPolicyError("upload_max_bytes_out_of_range")
    if not 1 <= byte_length <= max_bytes:
        raise EvidenceUploadPolicyError("byte_length_out_of_range")
    return normalized_media_type


def issue_upload_capability() -> UploadCapability:
    value = secrets.token_urlsafe(32)
    return UploadCapability(value=value, digest=hash_secret(value))


def hash_secret(value: str) -> str:
    if not value:
        raise EvidenceUploadPolicyError("secret_empty")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def upload_session_view(record: EvidenceUploadSession) -> EvidenceUploadSessionView:
    failure = (
        None if record.failure_code is None else EvidenceUploadFailureCode(record.failure_code)
    )
    return EvidenceUploadSessionView(
        upload_id=record.id,
        draft_id=record.draft_id,
        source_draft_version=record.source_draft_version,
        state=EvidenceUploadState(record.state),
        declared_media_type=record.declared_media_type,
        declared_byte_length=record.declared_byte_length,
        observed_byte_length=record.observed_byte_length,
        observed_sha256=record.observed_sha256,
        expires_at=record.expires_at,
        uploaded_at=record.uploaded_at,
        failure_code=failure,
        evidence_id=record.attached_evidence_id,
        sanitized_at=record.sanitized_at,
        attached_at=record.attached_at,
        preserved_at=record.preserved_at,
    )


async def _owned_draft(
    database: AsyncSession,
    *,
    draft_id: UUID,
    user_id: UUID,
    for_update: bool = False,
) -> ContributionDraft:
    statement = select(ContributionDraft).where(
        ContributionDraft.id == draft_id,
        ContributionDraft.user_id == user_id,
    )
    if for_update:
        statement = statement.with_for_update()
    draft = (await database.execute(statement)).scalar_one_or_none()
    if draft is None:
        raise EvidenceUploadNotFoundError
    return draft


async def _owned_upload(
    database: AsyncSession,
    *,
    upload_id: UUID,
    draft_id: UUID,
    user_id: UUID,
    for_update: bool = False,
) -> EvidenceUploadSession:
    statement = select(EvidenceUploadSession).where(
        EvidenceUploadSession.id == upload_id,
        EvidenceUploadSession.draft_id == draft_id,
        EvidenceUploadSession.user_id == user_id,
    )
    if for_update:
        statement = statement.with_for_update()
    record = (await database.execute(statement)).scalar_one_or_none()
    if record is None:
        raise EvidenceUploadNotFoundError
    return record


def _expire(record: EvidenceUploadSession, *, now: datetime) -> None:
    require_t34_1_transition(EvidenceUploadState(record.state), EvidenceUploadState.EXPIRED)
    record.state = EvidenceUploadState.EXPIRED.value
    record.version += 1
    record.updated_at = now


def _fail(
    record: EvidenceUploadSession,
    code: EvidenceUploadFailureCode,
    *,
    now: datetime,
) -> None:
    require_t34_1_transition(EvidenceUploadState(record.state), EvidenceUploadState.FAILED)
    record.state = EvidenceUploadState.FAILED.value
    record.failure_code = code.value
    record.failed_at = now
    record.version += 1
    record.updated_at = now


async def create_upload_session(
    database: AsyncSession,
    broker: EvidenceUploadBroker,
    *,
    draft_id: UUID,
    user_id: UUID,
    source_draft_version: int,
    media_type: str,
    byte_length: int,
    idempotency_key: str,
    now: datetime,
    ttl_seconds: int = EVIDENCE_UPLOAD_TTL_SECONDS,
    max_bytes: int = EVIDENCE_UPLOAD_MAX_BYTES,
    outstanding_account_limit: int = EVIDENCE_UPLOAD_OUTSTANDING_ACCOUNT_LIMIT,
    outstanding_draft_limit: int = EVIDENCE_UPLOAD_OUTSTANDING_DRAFT_LIMIT,
) -> EvidenceUploadCreation:
    if not 1 <= ttl_seconds <= EVIDENCE_UPLOAD_TTL_SECONDS:
        raise EvidenceUploadPolicyError("upload_ttl_out_of_range")
    normalized_media_type = validate_upload_declaration(
        media_type, byte_length, max_bytes=max_bytes
    )
    idempotency_hash = hash_secret(idempotency_key)
    request_digest = upload_request_hash(
        user_id=user_id,
        draft_id=draft_id,
        source_draft_version=source_draft_version,
        media_type=normalized_media_type,
        byte_length=byte_length,
    )
    existing = (
        await database.execute(
            select(EvidenceUploadSession).where(
                EvidenceUploadSession.user_id == user_id,
                EvidenceUploadSession.draft_id == draft_id,
                EvidenceUploadSession.idempotency_key_hash == idempotency_hash,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.request_hash != request_digest:
            raise EvidenceUploadConflictError
        return EvidenceUploadCreation(
            session=upload_session_view(existing),
            instruction=None,
            completion_capability=None,
            replayed=True,
        )

    draft = await _owned_draft(database, draft_id=draft_id, user_id=user_id)
    if draft.draft_version != source_draft_version or draft.review_state not in {
        "draft",
        "in_review",
        "changes_requested",
    }:
        raise EvidenceUploadConflictError
    await database.rollback()

    upload_id = uuid4()
    capability = issue_upload_capability()
    expires_at = now + timedelta(seconds=ttl_seconds)
    object_key = f"quarantine/{upload_id}"
    try:
        instruction = await broker.create_upload(
            object_key,
            media_type=normalized_media_type,
            byte_length=byte_length,
            expires_at=expires_at,
            expires_in_seconds=ttl_seconds,
        )
    except EvidenceUploadStorageError as error:
        await database.rollback()
        raise EvidenceUploadUnavailableError from error
    await _lock_upload_quota(database, user_id=user_id, draft_id=draft_id)
    draft = await _owned_draft(database, draft_id=draft_id, user_id=user_id, for_update=True)
    if draft.draft_version != source_draft_version or draft.review_state not in {
        "draft",
        "in_review",
        "changes_requested",
    }:
        await database.rollback()
        raise EvidenceUploadConflictError
    existing = (
        await database.execute(
            select(EvidenceUploadSession).where(
                EvidenceUploadSession.user_id == user_id,
                EvidenceUploadSession.draft_id == draft_id,
                EvidenceUploadSession.idempotency_key_hash == idempotency_hash,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.request_hash != request_digest:
            await database.rollback()
            raise EvidenceUploadConflictError
        await database.rollback()
        return EvidenceUploadCreation(
            session=upload_session_view(existing),
            instruction=None,
            completion_capability=None,
            replayed=True,
        )
    try:
        await _enforce_upload_quota(
            database,
            user_id=user_id,
            draft_id=draft_id,
            account_limit=outstanding_account_limit,
            draft_limit=outstanding_draft_limit,
        )
    except EvidenceUploadQuotaError:
        await database.rollback()
        raise
    record = EvidenceUploadSession(
        id=upload_id,
        user_id=user_id,
        draft_id=draft_id,
        source_draft_version=source_draft_version,
        object_key=object_key,
        declared_media_type=normalized_media_type,
        declared_byte_length=byte_length,
        capability_hash=capability.digest,
        idempotency_key_hash=idempotency_hash,
        request_hash=request_digest,
        expires_at=expires_at,
        created_at=now,
        updated_at=now,
    )
    database.add(record)
    try:
        await database.commit()
    except IntegrityError:
        await database.rollback()
        existing = (
            await database.execute(
                select(EvidenceUploadSession).where(
                    EvidenceUploadSession.user_id == user_id,
                    EvidenceUploadSession.draft_id == draft_id,
                    EvidenceUploadSession.idempotency_key_hash == idempotency_hash,
                )
            )
        ).scalar_one_or_none()
        if existing is None or existing.request_hash != request_digest:
            raise EvidenceUploadConflictError from None
        return EvidenceUploadCreation(
            session=upload_session_view(existing),
            instruction=None,
            completion_capability=None,
            replayed=True,
        )
    return EvidenceUploadCreation(
        session=upload_session_view(record),
        instruction=instruction,
        completion_capability=capability.value,
        replayed=False,
    )


def _observations_match(
    first: QuarantinedEvidenceObservation,
    second: QuarantinedEvidenceObservation,
) -> bool:
    return (
        first.object_key,
        first.media_type,
        first.size_bytes,
        first.content_digest,
        first.revision,
    ) == (
        second.object_key,
        second.media_type,
        second.size_bytes,
        second.content_digest,
        second.revision,
    )


async def complete_upload_session(
    database: AsyncSession,
    broker: EvidenceUploadBroker,
    *,
    upload_id: UUID,
    draft_id: UUID,
    user_id: UUID,
    completion_capability: str,
    now: datetime,
    max_bytes: int = EVIDENCE_UPLOAD_MAX_BYTES,
    queue: JobQueue | None = None,
    observation_semaphore: asyncio.Semaphore | None = None,
) -> EvidenceUploadSessionView:
    record = await _owned_upload(database, upload_id=upload_id, draft_id=draft_id, user_id=user_id)
    if not hmac.compare_digest(record.capability_hash, hash_secret(completion_capability)):
        raise EvidenceUploadNotFoundError
    state = EvidenceUploadState(record.state)
    if state is EvidenceUploadState.UPLOADED:
        return upload_session_view(record)
    if state is EvidenceUploadState.EXPIRED:
        raise EvidenceUploadExpiredError
    if state is not EvidenceUploadState.INITIATED:
        raise EvidenceUploadConflictError
    if now >= record.expires_at:
        locked = await _owned_upload(
            database,
            upload_id=upload_id,
            draft_id=draft_id,
            user_id=user_id,
            for_update=True,
        )
        locked_state = EvidenceUploadState(locked.state)
        if locked_state is EvidenceUploadState.UPLOADED:
            return upload_session_view(locked)
        if locked_state is EvidenceUploadState.EXPIRED:
            raise EvidenceUploadExpiredError
        if locked_state is not EvidenceUploadState.INITIATED:
            raise EvidenceUploadConflictError
        _expire(locked, now=now)
        await database.commit()
        raise EvidenceUploadExpiredError

    draft = await _owned_draft(database, draft_id=draft_id, user_id=user_id)
    if draft.draft_version != record.source_draft_version:
        raise EvidenceUploadConflictError
    snapshot_version = record.version
    object_key = record.object_key
    await database.rollback()
    try:
        if observation_semaphore is None:
            first = await broker.observe(object_key, max_bytes=max_bytes)
            second = await broker.observe(object_key, max_bytes=max_bytes)
        else:
            async with observation_semaphore:
                first = await broker.observe(object_key, max_bytes=max_bytes)
                second = await broker.observe(object_key, max_bytes=max_bytes)
    except EvidenceUploadObjectTooLargeError:
        draft = await _owned_draft(database, draft_id=draft_id, user_id=user_id, for_update=True)
        locked = await _owned_upload(
            database,
            upload_id=upload_id,
            draft_id=draft_id,
            user_id=user_id,
            for_update=True,
        )
        if locked.version != snapshot_version or locked.state != EvidenceUploadState.INITIATED:
            raise EvidenceUploadConflictError from None
        if draft.draft_version != locked.source_draft_version:
            raise EvidenceUploadConflictError from None
        _fail(locked, EvidenceUploadFailureCode.SIZE_EXCEEDED, now=now)
        await database.commit()
        raise EvidenceUploadConflictError from None
    except EvidenceUploadStorageError as error:
        await database.rollback()
        raise EvidenceUploadUnavailableError from error

    draft = await _owned_draft(database, draft_id=draft_id, user_id=user_id, for_update=True)
    locked = await _owned_upload(
        database,
        upload_id=upload_id,
        draft_id=draft_id,
        user_id=user_id,
        for_update=True,
    )
    if EvidenceUploadState(locked.state) is EvidenceUploadState.UPLOADED:
        return upload_session_view(locked)
    if locked.version != snapshot_version or locked.state != EvidenceUploadState.INITIATED.value:
        raise EvidenceUploadConflictError
    if draft.draft_version != locked.source_draft_version:
        raise EvidenceUploadConflictError
    failure: EvidenceUploadFailureCode | None = None
    if first is None or second is None:
        failure = EvidenceUploadFailureCode.OBJECT_MISSING
    elif not _observations_match(first, second):
        failure = EvidenceUploadFailureCode.OBJECT_CHANGED
    elif first.size_bytes > max_bytes:
        failure = EvidenceUploadFailureCode.SIZE_EXCEEDED
    elif first.size_bytes != locked.declared_byte_length:
        failure = EvidenceUploadFailureCode.SIZE_MISMATCH
    elif first.media_type != locked.declared_media_type:
        failure = EvidenceUploadFailureCode.MEDIA_TYPE_MISMATCH
    if failure is not None:
        _fail(locked, failure, now=now)
        await database.commit()
        raise EvidenceUploadConflictError
    assert first is not None
    require_t34_1_transition(EvidenceUploadState.INITIATED, EvidenceUploadState.UPLOADED)
    locked.state = EvidenceUploadState.UPLOADED.value
    locked.observed_byte_length = first.size_bytes
    locked.observed_sha256 = first.content_digest
    locked.observed_revision_sha256 = hashlib.sha256(
        first.revision.encode("utf-8")
    ).hexdigest()
    locked.uploaded_at = now
    locked.version += 1
    locked.updated_at = now
    if queue is not None:
        connection = await database.connection()
        await queue.enqueue(
            connection,
            sanitization_request(
                locked.id,
                workflow_revision=locked.version,
                run_after=now,
            ),
        )
    await database.commit()
    return upload_session_view(locked)


async def get_upload_session(
    database: AsyncSession,
    *,
    upload_id: UUID,
    draft_id: UUID,
    user_id: UUID,
    now: datetime,
) -> EvidenceUploadSessionView:
    record = await _owned_upload(
        database,
        upload_id=upload_id,
        draft_id=draft_id,
        user_id=user_id,
        for_update=True,
    )
    if (
        EvidenceUploadState(record.state) is EvidenceUploadState.INITIATED
        and now >= record.expires_at
    ):
        _expire(record, now=now)
        await database.commit()
    return upload_session_view(record)


async def _lock_upload_quota(
    database: AsyncSession,
    *,
    user_id: UUID,
    draft_id: UUID,
) -> None:
    for key in (f"evidence-upload:{user_id}", f"evidence-upload:{user_id}:{draft_id}"):
        await database.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": key},
        )


async def _enforce_upload_quota(
    database: AsyncSession,
    *,
    user_id: UUID,
    draft_id: UUID,
    account_limit: int,
    draft_limit: int,
) -> None:
    if account_limit < 1 or draft_limit < 1 or draft_limit > account_limit:
        raise EvidenceUploadPolicyError("upload_quota_out_of_range")
    states = tuple(state.value for state in _OUTSTANDING_STATES)
    account_count = await database.scalar(
        select(func.count())
        .select_from(EvidenceUploadSession)
        .where(
            EvidenceUploadSession.user_id == user_id,
            EvidenceUploadSession.state.in_(states),
        )
    )
    draft_count = await database.scalar(
        select(func.count())
        .select_from(EvidenceUploadSession)
        .where(
            EvidenceUploadSession.user_id == user_id,
            EvidenceUploadSession.draft_id == draft_id,
            EvidenceUploadSession.state.in_(states),
        )
    )
    if int(account_count or 0) >= account_limit or int(draft_count or 0) >= draft_limit:
        raise EvidenceUploadQuotaError


def upload_request_hash(
    *,
    user_id: UUID,
    draft_id: UUID,
    source_draft_version: int,
    media_type: str,
    byte_length: int,
) -> str:
    if source_draft_version < 1:
        raise EvidenceUploadPolicyError("source_draft_version_invalid")
    normalized_media_type = validate_upload_declaration(media_type, byte_length)
    canonical = json.dumps(
        {
            "byte_length": byte_length,
            "draft_id": str(draft_id),
            "media_type": normalized_media_type,
            "source_draft_version": source_draft_version,
            "user_id": str(user_id),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def require_t34_1_transition(
    current: EvidenceUploadState,
    target: EvidenceUploadState,
) -> None:
    if target not in _T34_1_TRANSITIONS.get(current, frozenset()):
        raise EvidenceUploadPolicyError("state_transition_invalid")


def sanitization_request(
    upload_id: UUID,
    *,
    workflow_revision: int,
    run_after: datetime,
) -> JobRequest:
    key = f"evidence-sanitize:{upload_id}:{workflow_revision}"
    return JobRequest(
        message=JobMessage(
            lane=JobLane.EVIDENCE,
            job_type="evidence.sanitize",
            subject_id=upload_id,
            workflow_revision=workflow_revision,
            idempotency_key=key,
        ),
        run_after=run_after,
        priority=9,
        deduplication_key=key,
    )
