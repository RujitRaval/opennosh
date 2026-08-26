from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from opennosh_api.evidence.contracts import (
    DocumentRightsState,
    EvidenceAcknowledgement,
    EvidenceManifest,
    EvidencePublicState,
    PublicDocumentManifest,
)
from opennosh_api.evidence.runtime import (
    EVIDENCE_MAX_UNEXPECTED_ATTEMPTS,
    EvidenceJobProcessor,
    process_evidence_wakeup,
)
from opennosh_api.evidence.storage import MemoryEvidenceStore
from opennosh_api.evidence.worker import EvidenceSourceUnavailableError
from opennosh_api.jobs.contracts import JobLane, JobMessage
from opennosh_api.jobs.pgqueuer import encode_message

NOW = datetime(2026, 8, 26, 12, tzinfo=UTC)


class RecordingRepository:
    def __init__(self, manifest: EvidenceManifest, actions: list[str]) -> None:
        self.manifest = manifest
        self.actions = actions

    async def load_manifest(self, evidence_id: object) -> EvidenceManifest:
        assert evidence_id == self.manifest.evidence_id
        self.actions.append("database-load-released")
        return self.manifest

    async def record_acknowledgements(
        self,
        manifest: EvidenceManifest,
        acknowledgements: tuple[EvidenceAcknowledgement, ...],
    ) -> EvidencePublicState:
        assert manifest == self.manifest
        assert acknowledgements
        self.actions.append("database-record")
        return EvidencePublicState.REFERENCE_ONLY


class RecordingSource:
    def __init__(self, actions: list[str]) -> None:
        self.actions = actions

    async def payloads_for(self, manifest: EvidenceManifest) -> dict[object, bytes]:
        del manifest
        self.actions.append("source-read")
        return {}


class RecordingStore(MemoryEvidenceStore):
    def __init__(self, actions: list[str]) -> None:
        super().__init__()
        self.actions = actions

    async def put_immutable(
        self,
        object_key: str,
        payload: bytes,
        *,
        expected_digest: str,
    ) -> None:
        self.actions.append("immutable-put")
        await super().put_immutable(object_key, payload, expected_digest=expected_digest)

    async def observe(self, object_key: str):  # type: ignore[no-untyped-def]
        self.actions.append("independent-observe")
        return await super().observe(object_key)


@pytest.mark.asyncio
async def test_processor_releases_database_before_source_and_storage_io() -> None:
    actions: list[str] = []
    manifest = PublicDocumentManifest(
        evidence_id=uuid4(),
        canonical_uri="https://example.test/reference",
        publisher="Publisher",
        license="CC-BY-4.0",
        title="Reference only",
        observed_at=NOW,
        observed_digest=hashlib.sha256(b"observed source").hexdigest(),
        rights_state=DocumentRightsState.REFERENCE_ONLY,
    )
    processor = EvidenceJobProcessor(
        RecordingRepository(manifest, actions),  # type: ignore[arg-type]
        RecordingSource(actions),  # type: ignore[arg-type]
        RecordingStore(actions),
        clock=lambda: NOW,
    )

    state = await processor.process(manifest.evidence_id)

    assert state is EvidencePublicState.REFERENCE_ONLY
    assert actions == [
        "database-load-released",
        "source-read",
        "immutable-put",
        "independent-observe",
        "database-record",
    ]


@pytest.mark.asyncio
async def test_exhausted_worker_retry_persists_safe_terminal_failure() -> None:
    evidence_id = uuid4()
    failures: list[tuple[object, str]] = []

    class FailingProcessor:
        async def process(self, attempted_id: object) -> None:
            assert attempted_id == evidence_id
            raise EvidenceSourceUnavailableError("private source missing")

        async def record_terminal_failure(
            self, attempted_id: object, error: Exception
        ) -> None:
            failures.append((attempted_id, type(error).__name__))

    message = JobMessage(
        lane=JobLane.EVIDENCE,
        job_type="evidence.preserve",
        subject_id=evidence_id,
        idempotency_key=f"evidence-preserve:{evidence_id}",
    )
    job = cast(
        Any,
        SimpleNamespace(
            payload=encode_message(message), attempts=EVIDENCE_MAX_UNEXPECTED_ATTEMPTS
        ),
    )

    with pytest.raises(EvidenceSourceUnavailableError):
        await process_evidence_wakeup(cast(Any, FailingProcessor()), job)

    assert failures == [(evidence_id, "EvidenceSourceUnavailableError")]
