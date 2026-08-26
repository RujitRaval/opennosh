from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, TypeAlias
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,2047}$")


class EvidenceClass(StrEnum):
    SANITIZED_MEDIA = "sanitized_media"
    VERSIONED_PUBLIC_DATASET = "versioned_public_dataset"
    PUBLIC_DOCUMENT = "public_document"
    MAINTAINER_ATTESTATION = "maintainer_attestation"


class EvidencePublicState(StrEnum):
    EVIDENCE_PRESERVED = "evidence_preserved"
    SOURCE_VERIFIED = "source_verified"
    REFERENCE_PRESERVED = "reference_preserved"
    REFERENCE_ONLY = "reference_only"
    ATTESTED = "attested"
    TOMBSTONED = "tombstoned"


class EvidenceAcknowledgementKind(StrEnum):
    IMMUTABLE_SANITIZED_COPY = "immutable_sanitized_copy"
    DATASET_SNAPSHOT = "dataset_snapshot"
    SIGNED_DATASET_MANIFEST = "signed_dataset_manifest"
    ARCHIVED_DOCUMENT = "archived_document"
    CITATION_MANIFEST = "citation_manifest"
    SIGNED_ATTESTATION = "signed_attestation"


class RedactionState(StrEnum):
    NOT_REQUIRED = "not_required"
    APPLIED = "applied"
    REVIEWED = "reviewed"


class DocumentRightsState(StrEnum):
    ARCHIVE_PERMITTED = "archive_permitted"
    REFERENCE_ONLY = "reference_only"


class _ManifestBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    evidence_id: UUID


class SanitizedMediaManifest(_ManifestBase):
    evidence_class: Literal[EvidenceClass.SANITIZED_MEDIA] = EvidenceClass.SANITIZED_MEDIA
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    safe_format: Literal["image/jpeg", "image/png", "image/webp"]
    source_description: str = Field(min_length=1, max_length=1000)
    rights_acknowledged: Literal[True]
    redaction_state: RedactionState
    storage_reference: str = Field(min_length=1, max_length=2048)

    @field_validator("storage_reference")
    @classmethod
    def validate_storage_reference(cls, value: str) -> str:
        return _safe_reference(value, "Media storage reference")


class VersionedPublicDatasetManifest(_ManifestBase):
    evidence_class: Literal[EvidenceClass.VERSIONED_PUBLIC_DATASET] = (
        EvidenceClass.VERSIONED_PUBLIC_DATASET
    )
    dataset_id: str = Field(min_length=1, max_length=255)
    release_version: str = Field(min_length=1, max_length=255)
    record_id: str = Field(min_length=1, max_length=255)
    publisher: str = Field(min_length=1, max_length=500)
    license: str = Field(min_length=1, max_length=160)
    source_uri: str = Field(min_length=1, max_length=2048)
    canonical_record_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    signature_key_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    signature: str = Field(pattern=r"^[A-Za-z0-9_-]{86}$")
    archival_permitted: bool
    storage_reference: str | None = Field(default=None, max_length=2048)

    @field_validator("source_uri")
    @classmethod
    def validate_source_uri(cls, value: str) -> str:
        return _public_https_url(value, "Dataset source URI")

    @model_validator(mode="after")
    def require_storage_when_archivable(self) -> VersionedPublicDatasetManifest:
        if self.archival_permitted and self.storage_reference is None:
            raise ValueError("Archivable datasets require a storage reference")
        if self.storage_reference is not None:
            _safe_reference(self.storage_reference, "Dataset storage reference")
        return self


class PublicDocumentManifest(_ManifestBase):
    evidence_class: Literal[EvidenceClass.PUBLIC_DOCUMENT] = EvidenceClass.PUBLIC_DOCUMENT
    canonical_uri: str = Field(min_length=1, max_length=2048)
    publisher: str = Field(min_length=1, max_length=500)
    license: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=1000)
    observed_at: datetime
    observed_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    rights_state: DocumentRightsState
    storage_reference: str | None = Field(default=None, max_length=2048)

    @field_validator("canonical_uri")
    @classmethod
    def validate_canonical_uri(cls, value: str) -> str:
        return _public_https_url(value, "Document canonical URI")

    @field_validator("observed_at")
    @classmethod
    def require_aware_observed_at(cls, value: datetime) -> datetime:
        return _aware(value, "Document observation time")

    @model_validator(mode="after")
    def enforce_rights_boundary(self) -> PublicDocumentManifest:
        if (
            self.rights_state is DocumentRightsState.ARCHIVE_PERMITTED
            and self.storage_reference is None
        ):
            raise ValueError("Archivable documents require a storage reference")
        if (
            self.rights_state is DocumentRightsState.REFERENCE_ONLY
            and self.storage_reference is not None
        ):
            raise ValueError("Reference-only documents cannot claim a stored copy")
        if self.storage_reference is not None:
            _safe_reference(self.storage_reference, "Document storage reference")
        return self


class MaintainerAttestationManifest(_ManifestBase):
    evidence_class: Literal[EvidenceClass.MAINTAINER_ATTESTATION] = (
        EvidenceClass.MAINTAINER_ATTESTATION
    )
    authority_id: str = Field(min_length=1, max_length=255)
    scope: str = Field(min_length=1, max_length=1000)
    signed_statement: str = Field(min_length=1, max_length=20_000)
    signature_key_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    signature: str = Field(pattern=r"^[A-Za-z0-9_-]{86}$")
    attested_at: datetime
    license: str = Field(min_length=1, max_length=160)
    supporting_reference: str = Field(min_length=1, max_length=2048)

    @field_validator("attested_at")
    @classmethod
    def require_aware_attested_at(cls, value: datetime) -> datetime:
        return _aware(value, "Attestation time")

    @field_validator("supporting_reference")
    @classmethod
    def validate_supporting_reference(cls, value: str) -> str:
        return _public_https_url(value, "Attestation supporting reference")

EvidenceManifest: TypeAlias = Annotated[
    SanitizedMediaManifest
    | VersionedPublicDatasetManifest
    | PublicDocumentManifest
    | MaintainerAttestationManifest,
    Field(discriminator="evidence_class"),
]
_MANIFEST_ADAPTER: TypeAdapter[EvidenceManifest] = TypeAdapter(EvidenceManifest)


class EvidenceAcknowledgement(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    evidence_id: UUID
    evidence_class: EvidenceClass
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: EvidenceAcknowledgementKind
    destination: str = Field(min_length=1, max_length=2048)
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    external_reference: str = Field(min_length=1, max_length=2048)
    verified_at: datetime
    adapter_identity: str = Field(min_length=1, max_length=255)
    adapter_version: str = Field(min_length=1, max_length=80)

    @field_validator("destination", "external_reference")
    @classmethod
    def validate_references(cls, value: str) -> str:
        return _safe_reference(value, "Evidence acknowledgement reference")

    @field_validator("verified_at")
    @classmethod
    def require_aware_verified_at(cls, value: datetime) -> datetime:
        return _aware(value, "Acknowledgement verification time")


class EvidenceTombstone(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    evidence_id: UUID
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    prior_state: EvidencePublicState
    reason: str = Field(min_length=1, max_length=2000)
    removed_by_actor_id: UUID
    removed_at: datetime

    @field_validator("removed_at")
    @classmethod
    def require_aware_removed_at(cls, value: datetime) -> datetime:
        return _aware(value, "Evidence removal time")

    @model_validator(mode="after")
    def reject_nested_tombstone(self) -> EvidenceTombstone:
        if self.prior_state is EvidencePublicState.TOMBSTONED:
            raise ValueError("A tombstone cannot tombstone another tombstone")
        return self


def parse_manifest(value: object) -> EvidenceManifest:
    return _MANIFEST_ADAPTER.validate_python(value)


def canonical_manifest_bytes(manifest: EvidenceManifest) -> bytes:
    return json.dumps(
        _MANIFEST_ADAPTER.dump_python(manifest, mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def manifest_digest(manifest: EvidenceManifest) -> str:
    return hashlib.sha256(canonical_manifest_bytes(manifest)).hexdigest()


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    return value


def _public_https_url(value: str, label: str) -> str:
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError(f"{label} must be a public HTTPS URL") from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or (port is not None and not 1 <= port <= 65_535)
        or any(character.isspace() or character in "<>\"'\\" for character in value)
    ):
        raise ValueError(f"{label} must be a public HTTPS URL")
    return value


def _safe_reference(value: str, label: str) -> str:
    if not _SAFE_REFERENCE.fullmatch(value):
        raise ValueError(f"{label} is invalid")
    return value
