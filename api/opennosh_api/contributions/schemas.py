from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ContributionStage(StrEnum):
    EVIDENCE = "evidence"
    DETAILS = "details"
    DUPLICATES = "duplicates"
    PROVENANCE = "provenance"
    REVIEW = "review"


class ContributionReviewState(StrEnum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    CHANGES_REQUESTED = "changes_requested"
    APPROVED = "approved"
    PUBLICATION_PENDING = "publication_pending"
    PUBLISHED = "published"


class ContributionEvidenceType(StrEnum):
    PACKAGING_LABEL = "packaging_label"
    GOVERNMENT_DATABASE = "government_database"
    PUBLIC_DOCUMENT = "public_document"
    MAINTAINER_ATTESTATION = "maintainer_attestation"


class ContributionSourceLicense(StrEnum):
    CONTRIBUTOR_ORIGINAL = "contributor-original"
    CC0 = "CC0-1.0"
    PUBLIC_DOMAIN = "public-domain"


class ContributionFieldName(StrEnum):
    EVIDENCE_TYPE = "evidence_type"
    SOURCE_URI = "source_uri"
    RIGHTS_ACKNOWLEDGED = "rights_acknowledged"
    NAME = "name"
    NAME_LOCAL = "name_local"
    LOCALE = "locale"
    CATEGORY = "category"
    PORTION_DESCRIPTION = "portion_description"
    PORTION_AMOUNT = "portion_amount"
    PORTION_UNIT = "portion_unit"
    PORTION_GRAMS = "portion_grams"
    ENERGY_KCAL = "energy_kcal"
    PROTEIN_G = "protein_g"
    FAT_G = "fat_g"
    CARBOHYDRATE_G = "carbohydrate_g"
    INGREDIENTS = "ingredients"
    DUPLICATES_RESOLVED = "duplicates_resolved"
    PACK_ID = "pack_id"
    SOURCE_DATE = "source_date"
    ATTRIBUTION = "attribution"
    SOURCE_LICENSE = "source_license"
    REVIEW_ACKNOWLEDGED = "review_acknowledged"


ContributionPatchValue = str | Decimal | bool | None


class ContributionFieldPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: ContributionFieldName
    value: ContributionPatchValue


class ContributionDraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_draft_id: Annotated[str | None, Field(min_length=1, max_length=120)] = None


class ContributionDraftPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_draft_version: Annotated[int, Field(ge=1)]
    operation_id: UUID
    patches: Annotated[list[ContributionFieldPatch], Field(min_length=1, max_length=25)]
    requested_stage: ContributionStage | None = None


class ContributionSubmit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_draft_version: Annotated[int, Field(ge=1)]
    idempotency_key: UUID


class ContributionDraftFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_type: ContributionEvidenceType | None = None
    source_uri: str | None = None
    rights_acknowledged: bool = False
    name: str | None = None
    name_local: str | None = None
    locale: str | None = None
    category: str | None = None
    portion_description: str | None = None
    portion_amount: Decimal | None = None
    portion_unit: Literal["g", "oz", "lb", "serving"] | None = None
    portion_grams: Decimal | None = None
    energy_kcal: Decimal | None = None
    protein_g: Decimal | None = None
    fat_g: Decimal | None = None
    carbohydrate_g: Decimal | None = None
    ingredients: str | None = None
    duplicates_resolved: bool = False
    pack_id: str | None = None
    source_date: date | None = None
    attribution: str | None = None
    source_license: ContributionSourceLicense | None = None
    review_acknowledged: bool = False


class ContributionBlocker(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: ContributionStage
    field: ContributionFieldName | None = None
    code: Annotated[str, Field(min_length=1, max_length=80)]
    message: Annotated[str, Field(min_length=1, max_length=240)]


class DuplicateCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["community", "usda"]
    source_id: Annotated[str, Field(min_length=1, max_length=160)]
    name: Annotated[str, Field(min_length=1, max_length=500)]
    locale: Annotated[str | None, Field(default=None, max_length=35)]


class ContributionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    submission_id: UUID
    status: Literal["received_for_review"] = "received_for_review"
    submitted_at: datetime
    acknowledgement_due_at: datetime
    attribution: Annotated[str, Field(min_length=1, max_length=100)]
    status_href: Annotated[str, Field(pattern=r"^/[A-Za-z0-9][A-Za-z0-9/_-]+$")]


class ContributionCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    workflow_version: Literal["1"] = "1"
    draft_id: UUID
    draft_version: Annotated[int, Field(ge=1)]
    review_state: ContributionReviewState
    completed_stages: list[ContributionStage]
    accessible_stages: list[ContributionStage]
    blockers: list[ContributionBlocker]
    next_safe_stage: ContributionStage
    requested_stage: ContributionStage
    resolved_stage: ContributionStage
    repair_reason: Literal["unknown_stage", "stage_not_accessible"] | None = None
    saved_at: datetime
    fields: ContributionDraftFields
    duplicate_candidates: Annotated[list[DuplicateCandidate], Field(max_length=5)]
    receipt: ContributionReceipt | None = None
