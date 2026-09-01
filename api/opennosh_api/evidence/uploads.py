from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

EVIDENCE_UPLOAD_MAX_BYTES = 10_485_760
EVIDENCE_UPLOAD_TTL_SECONDS = 600
SUPPORTED_EVIDENCE_UPLOAD_MEDIA_TYPES = frozenset(
    {"image/jpeg", "image/png", "image/webp"}
)


class EvidenceUploadState(StrEnum):
    INITIATED = "initiated"
    UPLOADED = "uploaded"
    SANITIZING = "sanitizing"
    SANITIZED = "sanitized"
    ATTACHED = "attached"
    PRESERVED = "preserved"
    EXPIRED = "expired"
    FAILED = "failed"


class EvidenceUploadFailureCode(StrEnum):
    OBJECT_MISSING = "object_missing"
    SIZE_MISMATCH = "size_mismatch"
    SIZE_EXCEEDED = "size_exceeded"
    MEDIA_TYPE_MISMATCH = "media_type_mismatch"
    OBJECT_CHANGED = "object_changed"
    CAPABILITY_INVALID = "capability_invalid"
    EXPIRED = "expired"
    STORAGE_UNAVAILABLE = "storage_unavailable"


class EvidenceUploadPolicyError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class UploadCapability:
    value: str
    digest: str


_T34_1_TRANSITIONS = {
    EvidenceUploadState.INITIATED: frozenset(
        {
            EvidenceUploadState.UPLOADED,
            EvidenceUploadState.EXPIRED,
            EvidenceUploadState.FAILED,
        }
    )
}


def validate_upload_declaration(media_type: str, byte_length: int) -> str:
    normalized_media_type = media_type.strip().lower()
    if normalized_media_type not in SUPPORTED_EVIDENCE_UPLOAD_MEDIA_TYPES:
        raise EvidenceUploadPolicyError("media_type_unsupported")
    if not 1 <= byte_length <= EVIDENCE_UPLOAD_MAX_BYTES:
        raise EvidenceUploadPolicyError("byte_length_out_of_range")
    return normalized_media_type


def issue_upload_capability() -> UploadCapability:
    value = secrets.token_urlsafe(32)
    return UploadCapability(value=value, digest=hash_secret(value))


def hash_secret(value: str) -> str:
    if not value:
        raise EvidenceUploadPolicyError("secret_empty")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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
