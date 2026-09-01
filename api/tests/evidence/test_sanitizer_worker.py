from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from io import BytesIO
from typing import Any
from uuid import uuid4

import pytest
from opennosh_api.evidence.sanitization import (
    DeterministicAllowEvidenceScanner,
    EvidenceSanitizationError,
    EvidenceSanitizationFailureCode,
    SanitizedEvidenceImage,
)
from opennosh_api.evidence.sanitizer_worker import (
    EvidenceSanitizationClaim,
    EvidenceSanitizationJobError,
    EvidenceSanitizationJobProcessor,
    EvidenceSanitizationResult,
)
from opennosh_api.evidence.storage import (
    EvidenceUploadObjectTooLargeError,
    EvidenceUploadStorageError,
    ImmutableObjectConflictError,
    MemoryEvidenceStore,
    MemoryEvidenceUploadBroker,
)
from opennosh_api.evidence.uploads import EvidenceUploadFailureCode, EvidenceUploadState
from PIL import Image

NOW = datetime(2026, 9, 1, 18, 0, tzinfo=UTC)
REVISION_SHA256 = hashlib.sha256(b'"test-revision"').hexdigest()


def _png() -> bytes:
    image = Image.new("RGB", (4, 3), "red")
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class MemorySanitizationRepository:
    def __init__(self, claim: EvidenceSanitizationClaim | None) -> None:
        self.claim_value = claim
        self.result_value = EvidenceSanitizationResult(
            upload_id=claim.upload_id if claim is not None else uuid4(),
            state=EvidenceUploadState.SANITIZED,
        )
        self.failures: list[EvidenceUploadFailureCode] = []
        self.recorded: list[tuple[str, SanitizedEvidenceImage]] = []

    async def claim(
        self,
        upload_id: object,
        *,
        expected_revision: int,
    ) -> EvidenceSanitizationClaim | None:
        del upload_id
        assert expected_revision == 2
        return self.claim_value

    async def result(self, upload_id: object) -> EvidenceSanitizationResult:
        del upload_id
        return self.result_value

    async def record_sanitized(
        self,
        claim: EvidenceSanitizationClaim,
        image: SanitizedEvidenceImage,
        *,
        object_key: str,
        sanitized_at: datetime,
    ) -> EvidenceSanitizationResult:
        assert sanitized_at == NOW
        self.recorded.append((object_key, image))
        self.result_value = EvidenceSanitizationResult(
            upload_id=claim.upload_id,
            state=EvidenceUploadState.SANITIZED,
            sanitized_object_key=object_key,
            sanitized_sha256=image.content_digest,
        )
        return self.result_value

    async def record_failure(
        self,
        claim: EvidenceSanitizationClaim,
        *,
        failure_code: EvidenceUploadFailureCode,
        failed_at: datetime,
    ) -> None:
        del claim
        assert failed_at == NOW
        self.failures.append(failure_code)


def _processor(
    repository: Any,
    quarantine: MemoryEvidenceUploadBroker,
    store: MemoryEvidenceStore,
    *,
    scanner: Any | None = None,
) -> EvidenceSanitizationJobProcessor:
    return EvidenceSanitizationJobProcessor(
        repository,
        quarantine,
        store,
        scanner or DeterministicAllowEvidenceScanner(),
        clock=lambda: NOW,
        max_bytes=10_485_760,
    )


def _matching_claim(source: bytes) -> EvidenceSanitizationClaim:
    upload_id = uuid4()
    return EvidenceSanitizationClaim(
        upload_id=upload_id,
        workflow_revision=3,
        object_key=f"quarantine/{upload_id}",
        declared_media_type="image/png",
        observed_byte_length=len(source),
        observed_sha256=hashlib.sha256(source).hexdigest(),
        observed_revision_sha256=REVISION_SHA256,
    )


@pytest.mark.asyncio
async def test_sanitization_worker_rewrites_verifies_records_then_deletes_raw() -> None:
    upload_id = uuid4()
    source = _png()
    claim = EvidenceSanitizationClaim(
        upload_id=upload_id,
        workflow_revision=3,
        object_key=f"quarantine/{upload_id}",
        declared_media_type="image/png",
        observed_byte_length=len(source),
        observed_sha256=hashlib.sha256(source).hexdigest(),
        observed_revision_sha256=REVISION_SHA256,
    )
    repository = MemorySanitizationRepository(claim)
    quarantine = MemoryEvidenceUploadBroker()
    quarantine.put_for_test(claim.object_key, source, media_type="image/png")
    store = MemoryEvidenceStore(destination="urn:opennosh:evidence:sanitized")

    result = await _processor(repository, quarantine, store).process(
        upload_id,
        workflow_revision=2,
    )

    assert result.state is EvidenceUploadState.SANITIZED
    assert result.sanitized_object_key is not None
    assert result.sanitized_object_key in store.objects
    assert repository.recorded[0][0] == result.sanitized_object_key
    assert quarantine.operations[-1] == ("delete", claim.object_key)
    assert claim.object_key not in quarantine.objects


@pytest.mark.asyncio
async def test_sanitization_worker_is_idempotent_after_recorded_success() -> None:
    upload_id = uuid4()
    repository = MemorySanitizationRepository(None)
    repository.result_value = EvidenceSanitizationResult(
        upload_id=upload_id,
        state=EvidenceUploadState.SANITIZED,
        sanitized_object_key="sanitized/" + "a" * 64 + ".png",
        sanitized_sha256="a" * 64,
    )
    quarantine = MemoryEvidenceUploadBroker()
    store = MemoryEvidenceStore()

    result = await _processor(repository, quarantine, store).process(
        upload_id,
        workflow_revision=2,
    )

    assert result == repository.result_value
    assert quarantine.operations == []
    assert store.objects == {}


@pytest.mark.asyncio
async def test_sanitization_worker_fails_if_quarantine_changed_after_completion() -> None:
    upload_id = uuid4()
    source = _png()
    claim = EvidenceSanitizationClaim(
        upload_id=upload_id,
        workflow_revision=3,
        object_key=f"quarantine/{upload_id}",
        declared_media_type="image/png",
        observed_byte_length=len(source),
        observed_sha256="0" * 64,
        observed_revision_sha256=REVISION_SHA256,
    )
    repository = MemorySanitizationRepository(claim)
    quarantine = MemoryEvidenceUploadBroker()
    quarantine.put_for_test(claim.object_key, source, media_type="image/png")

    with pytest.raises(EvidenceSanitizationJobError, match="object_changed") as raised:
        await _processor(repository, quarantine, MemoryEvidenceStore()).process(
            upload_id,
            workflow_revision=2,
        )

    assert raised.value.failure_code is EvidenceUploadFailureCode.OBJECT_CHANGED
    assert raised.value.retryable is False
    assert repository.failures == []


@pytest.mark.asyncio
async def test_sanitization_worker_binds_exact_provider_revision() -> None:
    upload_id = uuid4()
    source = _png()
    claim = EvidenceSanitizationClaim(
        upload_id=upload_id,
        workflow_revision=3,
        object_key=f"quarantine/{upload_id}",
        declared_media_type="image/png",
        observed_byte_length=len(source),
        observed_sha256=hashlib.sha256(source).hexdigest(),
        observed_revision_sha256=hashlib.sha256(b'"original-revision"').hexdigest(),
    )
    repository = MemorySanitizationRepository(claim)
    quarantine = MemoryEvidenceUploadBroker()
    quarantine.put_for_test(
        claim.object_key,
        source,
        media_type="image/png",
        revision='"replacement-revision"',
    )

    with pytest.raises(EvidenceSanitizationJobError, match="object_changed") as raised:
        await _processor(repository, quarantine, MemoryEvidenceStore()).process(
            upload_id,
            workflow_revision=2,
        )

    assert raised.value.failure_code is EvidenceUploadFailureCode.OBJECT_CHANGED
    assert repository.recorded == []


@pytest.mark.asyncio
async def test_sanitization_worker_maps_scanner_failure_and_keeps_raw_object() -> None:
    upload_id = uuid4()
    source = _png()
    digest = hashlib.sha256(source).hexdigest()
    claim = EvidenceSanitizationClaim(
        upload_id=upload_id,
        workflow_revision=3,
        object_key=f"quarantine/{upload_id}",
        declared_media_type="image/png",
        observed_byte_length=len(source),
        observed_sha256=digest,
        observed_revision_sha256=REVISION_SHA256,
    )
    repository = MemorySanitizationRepository(claim)
    quarantine = MemoryEvidenceUploadBroker()
    quarantine.put_for_test(claim.object_key, source, media_type="image/png")

    class RejectingScanner:
        identity = "test.reject"
        version = "1"

        async def scan(self, image: SanitizedEvidenceImage) -> None:
            del image
            raise EvidenceSanitizationError(EvidenceSanitizationFailureCode.MALWARE_DETECTED)

    processor = _processor(
        repository,
        quarantine,
        MemoryEvidenceStore(),
        scanner=RejectingScanner(),
    )
    with pytest.raises(EvidenceSanitizationJobError, match="malware_detected") as raised:
        await processor.process(
            upload_id,
            workflow_revision=2,
        )

    assert raised.value.failure_code is EvidenceUploadFailureCode.MALWARE_DETECTED
    assert raised.value.retryable is False
    await processor.record_terminal_failure(raised.value)

    assert repository.failures == [EvidenceUploadFailureCode.MALWARE_DETECTED]
    assert claim.object_key in quarantine.objects


@pytest.mark.asyncio
async def test_sanitization_worker_defers_retryable_scanner_failure() -> None:
    upload_id = uuid4()
    source = _png()
    digest = hashlib.sha256(source).hexdigest()
    claim = EvidenceSanitizationClaim(
        upload_id=upload_id,
        workflow_revision=3,
        object_key=f"quarantine/{upload_id}",
        declared_media_type="image/png",
        observed_byte_length=len(source),
        observed_sha256=digest,
        observed_revision_sha256=REVISION_SHA256,
    )
    repository = MemorySanitizationRepository(claim)
    quarantine = MemoryEvidenceUploadBroker()
    quarantine.put_for_test(claim.object_key, source, media_type="image/png")

    class UnavailableScanner:
        identity = "test.unavailable"
        version = "1"

        async def scan(self, image: SanitizedEvidenceImage) -> None:
            del image
            raise EvidenceSanitizationError(EvidenceSanitizationFailureCode.SCANNER_UNAVAILABLE)

    processor = _processor(
        repository,
        quarantine,
        MemoryEvidenceStore(),
        scanner=UnavailableScanner(),
    )
    with pytest.raises(EvidenceSanitizationJobError, match="scanner_unavailable") as raised:
        await processor.process(
            upload_id,
            workflow_revision=2,
        )

    assert raised.value.retryable is True
    assert repository.failures == []
    assert claim.object_key in quarantine.objects


@pytest.mark.asyncio
async def test_sanitization_worker_does_not_reverse_success_when_raw_delete_fails() -> None:
    upload_id = uuid4()
    source = _png()
    digest = hashlib.sha256(source).hexdigest()
    claim = EvidenceSanitizationClaim(
        upload_id=upload_id,
        workflow_revision=3,
        object_key=f"quarantine/{upload_id}",
        declared_media_type="image/png",
        observed_byte_length=len(source),
        observed_sha256=digest,
        observed_revision_sha256=REVISION_SHA256,
    )
    repository = MemorySanitizationRepository(claim)

    class DeleteFailureBroker(MemoryEvidenceUploadBroker):
        async def delete(self, object_key: str) -> None:
            del object_key
            raise EvidenceUploadStorageError("delete unavailable")

    quarantine = DeleteFailureBroker()
    quarantine.put_for_test(claim.object_key, source, media_type="image/png")

    result = await _processor(repository, quarantine, MemoryEvidenceStore()).process(
        upload_id,
        workflow_revision=2,
    )

    assert result.state is EvidenceUploadState.SANITIZED
    assert repository.failures == []
    assert claim.object_key in quarantine.objects


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "code", "retryable"),
    [
        ("missing", EvidenceUploadFailureCode.OBJECT_MISSING, True),
        ("too_large", EvidenceUploadFailureCode.SIZE_EXCEEDED, False),
        ("unavailable", EvidenceUploadFailureCode.STORAGE_UNAVAILABLE, True),
    ],
)
async def test_sanitization_worker_maps_quarantine_read_failures(
    failure: str,
    code: EvidenceUploadFailureCode,
    retryable: bool,
) -> None:
    source = _png()
    claim = _matching_claim(source)
    repository = MemorySanitizationRepository(claim)

    class FailingQuarantine(MemoryEvidenceUploadBroker):
        async def read(self, object_key: str, *, max_bytes: int):  # type: ignore[no-untyped-def]
            del object_key, max_bytes
            if failure == "missing":
                return None
            if failure == "too_large":
                raise EvidenceUploadObjectTooLargeError("too large")
            raise EvidenceUploadStorageError("unavailable")

    with pytest.raises(EvidenceSanitizationJobError, match=code.value) as raised:
        await _processor(
            repository,
            FailingQuarantine(),
            MemoryEvidenceStore(),
        ).process(claim.upload_id, workflow_revision=2)

    assert raised.value.failure_code is code
    assert raised.value.retryable is retryable


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "code", "retryable"),
    [
        (
            "conflict",
            EvidenceUploadFailureCode.SANITIZED_STORAGE_CONFLICT,
            False,
        ),
        (
            "unavailable",
            EvidenceUploadFailureCode.SANITIZED_STORAGE_UNAVAILABLE,
            True,
        ),
    ],
)
async def test_sanitization_worker_maps_sanitized_store_failures(
    failure: str,
    code: EvidenceUploadFailureCode,
    retryable: bool,
) -> None:
    source = _png()
    claim = _matching_claim(source)
    repository = MemorySanitizationRepository(claim)
    quarantine = MemoryEvidenceUploadBroker()
    quarantine.put_for_test(claim.object_key, source, media_type="image/png")

    class FailingStore(MemoryEvidenceStore):
        async def put_immutable(
            self,
            object_key: str,
            payload: bytes,
            *,
            expected_digest: str,
        ) -> None:
            del object_key, payload, expected_digest
            if failure == "conflict":
                raise ImmutableObjectConflictError("conflict")
            raise EvidenceUploadStorageError("unavailable")

    with pytest.raises(EvidenceSanitizationJobError, match=code.value) as raised:
        await _processor(repository, quarantine, FailingStore()).process(
            claim.upload_id,
            workflow_revision=2,
        )

    assert raised.value.failure_code is code
    assert raised.value.retryable is retryable
    assert repository.recorded == []


@pytest.mark.asyncio
async def test_sanitization_worker_maps_decode_and_unexpected_failures(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    invalid = b"\x89PNG\r\n\x1a\n\x00\x00\x00\x00IEND\xaeB`\x82"
    claim = _matching_claim(invalid)
    repository = MemorySanitizationRepository(claim)
    quarantine = MemoryEvidenceUploadBroker()
    quarantine.put_for_test(claim.object_key, invalid, media_type="image/png")

    with pytest.raises(EvidenceSanitizationJobError, match="decode_failed") as decoded:
        await _processor(repository, quarantine, MemoryEvidenceStore()).process(
            claim.upload_id,
            workflow_revision=2,
        )
    assert decoded.value.retryable is False

    monkeypatch.setattr(
        "opennosh_api.evidence.sanitizer_worker.sanitize_evidence_image",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("unexpected")),
    )
    with pytest.raises(
        EvidenceSanitizationJobError,
        match="metadata_rewrite_failed",
    ) as unexpected:
        await _processor(repository, quarantine, MemoryEvidenceStore()).process(
            claim.upload_id,
            workflow_revision=2,
        )
    assert unexpected.value.retryable is False


@pytest.mark.asyncio
async def test_sanitization_worker_preserves_cancellation() -> None:
    source = _png()
    claim = _matching_claim(source)

    class CancelledQuarantine(MemoryEvidenceUploadBroker):
        async def read(self, object_key: str, *, max_bytes: int):  # type: ignore[no-untyped-def]
            del object_key, max_bytes
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await _processor(
            MemorySanitizationRepository(claim),
            CancelledQuarantine(),
            MemoryEvidenceStore(),
        ).process(claim.upload_id, workflow_revision=2)


@pytest.mark.asyncio
async def test_sanitization_worker_does_not_remap_typed_job_errors() -> None:
    source = _png()
    claim = _matching_claim(source)
    repository = MemorySanitizationRepository(claim)
    quarantine = MemoryEvidenceUploadBroker()
    quarantine.put_for_test(claim.object_key, source, media_type="image/png")
    expected = EvidenceSanitizationJobError(
        claim,
        failure_code=EvidenceUploadFailureCode.SCANNER_UNAVAILABLE,
        retryable=True,
    )

    class TypedFailureScanner:
        async def scan(self, image: SanitizedEvidenceImage) -> None:
            del image
            raise expected

    with pytest.raises(EvidenceSanitizationJobError) as raised:
        await _processor(
            repository,
            quarantine,
            MemoryEvidenceStore(),
            scanner=TypedFailureScanner(),
        ).process(claim.upload_id, workflow_revision=2)
    assert raised.value is expected
