from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.evidence.contracts import EvidenceManifest, manifest_digest
from opennosh_api.evidence.models import EvidenceManifestRecord
from opennosh_api.evidence.repository import create_manifest
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
    connection = await session.connection()
    await queue.enqueue(connection, preservation_request(manifest, run_after=now))
    return record
