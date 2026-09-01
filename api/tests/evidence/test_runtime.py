from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from opennosh_api.capacity import ProcessRole
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
    create_evidence_role_driver,
    process_evidence_wakeup,
)
from opennosh_api.evidence.storage import MemoryEvidenceStore
from opennosh_api.evidence.worker import EvidenceSourceUnavailableError
from opennosh_api.jobs.contracts import JobLane, JobMessage
from opennosh_api.jobs.pgqueuer import encode_message
from opennosh_api.settings import Settings

NOW = datetime(2026, 8, 26, 12, tzinfo=UTC)


def _active_evidence_manifest() -> SimpleNamespace:
    budget = SimpleNamespace(
        pool_size=3,
        acquisition_timeout_ms=5000,
        statement_timeout_ms=120000,
        max_in_flight_database_sections=2,
        worker_concurrency=1,
    )
    return SimpleNamespace(
        deployment_id="evidence-test",
        active_role_budget=lambda role: budget,
    )


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

        async def record_terminal_failure(self, attempted_id: object, error: Exception) -> None:
            failures.append((attempted_id, type(error).__name__))

    message = JobMessage(
        lane=JobLane.EVIDENCE,
        job_type="evidence.preserve",
        subject_id=evidence_id,
        idempotency_key=f"evidence-preserve:{evidence_id}",
    )
    job = cast(
        Any,
        SimpleNamespace(payload=encode_message(message), attempts=EVIDENCE_MAX_UNEXPECTED_ATTEMPTS),
    )

    with pytest.raises(EvidenceSourceUnavailableError):
        await process_evidence_wakeup(cast(Any, FailingProcessor()), job)

    assert failures == [(evidence_id, "EvidenceSourceUnavailableError")]


@pytest.mark.asyncio
async def test_evidence_role_requires_and_composes_local_private_adapters(
    tmp_path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "opennosh_api.evidence.runtime.load_capacity_manifest",
        lambda path: _active_evidence_manifest(),
    )
    base = {
        "process_role": ProcessRole.EVIDENCE,
        "evidence_database_url": "postgresql+asyncpg://evidence:secret@localhost/opennosh",
        "_env_file": None,
    }
    with pytest.raises(ValueError, match="private source adapter"):
        await create_evidence_role_driver(Settings(**base))

    async def no_pool(**arguments: object) -> None:
        del arguments
        return None

    monkeypatch.setattr("opennosh_api.evidence.runtime.asyncpg.create_pool", no_pool)
    with pytest.raises(RuntimeError, match="did not create"):
        await create_evidence_role_driver(
            Settings(
                **base,
                evidence_private_source_directory=tmp_path / "source",
                evidence_immutable_directory=tmp_path / "immutable",
            )
        )


@pytest.mark.asyncio
async def test_evidence_role_composes_isolated_hosted_adapters(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    captured: dict[str, object] = {}

    async def no_pool(**arguments: object) -> None:
        captured.update(arguments)
        return None

    monkeypatch.setattr("opennosh_api.evidence.runtime.asyncpg.create_pool", no_pool)
    monkeypatch.setattr(
        "opennosh_api.evidence.runtime.load_capacity_manifest",
        lambda path: _active_evidence_manifest(),
    )
    settings = Settings(
        process_role=ProcessRole.EVIDENCE,
        evidence_database_url="postgresql+asyncpg://evidence:secret@localhost/opennosh",
        evidence_upload_max_bytes=4096,
        evidence_sanitized_endpoint="https://account.r2.cloudflarestorage.com",
        evidence_sanitized_region="auto",
        evidence_sanitized_bucket="opennosh-evidence-sanitized",
        evidence_sanitized_access_key_id="sanitized-access",
        evidence_sanitized_secret_access_key="sanitized-secret",
        evidence_immutable_endpoint="https://account.r2.cloudflarestorage.com",
        evidence_immutable_region="auto",
        evidence_immutable_bucket="opennosh-evidence-immutable",
        evidence_immutable_access_key_id="immutable-access",
        evidence_immutable_secret_access_key="immutable-secret",
        _env_file=None,
    )

    with pytest.raises(RuntimeError, match="did not create"):
        await create_evidence_role_driver(settings)

    assert captured["min_size"] == 1
    assert captured["dsn"] == "postgresql://evidence:secret@localhost/opennosh"
