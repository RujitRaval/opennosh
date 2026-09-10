from __future__ import annotations

from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.contributions.models import ContributionDraft
from opennosh_api.evidence.contracts import (
    DocumentRightsState,
    EvidenceManifest,
    EvidencePublicState,
    PublicDocumentManifest,
    RedactionState,
    SanitizedMediaManifest,
    canonical_manifest_bytes,
    manifest_digest,
)
from opennosh_api.evidence.models import EvidenceManifestRecord, EvidenceUploadSession
from opennosh_api.evidence.policy import verify_durability
from opennosh_api.evidence.repository import create_manifest, load_bundle
from opennosh_api.evidence.uploads import (
    EvidenceUploadConflictError,
    EvidenceUploadNotFoundError,
    EvidenceUploadSessionView,
    EvidenceUploadState,
    upload_session_view,
)
from opennosh_api.jobs import JobLane, JobMessage, JobQueue, JobRequest


def preservation_request(
    manifest: EvidenceManifest,
    *,
    run_after: datetime,
) -> JobRequest:
    digest = manifest_digest(manifest)
    key = f"evidence-preserve:{manifest.evidence_id}:{digest}"
    return JobRequest(
        message=JobMessage(
            lane=JobLane.EVIDENCE,
            job_type="evidence.preserve",
            subject_id=manifest.evidence_id,
            idempotency_key=key,
        ),
        run_after=run_after,
        priority=8,
        deduplication_key=key,
    )


async def create_manifest_and_enqueue(
    session: AsyncSession,
    queue: JobQueue,
    *,
    source_draft_id: UUID,
    source_draft_version: int,
    manifest: EvidenceManifest,
    now: datetime,
) -> EvidenceManifestRecord:
    """Persist manifest identity and its wake-up in the caller's transaction."""

    record = await create_manifest(
        session,
        source_draft_id=source_draft_id,
        source_draft_version=source_draft_version,
        manifest=manifest,
    )
    if (
        isinstance(manifest, PublicDocumentManifest)
        and manifest.rights_state is DocumentRightsState.REFERENCE_ONLY
    ):
        # Only bounded citation metadata is copied. No source bytes are downloaded
        # or scanned, and the evidence remains explicitly reference_only.
        bundle = await load_bundle(session, manifest.evidence_id)
        if bundle.acknowledgements:
            if bundle.tombstone is not None:
                raise RuntimeError("Tombstoned citation cannot be preserved")
            verified = verify_durability(bundle.manifest, bundle.acknowledgements)
            if (
                verified is not EvidencePublicState.REFERENCE_ONLY
                or bundle.public_state is not EvidencePublicState.REFERENCE_ONLY
            ):
                raise RuntimeError("Existing citation state is inconsistent")
            return record
        observed = await session.scalar(
            text("SELECT preserve_reference_citation(:evidence_id, :payload)"),
            {"evidence_id": manifest.evidence_id, "payload": canonical_manifest_bytes(manifest)},
        )
        if observed != manifest_digest(manifest):
            raise RuntimeError("Citation readback digest mismatch")
        await session.refresh(record)
    else:
        connection = await session.connection()
        await queue.enqueue(connection, preservation_request(manifest, run_after=now))
    return record


async def attach_sanitized_upload(
    session: AsyncSession,
    queue: JobQueue,
    *,
    upload_id: UUID,
    draft_id: UUID,
    user_id: UUID,
    source_draft_version: int,
    source_description: str,
    rights_acknowledged: bool,
    redaction_state: RedactionState,
    now: datetime,
) -> EvidenceUploadSessionView:
    """Author one sanitized-media manifest and its preservation wake-up atomically."""

    if not rights_acknowledged:
        raise EvidenceUploadConflictError
    draft = await session.scalar(
        select(ContributionDraft)
        .where(
            ContributionDraft.id == draft_id,
            ContributionDraft.user_id == user_id,
        )
        .with_for_update()
    )
    upload = await session.scalar(
        select(EvidenceUploadSession)
        .where(
            EvidenceUploadSession.id == upload_id,
            EvidenceUploadSession.draft_id == draft_id,
            EvidenceUploadSession.user_id == user_id,
        )
        .with_for_update()
    )
    if draft is None or upload is None:
        raise EvidenceUploadNotFoundError
    if (
        draft.draft_version != source_draft_version
        or upload.source_draft_version != source_draft_version
        or draft.review_state not in {"draft", "in_review", "changes_requested"}
    ):
        raise EvidenceUploadConflictError
    state = EvidenceUploadState(upload.state)
    if state not in {
        EvidenceUploadState.SANITIZED,
        EvidenceUploadState.ATTACHED,
        EvidenceUploadState.PRESERVED,
    }:
        raise EvidenceUploadConflictError
    if (
        upload.sanitized_object_key is None
        or upload.sanitized_sha256 is None
        or upload.sanitized_at is None
    ):
        raise EvidenceUploadConflictError
    evidence_id = uuid5(NAMESPACE_URL, f"opennosh:evidence-upload:{upload.id}")
    if upload.attached_evidence_id not in {None, evidence_id}:
        raise EvidenceUploadConflictError
    manifest = SanitizedMediaManifest(
        evidence_id=evidence_id,
        content_digest=upload.sanitized_sha256,
        safe_format="image/png",
        source_description=source_description,
        rights_acknowledged=True,
        redaction_state=redaction_state,
        storage_reference=f"private:{upload.sanitized_object_key}",
    )
    await create_manifest_and_enqueue(
        session,
        queue,
        source_draft_id=draft_id,
        source_draft_version=source_draft_version,
        manifest=manifest,
        now=now,
    )
    if state is EvidenceUploadState.SANITIZED:
        attached_at = max(now, upload.sanitized_at, upload.updated_at)
        upload.state = EvidenceUploadState.ATTACHED.value
        upload.attached_evidence_id = evidence_id
        upload.attached_at = attached_at
        upload.version += 1
        upload.updated_at = attached_at
    await session.commit()
    return upload_session_view(upload)
