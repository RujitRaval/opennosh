from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Literal, Self
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from opennosh_api.contributions.schemas import ContributionDraftFields
from opennosh_api.evidence.contracts import PublicDocumentManifest, parse_manifest
from opennosh_api.governance.contracts import ApprovedChangeSet

FIRST_CONTRIBUTION_NAMESPACE = UUID("2eb7181a-4cd0-4c8c-a58b-3d0638c346f0")
FIRST_FDC_ID = "1105314"
FIRST_PACK_ID = "common-fruits"
FIRST_RECORD_ID = "bananas-ripe-and-slightly-ripe-raw"
FIRST_SOURCE_URI = "https://fdc.nal.usda.gov/fdc-app.html#/food-details/1105314/nutrients"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def derived_id(source_digest: str, purpose: str) -> UUID:
    if not _SHA256.fullmatch(source_digest):
        raise ValueError("First-contribution source digest must be lowercase SHA-256")
    if not purpose or not purpose.isascii():
        raise ValueError("First-contribution identity purpose must be ASCII")
    return uuid5(FIRST_CONTRIBUTION_NAMESPACE, f"{source_digest}:{purpose}")


class FirstContributionPackage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    fdc_id: Literal["1105314"] = "1105314"
    source_record_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_actor_id: UUID
    draft_id: UUID
    submission_id: UUID
    evidence_id: UUID
    role_assignment_id: UUID
    decision_id: UUID
    publication_intent_id: UUID
    draft_fields: dict[str, Any]
    evidence_manifest: dict[str, Any]
    approved_changes: dict[str, Any]
    record_id: Literal["bananas-ripe-and-slightly-ripe-raw"] = (
        "bananas-ripe-and-slightly-ripe-raw"
    )
    package_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("draft_fields")
    @classmethod
    def validate_draft_fields(cls, value: dict[str, Any]) -> dict[str, Any]:
        fields = ContributionDraftFields.model_validate(value)
        if fields.pack_id != FIRST_PACK_ID:
            raise ValueError("First contribution must target common-fruits")
        return fields.model_dump(mode="json")

    @field_validator("evidence_manifest")
    @classmethod
    def validate_evidence_manifest(cls, value: dict[str, Any]) -> dict[str, Any]:
        manifest = parse_manifest(value)
        if not isinstance(manifest, PublicDocumentManifest):
            raise ValueError("First contribution requires public-document evidence")
        if manifest.canonical_uri != FIRST_SOURCE_URI or manifest.storage_reference is not None:
            raise ValueError("First contribution requires the pinned reference-only USDA source")
        return manifest.model_dump(mode="json")

    @field_validator("approved_changes")
    @classmethod
    def validate_approved_changes(cls, value: dict[str, Any]) -> dict[str, Any]:
        changes = ApprovedChangeSet.from_json(value)
        if changes.pack_id != FIRST_PACK_ID or len(changes.files) != 3:
            raise ValueError("First contribution requires the exact three-file common-fruits pack")
        return changes.as_json()

    @model_validator(mode="after")
    def validate_identities_and_digest(self) -> Self:
        expected = {
            "source_actor_id": derived_id(self.source_record_digest, "source-actor"),
            "draft_id": derived_id(self.source_record_digest, "draft"),
            "submission_id": derived_id(self.source_record_digest, "submission"),
            "evidence_id": derived_id(self.source_record_digest, "evidence"),
            "role_assignment_id": derived_id(self.source_record_digest, "steward-role"),
            "decision_id": derived_id(self.source_record_digest, "decision"),
            "publication_intent_id": derived_id(self.source_record_digest, "publication-intent"),
        }
        if any(getattr(self, field) != identity for field, identity in expected.items()):
            raise ValueError("First-contribution deterministic identity mismatch")
        material = self.model_dump(mode="json", exclude={"package_digest"})
        digest = hashlib.sha256(canonical_json(material)).hexdigest()
        if digest != self.package_digest:
            raise ValueError("First-contribution package digest mismatch")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json(self.model_dump(mode="json")) + b"\n"


class FirstContributionReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    package_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_actor_id: UUID
    steward_actor_id: UUID
    draft_id: UUID
    evidence_id: UUID
    evidence_state: Literal["reference_only"] = "reference_only"
    evidence_manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision_id: UUID
    publication_intent_id: UUID
    pack_id: Literal["common-fruits"] = "common-fruits"
    record_id: Literal["bananas-ripe-and-slightly-ripe-raw"] = (
        "bananas-ripe-and-slightly-ripe-raw"
    )
    approved_payload_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    decided_at: datetime

    @field_validator("decided_at")
    @classmethod
    def require_aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("First-contribution receipt time must include a timezone")
        return value
