from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from opennosh_api.evidence.sanitization import SanitizedEvidenceImage
from opennosh_api.evidence.sanitizer_worker import (
    EvidenceSanitizationClaim,
    EvidenceSanitizationRepository,
    EvidenceSanitizationResult,
)
from opennosh_api.evidence.uploads import EvidenceUploadFailureCode, EvidenceUploadState

NOW = datetime(2026, 9, 1, 18, 0, tzinfo=UTC)


class _Context:
    def __init__(self, value: Any) -> None:
        self.value = value

    async def __aenter__(self) -> Any:
        return self.value

    async def __aexit__(self, *args: object) -> None:
        del args


class _Connection:
    def __init__(
        self,
        *,
        rows: list[dict[str, object] | None] | None = None,
        values: list[object | None] | None = None,
    ) -> None:
        self.rows = list(rows or [])
        self.values = list(values or [])

    def transaction(self) -> _Context:
        return _Context(self)

    async def fetchrow(self, query: str, *args: object) -> dict[str, object] | None:
        del query, args
        return self.rows.pop(0)

    async def fetchval(self, query: str, *args: object) -> object | None:
        del query, args
        return self.values.pop(0)


class _Pool:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    def acquire(self) -> _Context:
        return _Context(self.connection)


def _row(state: EvidenceUploadState, *, version: int = 2) -> dict[str, object]:
    upload_id = uuid4()
    return {
        "id": upload_id,
        "state": state.value,
        "version": version,
        "object_key": f"quarantine/{upload_id}",
        "declared_media_type": "image/png",
        "observed_byte_length": 8,
        "observed_sha256": "a" * 64,
        "observed_revision_sha256": "b" * 64,
        "sanitized_object_key": None,
        "sanitized_sha256": None,
        "failure_code": None,
    }


@pytest.mark.asyncio
async def test_repository_claim_handles_missing_terminal_and_failed_rows() -> None:
    upload_id = uuid4()
    missing = EvidenceSanitizationRepository(_Pool(_Connection(rows=[None])))
    with pytest.raises(LookupError, match="Unknown evidence upload"):
        await missing.claim(upload_id, expected_revision=2)

    for state in (
        EvidenceUploadState.SANITIZED,
        EvidenceUploadState.ATTACHED,
        EvidenceUploadState.PRESERVED,
    ):
        repository = EvidenceSanitizationRepository(
            _Pool(_Connection(rows=[_row(state)]))
        )
        assert await repository.claim(upload_id, expected_revision=2) is None

    failed = EvidenceSanitizationRepository(
        _Pool(_Connection(rows=[_row(EvidenceUploadState.FAILED)]))
    )
    with pytest.raises(RuntimeError, match="cannot be sanitized"):
        await failed.claim(upload_id, expected_revision=2)


@pytest.mark.asyncio
async def test_repository_claim_rejects_stale_concurrent_and_invalid_states() -> None:
    upload_id = uuid4()
    stale = EvidenceSanitizationRepository(
        _Pool(_Connection(rows=[_row(EvidenceUploadState.UPLOADED, version=3)]))
    )
    assert await stale.claim(upload_id, expected_revision=2) is None

    concurrent = EvidenceSanitizationRepository(
        _Pool(
            _Connection(
                rows=[_row(EvidenceUploadState.UPLOADED, version=2), None]
            )
        )
    )
    with pytest.raises(RuntimeError, match="changed concurrently"):
        await concurrent.claim(upload_id, expected_revision=2)

    sanitizing = EvidenceSanitizationRepository(
        _Pool(_Connection(rows=[_row(EvidenceUploadState.SANITIZING, version=4)]))
    )
    assert await sanitizing.claim(upload_id, expected_revision=2) is None

    invalid = EvidenceSanitizationRepository(
        _Pool(_Connection(rows=[_row(EvidenceUploadState.INITIATED)]))
    )
    with pytest.raises(RuntimeError, match="cannot be sanitized"):
        await invalid.claim(upload_id, expected_revision=2)


@pytest.mark.asyncio
async def test_repository_claim_requires_complete_upload_observation() -> None:
    upload_id = uuid4()
    row = _row(EvidenceUploadState.SANITIZING, version=3)
    row["observed_revision_sha256"] = None
    repository = EvidenceSanitizationRepository(_Pool(_Connection(rows=[row])))

    with pytest.raises(RuntimeError, match="lacks an upload observation"):
        await repository.claim(upload_id, expected_revision=2)


def _claim() -> EvidenceSanitizationClaim:
    upload_id = uuid4()
    return EvidenceSanitizationClaim(
        upload_id=upload_id,
        workflow_revision=3,
        object_key=f"quarantine/{upload_id}",
        declared_media_type="image/png",
        observed_byte_length=8,
        observed_sha256="a" * 64,
        observed_revision_sha256="b" * 64,
    )


def _image() -> SanitizedEvidenceImage:
    return SanitizedEvidenceImage(
        payload=b"sanitized",
        media_type="image/png",
        content_digest="c" * 64,
        width=1,
        height=1,
    )


@pytest.mark.asyncio
async def test_repository_record_sanitized_accepts_only_exact_idempotent_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _claim()
    image = _image()
    repository = EvidenceSanitizationRepository(_Pool(_Connection(rows=[None])))
    expected = EvidenceSanitizationResult(
        upload_id=claim.upload_id,
        state=EvidenceUploadState.SANITIZED,
        sanitized_object_key=f"sanitized/{image.content_digest}.png",
        sanitized_sha256=image.content_digest,
    )

    async def exact(_upload_id: object) -> EvidenceSanitizationResult:
        return expected

    monkeypatch.setattr(repository, "result", exact)
    assert await repository.record_sanitized(
        claim,
        image,
        object_key=expected.sanitized_object_key or "",
        sanitized_at=NOW,
    ) == expected

    changed = EvidenceSanitizationRepository(_Pool(_Connection(rows=[None])))

    async def different(_upload_id: object) -> EvidenceSanitizationResult:
        return EvidenceSanitizationResult(
            upload_id=claim.upload_id,
            state=EvidenceUploadState.FAILED,
        )

    monkeypatch.setattr(changed, "result", different)
    with pytest.raises(RuntimeError, match="changed concurrently"):
        await changed.record_sanitized(
            claim,
            image,
            object_key=expected.sanitized_object_key or "",
            sanitized_at=NOW,
        )


@pytest.mark.asyncio
async def test_repository_record_failure_is_exactly_idempotent() -> None:
    claim = _claim()
    updated = EvidenceSanitizationRepository(
        _Pool(_Connection(values=[claim.upload_id]))
    )
    await updated.record_failure(
        claim,
        failure_code=EvidenceUploadFailureCode.DECODE_FAILED,
        failed_at=NOW,
    )

    same = EvidenceSanitizationRepository(
        _Pool(
            _Connection(
                rows=[
                    {
                        "state": EvidenceUploadState.FAILED.value,
                        "failure_code": EvidenceUploadFailureCode.DECODE_FAILED.value,
                    }
                ],
                values=[None],
            )
        )
    )
    await same.record_failure(
        claim,
        failure_code=EvidenceUploadFailureCode.DECODE_FAILED,
        failed_at=NOW,
    )

    changed = EvidenceSanitizationRepository(
        _Pool(
            _Connection(
                rows=[
                    {
                        "state": EvidenceUploadState.FAILED.value,
                        "failure_code": EvidenceUploadFailureCode.OBJECT_CHANGED.value,
                    }
                ],
                values=[None],
            )
        )
    )
    with pytest.raises(RuntimeError, match="terminal state already differs"):
        await changed.record_failure(
            claim,
            failure_code=EvidenceUploadFailureCode.DECODE_FAILED,
            failed_at=NOW,
        )


@pytest.mark.asyncio
async def test_repository_result_rejects_unknown_upload() -> None:
    repository = EvidenceSanitizationRepository(_Pool(_Connection(rows=[None])))
    with pytest.raises(LookupError, match="Unknown evidence upload"):
        await repository.result(uuid4())


@pytest.mark.asyncio
async def test_repository_result_returns_safe_sanitized_identity() -> None:
    upload_id = uuid4()
    repository = EvidenceSanitizationRepository(
        _Pool(
            _Connection(
                rows=[
                    {
                        "state": EvidenceUploadState.SANITIZED.value,
                        "sanitized_object_key": "sanitized/" + "c" * 64 + ".png",
                        "sanitized_sha256": "c" * 64,
                    }
                ]
            )
        )
    )
    result = await repository.result(upload_id)
    assert result.upload_id == upload_id
    assert result.state is EvidenceUploadState.SANITIZED
