from __future__ import annotations

import hashlib
from datetime import datetime
from uuid import UUID, uuid4

from opennosh_api.evidence.contracts import DocumentRightsState, PublicDocumentManifest
from opennosh_api.evidence.repository import create_manifest, record_acknowledgements
from opennosh_api.evidence.storage import MemoryEvidenceStore
from opennosh_api.evidence.worker import EvidencePreservationWorker
from sqlalchemy.ext.asyncio import AsyncSession


async def seed_verified_reference_evidence(
    session: AsyncSession,
    *,
    draft_id: UUID,
    draft_version: int,
    now: datetime,
) -> PublicDocumentManifest:
    manifest = PublicDocumentManifest(
        evidence_id=uuid4(),
        canonical_uri="https://example.test/governed-source",
        publisher="Fixture publisher",
        license="CC-BY-4.0",
        title="Governed source",
        observed_at=now,
        observed_digest=hashlib.sha256(b"observed source").hexdigest(),
        rights_state=DocumentRightsState.REFERENCE_ONLY,
    )
    await create_manifest(
        session,
        source_draft_id=draft_id,
        source_draft_version=draft_version,
        manifest=manifest,
    )
    acknowledgements = await EvidencePreservationWorker(MemoryEvidenceStore()).preserve(
        manifest,
        payloads={},
        now=now,
    )
    await record_acknowledgements(session, manifest.evidence_id, acknowledgements)
    return manifest
