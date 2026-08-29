from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, field_validator

_DOMAIN = b"opennosh:federation-release:1.0\0"
_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_PACK_ID = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,158}[a-z0-9])?$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")


class FederationLifecycleState(StrEnum):
    REQUESTED = "requested"
    VERIFIED = "verified"
    ACTIVE = "active"
    QUARANTINED = "quarantined"
    REVOKED = "revoked"


class FederationEventType(StrEnum):
    INVITATION_CREATED = "invitation_created"
    INVITATION_CONSUMED = "invitation_consumed"
    MAINTAINER_REQUESTED = "maintainer_requested"
    MAINTAINER_VERIFIED = "maintainer_verified"
    MAINTAINER_ACTIVATED = "maintainer_activated"
    RELEASE_PUBLISHED = "release_published"
    ROLE_KEY_ROTATED = "role_key_rotated"
    MAINTAINER_QUARANTINED = "maintainer_quarantined"
    MAINTAINER_REVOKED = "maintainer_revoked"
    ADMIN_ATTEMPT_REJECTED = "admin_attempt_rejected"


class FederationScope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    github_account_id: int = Field(gt=0)
    github_login: str = Field(min_length=1, max_length=100)
    repository_id: int = Field(gt=0)
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
    pack_id: str = Field(pattern=r"^[a-z0-9](?:[a-z0-9-]{0,158}[a-z0-9])?$")

    @field_validator("github_login")
    @classmethod
    def validate_login(cls, value: str) -> str:
        if value != value.strip() or any(character.isspace() for character in value):
            raise ValueError("GitHub login must not contain whitespace")
        return value


class InvitationSecret(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    invitation_id: UUID
    token: str = Field(min_length=32, max_length=256)
    expires_at: datetime


class MaintainerStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    maintainer_id: UUID
    state: FederationLifecycleState
    github_account_id: int
    github_login: str
    repository_id: int
    repository: str
    pack_id: str
    current_role_key_id: str
    current_role_key_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    requested_at: datetime
    verified_at: datetime | None
    activated_at: datetime | None
    quarantined_at: datetime | None
    revoked_at: datetime | None


class FederationReleaseStatement(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    maintainer_id: UUID
    repository_id: int = Field(gt=0)
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
    pack_id: str = Field(pattern=r"^[a-z0-9](?:[a-z0-9-]{0,158}[a-z0-9])?$")
    publication_id: UUID
    release_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    receipt_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    public_url: str = Field(min_length=1, max_length=2048)
    issued_at: datetime
    key_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

    @field_validator("issued_at")
    @classmethod
    def require_aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Federation release time must include a timezone")
        return value

    @field_validator("public_url")
    @classmethod
    def require_canonical_https_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or any(character.isspace() for character in value)
        ):
            raise ValueError("Federation public URL must be a canonical HTTPS URL")
        return value


class SignedFederationRelease(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    statement: FederationReleaseStatement
    signature: str = Field(pattern=r"^[A-Za-z0-9_-]{86}$")


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def release_signature_material(statement: FederationReleaseStatement) -> bytes:
    return _DOMAIN + canonical_json(statement.model_dump(mode="json"))


def release_statement_digest(statement: FederationReleaseStatement) -> str:
    return hashlib.sha256(canonical_json(statement.model_dump(mode="json"))).hexdigest()


def encode_public_key(key: Ed25519PublicKey) -> str:
    raw = key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def public_key_fingerprint(encoded_public_key: str) -> str:
    key = decode_public_key(encoded_public_key)
    raw = key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return hashlib.sha256(raw).hexdigest()


def decode_public_key(value: str) -> Ed25519PublicKey:
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (TypeError, ValueError) as error:
        raise ValueError("Federation public key encoding is invalid") from error
    if len(raw) != 32:
        raise ValueError("Federation public key must contain 32 bytes")
    return Ed25519PublicKey.from_public_bytes(raw)


def load_public_key(path: Path) -> tuple[str, str]:
    payload = path.read_bytes()
    try:
        loaded = serialization.load_pem_public_key(payload)
    except ValueError:
        try:
            encoded = payload.decode("ascii").strip()
        except UnicodeDecodeError as error:
            raise ValueError("Federation public key file is invalid") from error
        key = decode_public_key(encoded)
    else:
        if not isinstance(loaded, Ed25519PublicKey):
            raise ValueError("Federation role key must be Ed25519")
        key = loaded
    encoded = encode_public_key(key)
    return encoded, public_key_fingerprint(encoded)


def verify_release_signature(
    release: SignedFederationRelease,
    *,
    encoded_public_key: str,
) -> None:
    key = decode_public_key(encoded_public_key)
    try:
        signature = base64.b64decode(
            release.signature + "=" * (-len(release.signature) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (TypeError, ValueError) as error:
        raise ValueError("Federation release signature encoding is invalid") from error
    if len(signature) != 64:
        raise ValueError("Federation release signature must contain 64 bytes")
    try:
        key.verify(signature, release_signature_material(release.statement))
    except InvalidSignature as error:
        raise ValueError("Federation release signature is invalid") from error


def validate_key_id(value: str) -> str:
    if not _KEY_ID.fullmatch(value):
        raise ValueError("Federation role key ID is invalid")
    return value


def validate_scope_labels(repository: str, pack_id: str) -> None:
    if not _REPOSITORY.fullmatch(repository):
        raise ValueError("Federation repository name is invalid")
    if not _PACK_ID.fullmatch(pack_id):
        raise ValueError("Federation pack ID is invalid")
