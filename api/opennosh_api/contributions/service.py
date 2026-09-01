from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.contributions.models import ContributionDraft, ContributionDraftOperation
from opennosh_api.contributions.schemas import (
    ContributionBlocker,
    ContributionCapability,
    ContributionDraftFields,
    ContributionDraftPatch,
    ContributionEvidenceType,
    ContributionFieldName,
    ContributionFieldPatch,
    ContributionReceipt,
    ContributionReviewState,
    ContributionSourceLicense,
    ContributionStage,
    ContributionSubmit,
    DuplicateCandidate,
)
from opennosh_api.evidence.contracts import (
    EvidenceClass,
    EvidenceManifest,
    MaintainerAttestationManifest,
    PublicDocumentManifest,
    SanitizedMediaManifest,
    VersionedPublicDatasetManifest,
    parse_manifest,
)
from opennosh_api.evidence.models import EvidenceManifestRecord
from opennosh_api.evidence.service import create_manifest_and_enqueue
from opennosh_api.jobs import JobQueue
from opennosh_api.models.tables import FoodCommunity, FoodReference

STAGES = tuple(ContributionStage)
_PLAIN = re.compile(r"^[^<>\x00-\x1f\x7f]+$")
_LOCALE = re.compile(r"^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$")
_TEXT_LIMITS: dict[ContributionFieldName, int] = {
    ContributionFieldName.NAME: 255,
    ContributionFieldName.NAME_LOCAL: 255,
    ContributionFieldName.LOCALE: 35,
    ContributionFieldName.CATEGORY: 160,
    ContributionFieldName.PORTION_DESCRIPTION: 160,
    ContributionFieldName.PORTION_UNIT: 16,
    ContributionFieldName.INGREDIENTS: 5_000,
    ContributionFieldName.PACK_ID: 160,
    ContributionFieldName.ATTRIBUTION: 100,
}
_NUMERIC_LIMITS: dict[ContributionFieldName, Decimal] = {
    ContributionFieldName.PORTION_AMOUNT: Decimal("1000000"),
    ContributionFieldName.PORTION_GRAMS: Decimal("1000000"),
    ContributionFieldName.ENERGY_KCAL: Decimal("100000"),
    ContributionFieldName.PROTEIN_G: Decimal("10000"),
    ContributionFieldName.FAT_G: Decimal("10000"),
    ContributionFieldName.CARBOHYDRATE_G: Decimal("10000"),
}


class ContributionNotFoundError(Exception):
    pass


class ContributionConflictError(Exception):
    def __init__(self, capability: ContributionCapability) -> None:
        super().__init__("The contribution changed in another session.")
        self.capability = capability


class ContributionValidationError(Exception):
    def __init__(self, blockers: list[ContributionBlocker]) -> None:
        super().__init__("The contribution is not ready for this action.")
        self.blockers = blockers


def _string(value: object, field: ContributionFieldName, limit: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field.value} must be text")
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > limit or not _PLAIN.fullmatch(normalized):
        raise ValueError(f"{field.value} is invalid")
    return normalized


def _decimal(value: object, field: ContributionFieldName) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field.value} must be a number")
    raw = str(value).strip()
    if len(raw) > 64 or not raw or any(ord(character) < 32 for character in raw):
        raise ValueError(f"{field.value} is invalid")
    try:
        parsed = Decimal(raw)
    except (InvalidOperation, ValueError):
        return raw
    if not parsed.is_finite() or parsed < 0 or parsed > _NUMERIC_LIMITS[field]:
        return raw
    return str(parsed.normalize())


def _normalize_patch(patch: ContributionFieldPatch) -> object:
    field = patch.field
    value = patch.value
    if field in _TEXT_LIMITS:
        normalized = _string(value, field, _TEXT_LIMITS[field])
        if field is ContributionFieldName.PORTION_UNIT and normalized not in {
            None,
            "g",
            "oz",
            "lb",
            "serving",
        }:
            raise ValueError("portion_unit is invalid")
        return normalized
    if field in _NUMERIC_LIMITS:
        return _decimal(value, field)
    if field in {
        ContributionFieldName.RIGHTS_ACKNOWLEDGED,
        ContributionFieldName.DUPLICATES_RESOLVED,
        ContributionFieldName.REVIEW_ACKNOWLEDGED,
    }:
        if not isinstance(value, bool):
            raise ValueError(f"{field.value} must be true or false")
        return value
    if field is ContributionFieldName.SOURCE_URI:
        normalized = _string(value, field, 2_048)
        if normalized is None:
            return None
        source_url = urlsplit(normalized)
        if source_url.username or source_url.password:
            raise ValueError("source_uri must be a public HTTPS URL")
        return normalized
    if field is ContributionFieldName.EVIDENCE_TYPE:
        return None if value is None else ContributionEvidenceType(str(value)).value
    if field is ContributionFieldName.SOURCE_LICENSE:
        return None if value is None else ContributionSourceLicense(str(value)).value
    if field is ContributionFieldName.SOURCE_DATE:
        if value is None or value == "":
            return None
        source_date = date.fromisoformat(str(value))
        return source_date.isoformat()
    raise ValueError("Unsupported contribution field")


def _normalize_base(patch: ContributionFieldPatch) -> object:
    return _normalize_patch(
        ContributionFieldPatch(field=patch.field, value=patch.base_value)
    )


async def _duplicate_candidates(
    database: AsyncSession,
    name: str | None,
) -> list[dict[str, str | None]]:
    if not name:
        return []
    normalized = name.casefold()
    community = (
        await database.execute(
            select(FoodCommunity.slug, FoodCommunity.name, FoodCommunity.locale)
            .where(func.lower(FoodCommunity.name) == normalized)
            .limit(5)
        )
    ).all()
    remaining = 5 - len(community)
    reference = (
        (
            await database.execute(
                select(FoodReference.fdc_id, FoodReference.description)
                .where(func.lower(FoodReference.description) == normalized)
                .limit(remaining)
            )
        ).all()
        if remaining
        else []
    )
    return [
        {"source": "community", "source_id": row[0], "name": row[1], "locale": row[2]}
        for row in community
    ] + [
        {"source": "usda", "source_id": row[0], "name": row[1], "locale": None} for row in reference
    ]


def _missing(
    stage: ContributionStage,
    field: ContributionFieldName,
    message: str,
) -> ContributionBlocker:
    return ContributionBlocker(stage=stage, field=field, code="required", message=message)


def _valid_decimal(value: str | None, field: ContributionFieldName) -> bool:
    if value is None:
        return False
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        return False
    return parsed.is_finite() and 0 <= parsed <= _NUMERIC_LIMITS[field]


def _stage_blockers(
    fields: ContributionDraftFields,
    candidates: list[DuplicateCandidate],
) -> dict[ContributionStage, list[ContributionBlocker]]:
    blockers: dict[ContributionStage, list[ContributionBlocker]] = {stage: [] for stage in STAGES}
    if fields.evidence_type is None:
        blockers[ContributionStage.EVIDENCE].append(
            _missing(
                ContributionStage.EVIDENCE,
                ContributionFieldName.EVIDENCE_TYPE,
                "Choose the kind of source you are documenting.",
            )
        )
    source_url = urlsplit(fields.source_uri or "")
    if (
        source_url.scheme != "https"
        or not source_url.hostname
        or source_url.username
        or source_url.password
    ):
        blockers[ContributionStage.EVIDENCE].append(
            _missing(
                ContributionStage.EVIDENCE,
                ContributionFieldName.SOURCE_URI,
                "Add the public source URL.",
            )
        )
    if not fields.rights_acknowledged:
        blockers[ContributionStage.EVIDENCE].append(
            _missing(
                ContributionStage.EVIDENCE,
                ContributionFieldName.RIGHTS_ACKNOWLEDGED,
                "Confirm that opennosh may preserve this source reference.",
            )
        )
    for detail_field, detail_value, detail_message in (
        (ContributionFieldName.NAME, fields.name, "Add the food name."),
        (ContributionFieldName.LOCALE, fields.locale, "Add the food locale."),
        (ContributionFieldName.CATEGORY, fields.category, "Add a food category."),
        (
            ContributionFieldName.PORTION_DESCRIPTION,
            fields.portion_description,
            "Describe the portion.",
        ),
        (
            ContributionFieldName.PORTION_AMOUNT,
            fields.portion_amount,
            "Add the original portion amount.",
        ),
        (
            ContributionFieldName.PORTION_UNIT,
            fields.portion_unit,
            "Choose the original portion unit.",
        ),
        (
            ContributionFieldName.PORTION_GRAMS,
            fields.portion_grams,
            "Add the canonical gram weight.",
        ),
        (ContributionFieldName.ENERGY_KCAL, fields.energy_kcal, "Add energy per portion."),
        (ContributionFieldName.PROTEIN_G, fields.protein_g, "Add protein per portion."),
        (ContributionFieldName.FAT_G, fields.fat_g, "Add fat per portion."),
        (
            ContributionFieldName.CARBOHYDRATE_G,
            fields.carbohydrate_g,
            "Add carbohydrate per portion.",
        ),
    ):
        valid = detail_value is not None
        if detail_field is ContributionFieldName.LOCALE:
            valid = bool(detail_value and _LOCALE.fullmatch(detail_value))
        if detail_field in _NUMERIC_LIMITS:
            valid = isinstance(detail_value, str) and _valid_decimal(detail_value, detail_field)
        if not valid:
            blockers[ContributionStage.DETAILS].append(
                _missing(ContributionStage.DETAILS, detail_field, detail_message)
            )
    if candidates and not fields.duplicates_resolved:
        blockers[ContributionStage.DUPLICATES].append(
            _missing(
                ContributionStage.DUPLICATES,
                ContributionFieldName.DUPLICATES_RESOLVED,
                "Review the possible existing records before continuing.",
            )
        )
    for provenance_field, provenance_value, provenance_message in (
        (ContributionFieldName.PACK_ID, fields.pack_id, "Choose the target pack."),
        (
            ContributionFieldName.SOURCE_DATE,
            fields.source_date,
            "Add the date observed on the source.",
        ),
        (
            ContributionFieldName.ATTRIBUTION,
            fields.attribution,
            "Add the public contributor credit.",
        ),
        (ContributionFieldName.SOURCE_LICENSE, fields.source_license, "Choose the source license."),
    ):
        valid = provenance_value is not None
        if provenance_field is ContributionFieldName.SOURCE_DATE:
            valid = isinstance(provenance_value, date) and provenance_value <= date.today()
        if not valid:
            blockers[ContributionStage.PROVENANCE].append(
                _missing(
                    ContributionStage.PROVENANCE,
                    provenance_field,
                    provenance_message,
                )
            )
    if not fields.review_acknowledged:
        blockers[ContributionStage.REVIEW].append(
            _missing(
                ContributionStage.REVIEW,
                ContributionFieldName.REVIEW_ACKNOWLEDGED,
                "Confirm the attribution, CC0 terms, and review process.",
            )
        )
    return blockers


def build_capability(
    draft: ContributionDraft,
    requested_stage: str | ContributionStage | None = None,
) -> ContributionCapability:
    fields = ContributionDraftFields.model_validate(draft.fields_json)
    candidates = [
        DuplicateCandidate.model_validate(item) for item in draft.duplicate_candidates_json[:5]
    ]
    blockers_by_stage = _stage_blockers(fields, candidates)
    completed: list[ContributionStage] = []
    accessible: list[ContributionStage] = [ContributionStage.EVIDENCE]
    for index, stage in enumerate(STAGES):
        stage_complete = not blockers_by_stage[stage]
        if stage is ContributionStage.REVIEW:
            stage_complete = draft.review_state != ContributionReviewState.DRAFT.value
        if stage_complete and (index == 0 or STAGES[index - 1] in completed):
            completed.append(stage)
            if index + 1 < len(STAGES):
                accessible.append(STAGES[index + 1])
        else:
            break
    next_safe = next(
        (stage for stage in STAGES if stage not in completed), ContributionStage.REVIEW
    )
    repair_reason: str | None = None
    try:
        requested = ContributionStage(requested_stage or next_safe)
    except ValueError:
        requested = next_safe
        repair_reason = "unknown_stage"
    resolved = requested
    if requested not in accessible:
        resolved = next_safe
        repair_reason = "stage_not_accessible"
    visible_stages = set(accessible)
    blockers = [
        blocker
        for stage in STAGES
        if stage in visible_stages
        for blocker in blockers_by_stage[stage]
    ]
    receipt = None
    if draft.submission_id and draft.submitted_at:
        receipt = ContributionReceipt(
            submission_id=draft.submission_id,
            submitted_at=draft.submitted_at,
            acknowledgement_due_at=draft.submitted_at + timedelta(hours=48),
            attribution=fields.attribution or "Anonymous contributor",
            status_href=f"/en/contribute/{draft.id}/status",
        )
    return ContributionCapability(
        draft_id=draft.id,
        draft_version=draft.draft_version,
        review_state=ContributionReviewState(draft.review_state),
        completed_stages=completed,
        accessible_stages=accessible,
        blockers=blockers,
        next_safe_stage=next_safe,
        requested_stage=requested,
        resolved_stage=resolved,
        repair_reason=repair_reason,  # type: ignore[arg-type]
        saved_at=draft.updated_at,
        fields=fields,
        duplicate_candidates=candidates,
        receipt=receipt,
    )


async def create_draft(
    database: AsyncSession,
    *,
    user_id: UUID,
    client_draft_id: str | None,
) -> ContributionCapability:
    if client_draft_id:
        existing = (
            await database.execute(
                select(ContributionDraft).where(
                    ContributionDraft.user_id == user_id,
                    ContributionDraft.client_draft_id == client_draft_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return build_capability(existing, ContributionStage.EVIDENCE)
    draft = ContributionDraft(user_id=user_id, client_draft_id=client_draft_id)
    database.add(draft)
    try:
        await database.commit()
    except IntegrityError:
        await database.rollback()
        if not client_draft_id:
            raise
        existing = (
            await database.execute(
                select(ContributionDraft).where(
                    ContributionDraft.user_id == user_id,
                    ContributionDraft.client_draft_id == client_draft_id,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            raise
        return build_capability(existing, ContributionStage.EVIDENCE)
    await database.refresh(draft)
    return build_capability(draft, ContributionStage.EVIDENCE)


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
        raise ContributionNotFoundError
    return draft


async def get_draft(
    database: AsyncSession,
    *,
    draft_id: UUID,
    user_id: UUID,
    requested_stage: str | None,
) -> ContributionCapability:
    return build_capability(
        await _owned_draft(database, draft_id=draft_id, user_id=user_id),
        requested_stage,
    )


async def patch_draft(
    database: AsyncSession,
    *,
    draft_id: UUID,
    user_id: UUID,
    payload: ContributionDraftPatch,
    operation_retention_seconds: int,
) -> ContributionCapability:
    draft = await _owned_draft(database, draft_id=draft_id, user_id=user_id, for_update=True)
    await database.execute(
        delete(ContributionDraftOperation).where(
            ContributionDraftOperation.draft_id == draft_id,
            ContributionDraftOperation.created_at
            <= datetime.now(UTC) - timedelta(seconds=operation_retention_seconds),
        )
    )
    existing_operation = (
        await database.execute(
            select(ContributionDraftOperation).where(
                ContributionDraftOperation.draft_id == draft_id,
                ContributionDraftOperation.operation_id == payload.operation_id,
            )
        )
    ).scalar_one_or_none()
    if existing_operation is not None:
        return build_capability(draft, payload.requested_stage)
    if draft.review_state != ContributionReviewState.DRAFT.value:
        raise ContributionValidationError(
            [
                ContributionBlocker(
                    stage=ContributionStage.REVIEW,
                    code="review_locked",
                    message="This contribution is already in review.",
                )
            ]
        )
    if draft.draft_version != payload.expected_draft_version:
        fields = dict(draft.fields_json)
        can_merge = all(
            patch.base_version is not None
            and patch.base_version <= payload.expected_draft_version
            and fields.get(patch.field.value) == _normalize_base(patch)
            for patch in payload.patches
        )
        if not can_merge:
            raise ContributionConflictError(build_capability(draft, payload.requested_stage))
    fields = dict(draft.fields_json)
    for patch in payload.patches:
        fields[patch.field.value] = _normalize_patch(patch)
    validated = ContributionDraftFields.model_validate(fields)
    if any(patch.field is ContributionFieldName.NAME for patch in payload.patches):
        draft.duplicate_candidates_json = await _duplicate_candidates(database, validated.name)
        validated.duplicates_resolved = False
    draft.fields_json = validated.model_dump(mode="json")
    draft.draft_version += 1
    draft.updated_at = datetime.now(UTC)
    database.add(
        ContributionDraftOperation(
            draft_id=draft.id,
            operation_id=payload.operation_id,
            resulting_version=draft.draft_version,
        )
    )
    await database.commit()
    await database.refresh(draft)
    return build_capability(draft, payload.requested_stage)


async def submit_draft(
    database: AsyncSession,
    queue: JobQueue,
    *,
    draft_id: UUID,
    user_id: UUID,
    payload: ContributionSubmit,
    now: datetime,
) -> ContributionCapability:
    draft = await _owned_draft(database, draft_id=draft_id, user_id=user_id, for_update=True)
    key_hash = hashlib.sha256(str(payload.idempotency_key).encode()).hexdigest()
    request_hash = hashlib.sha256(
        json.dumps(
            payload.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    if draft.submission_key_hash == key_hash and draft.submitted_at is not None:
        if draft.submission_request_hash == request_hash:
            return build_capability(draft, ContributionStage.REVIEW)
        raise ContributionValidationError(
            [
                ContributionBlocker(
                    stage=ContributionStage.REVIEW,
                    code="idempotency_payload_mismatch",
                    message="This submission key was already used with different evidence.",
                )
            ]
        )
    if draft.review_state != ContributionReviewState.DRAFT.value:
        raise ContributionValidationError(
            [
                ContributionBlocker(
                    stage=ContributionStage.REVIEW,
                    code="review_locked",
                    message="This contribution has already been handed to review.",
                )
            ]
        )
    if draft.draft_version != payload.expected_draft_version:
        raise ContributionConflictError(build_capability(draft, ContributionStage.REVIEW))
    capability = build_capability(draft, ContributionStage.REVIEW)
    required = set(STAGES[:-1])
    blockers = list(capability.blockers)
    if not required.issubset(capability.completed_stages):
        blockers.append(
            ContributionBlocker(
                stage=ContributionStage.REVIEW,
                code="earlier_stage_incomplete",
                message="Complete every earlier stage before submission.",
            )
        )
    if not capability.fields.review_acknowledged:
        blockers.append(
            _missing(
                ContributionStage.REVIEW,
                ContributionFieldName.REVIEW_ACKNOWLEDGED,
                "Confirm the attribution, CC0 terms, and review process.",
            )
        )
    if blockers:
        raise ContributionValidationError(blockers)
    existing_evidence: EvidenceManifestRecord | None = None
    manifest = payload.evidence_manifest
    if manifest is None:
        existing_evidence = await database.scalar(
            select(EvidenceManifestRecord)
            .where(
                EvidenceManifestRecord.source_draft_id == draft.id,
                EvidenceManifestRecord.source_draft_version == draft.draft_version,
            )
            .with_for_update()
        )
        if existing_evidence is not None:
            manifest = parse_manifest(existing_evidence.manifest_json)
    if manifest is None:
        raise ContributionValidationError(
            [
                ContributionBlocker(
                    stage=ContributionStage.EVIDENCE,
                    code="evidence_manifest_required",
                    message="Preserve the complete typed evidence before review begins.",
                )
            ]
        )
    if existing_evidence is not None and (
        existing_evidence.preservation_failure_code is not None
        or existing_evidence.public_state == "tombstoned"
    ):
        raise ContributionValidationError(
            [
                ContributionBlocker(
                    stage=ContributionStage.EVIDENCE,
                    code="evidence_preservation_failed",
                    message="Repair the attached evidence before review begins.",
                )
            ]
        )
    _validate_evidence_binding(draft, manifest)
    draft.review_state = ContributionReviewState.IN_REVIEW.value
    draft.submission_id = uuid4()
    draft.submission_key_hash = key_hash
    draft.submission_request_hash = request_hash
    draft.submitted_at = datetime.now(UTC)
    draft.updated_at = draft.submitted_at
    draft.draft_version += 1
    if existing_evidence is None:
        await create_manifest_and_enqueue(
            database,
            queue,
            source_draft_id=draft.id,
            source_draft_version=draft.draft_version,
            manifest=manifest,
            now=now,
        )
    else:
        # Evidence identities are immutable. Materialize a deterministic successor
        # manifest for the submitted draft version instead of rewriting the attachment.
        successor = manifest.model_copy(
            update={
                "evidence_id": uuid5(
                    NAMESPACE_URL,
                    (
                        "opennosh:submission-evidence:"
                        f"{existing_evidence.id}:{draft.draft_version}"
                    ),
                )
            }
        )
        await create_manifest_and_enqueue(
            database,
            queue,
            source_draft_id=draft.id,
            source_draft_version=draft.draft_version,
            manifest=successor,
            now=now,
        )
    await database.commit()
    await database.refresh(draft)
    return build_capability(draft, ContributionStage.REVIEW)


async def attach_evidence(
    database: AsyncSession,
    queue: JobQueue,
    *,
    draft_id: UUID,
    user_id: UUID,
    expected_draft_version: int,
    manifest: EvidenceManifest,
    now: datetime,
) -> EvidenceManifestRecord:
    """Bind complete typed evidence to an exact owned draft and enqueue preservation."""

    draft = await _owned_draft(database, draft_id=draft_id, user_id=user_id, for_update=True)
    if draft.draft_version != expected_draft_version:
        raise ContributionConflictError(build_capability(draft, ContributionStage.EVIDENCE))
    if draft.review_state not in {
        ContributionReviewState.DRAFT.value,
        ContributionReviewState.IN_REVIEW.value,
        ContributionReviewState.CHANGES_REQUESTED.value,
    }:
        raise ContributionValidationError(
            [
                ContributionBlocker(
                    stage=ContributionStage.EVIDENCE,
                    code="evidence_locked",
                    message="Evidence cannot be replaced after approval begins.",
                )
            ]
        )
    _validate_evidence_binding(draft, manifest)
    record = await create_manifest_and_enqueue(
        database,
        queue,
        source_draft_id=draft.id,
        source_draft_version=draft.draft_version,
        manifest=manifest,
        now=now,
    )
    await database.commit()
    return record


def _validate_evidence_binding(
    draft: ContributionDraft, manifest: EvidenceManifest
) -> None:
    selected_fields = ContributionDraftFields.model_validate(draft.fields_json)
    selected_evidence_type = selected_fields.evidence_type
    expected_class = None if selected_evidence_type is None else {
        ContributionEvidenceType.PACKAGING_LABEL: EvidenceClass.SANITIZED_MEDIA,
        ContributionEvidenceType.GOVERNMENT_DATABASE: EvidenceClass.VERSIONED_PUBLIC_DATASET,
        ContributionEvidenceType.PUBLIC_DOCUMENT: EvidenceClass.PUBLIC_DOCUMENT,
        ContributionEvidenceType.MAINTAINER_ATTESTATION: EvidenceClass.MAINTAINER_ATTESTATION,
    }[selected_evidence_type]
    if expected_class is None or manifest.evidence_class is not expected_class:
        raise ContributionValidationError(
            [
                ContributionBlocker(
                    stage=ContributionStage.EVIDENCE,
                    field=ContributionFieldName.EVIDENCE_TYPE,
                    code="evidence_class_mismatch",
                    message="The typed evidence must match the selected source class.",
                )
            ]
        )
    manifest_source_uri = (
        manifest.source_uri
        if isinstance(manifest, VersionedPublicDatasetManifest)
        else manifest.canonical_uri
        if isinstance(manifest, PublicDocumentManifest)
        else manifest.supporting_reference
        if isinstance(manifest, MaintainerAttestationManifest)
        else selected_fields.source_uri
    )
    if manifest_source_uri != selected_fields.source_uri:
        raise ContributionValidationError(
            [
                ContributionBlocker(
                    stage=ContributionStage.EVIDENCE,
                    field=ContributionFieldName.SOURCE_URI,
                    code="evidence_source_mismatch",
                    message="The typed evidence source must match the reviewed draft source.",
                )
            ]
        )
    manifest_license = (
        None if isinstance(manifest, SanitizedMediaManifest) else manifest.license
    )
    selected_license = (
        None
        if selected_fields.source_license is None
        else selected_fields.source_license.value
    )
    if manifest_license is not None and manifest_license != selected_license:
        raise ContributionValidationError(
            [
                ContributionBlocker(
                    stage=ContributionStage.PROVENANCE,
                    field=ContributionFieldName.SOURCE_LICENSE,
                    code="evidence_license_mismatch",
                    message="The typed evidence license must match the reviewed source license.",
                )
            ]
        )
    if (
        isinstance(manifest, SanitizedMediaManifest)
        or (
            isinstance(manifest, VersionedPublicDatasetManifest)
            and manifest.archival_permitted
        )
        or (
            isinstance(manifest, PublicDocumentManifest)
            and manifest.storage_reference is not None
        )
    ):
        raise ContributionValidationError(
            [
                ContributionBlocker(
                    stage=ContributionStage.EVIDENCE,
                    code="trusted_ingestion_required",
                    message=(
                        "Byte-backed evidence must use the trusted upload and sanitization flow."
                    ),
                )
            ]
        )


async def get_evidence_status(
    database: AsyncSession,
    *,
    draft_id: UUID,
    user_id: UUID,
) -> EvidenceManifestRecord | None:
    draft = await _owned_draft(database, draft_id=draft_id, user_id=user_id)
    record: EvidenceManifestRecord | None = await database.scalar(
        select(EvidenceManifestRecord).where(
            EvidenceManifestRecord.source_draft_id == draft.id,
            EvidenceManifestRecord.source_draft_version == draft.draft_version,
        )
    )
    return record
