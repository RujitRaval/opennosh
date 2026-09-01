from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from opennosh_api.evidence.sanitization import (
    EvidenceContentScanner,
    EvidenceSanitizationError,
    EvidenceSanitizationFailureCode,
    SanitizedEvidenceImage,
    sanitize_evidence_image,
)
from opennosh_api.evidence.storage import (
    EvidenceQuarantineSource,
    EvidenceStore,
    EvidenceUploadObjectTooLargeError,
    EvidenceUploadStorageError,
    ImmutableObjectConflictError,
)
from opennosh_api.evidence.uploads import EvidenceUploadFailureCode, EvidenceUploadState


@dataclass(frozen=True, slots=True)
class EvidenceSanitizationClaim:
    upload_id: UUID
    workflow_revision: int
    object_key: str
    declared_media_type: str
    observed_byte_length: int
    observed_sha256: str
    observed_revision_sha256: str


@dataclass(frozen=True, slots=True)
class EvidenceSanitizationResult:
    upload_id: UUID
    state: EvidenceUploadState
    sanitized_object_key: str | None = None
    sanitized_sha256: str | None = None


class EvidenceSanitizationRepository:
    """Own short DB sections on either side of sanitizer provider I/O."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def claim(
        self,
        upload_id: UUID,
        *,
        expected_revision: int,
    ) -> EvidenceSanitizationClaim | None:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    """
                    SELECT id, state, version, object_key, declared_media_type,
                           observed_byte_length, observed_sha256,
                           observed_revision_sha256
                    FROM evidence_upload_sessions
                    WHERE id = $1
                    FOR UPDATE
                    """,
                    upload_id,
                )
                if row is None:
                    raise LookupError(f"Unknown evidence upload: {upload_id}")
                state = EvidenceUploadState(row["state"])
                if state in {
                    EvidenceUploadState.SANITIZED,
                    EvidenceUploadState.ATTACHED,
                    EvidenceUploadState.PRESERVED,
                }:
                    return None
                if state is EvidenceUploadState.FAILED:
                    raise RuntimeError("Failed evidence upload cannot be sanitized")
                if state is EvidenceUploadState.UPLOADED:
                    if row["version"] != expected_revision:
                        return None
                    row = await connection.fetchrow(
                        """
                        UPDATE evidence_upload_sessions
                        SET state = 'sanitizing', version = version + 1, updated_at = now()
                        WHERE id = $1 AND state = 'uploaded' AND version = $2
                        RETURNING id, state, version, object_key, declared_media_type,
                                  observed_byte_length, observed_sha256,
                                  observed_revision_sha256
                        """,
                        upload_id,
                        row["version"],
                    )
                    if row is None:
                        raise RuntimeError("Evidence sanitization claim changed concurrently")
                elif state is EvidenceUploadState.SANITIZING:
                    if row["version"] != expected_revision + 1:
                        return None
                else:
                    raise RuntimeError(f"Evidence upload state cannot be sanitized: {state.value}")
                if (
                    row["observed_byte_length"] is None
                    or row["observed_sha256"] is None
                    or row["observed_revision_sha256"] is None
                ):
                    raise RuntimeError("Evidence sanitization claim lacks an upload observation")
                return EvidenceSanitizationClaim(
                    upload_id=row["id"],
                    workflow_revision=row["version"],
                    object_key=row["object_key"],
                    declared_media_type=row["declared_media_type"],
                    observed_byte_length=row["observed_byte_length"],
                    observed_sha256=row["observed_sha256"],
                    observed_revision_sha256=row["observed_revision_sha256"],
                )

    async def record_sanitized(
        self,
        claim: EvidenceSanitizationClaim,
        image: SanitizedEvidenceImage,
        *,
        object_key: str,
        sanitized_at: datetime,
    ) -> EvidenceSanitizationResult:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                UPDATE evidence_upload_sessions
                SET state = 'sanitized', sanitized_object_key = $3,
                    sanitized_media_type = $4, sanitized_byte_length = $5,
                    sanitized_sha256 = $6, sanitized_width = $7,
                    sanitized_height = $8,
                    sanitized_at = GREATEST($9, uploaded_at, updated_at),
                    version = version + 1,
                    updated_at = GREATEST($9, uploaded_at, updated_at)
                WHERE id = $1 AND state = 'sanitizing' AND version = $2
                RETURNING state, sanitized_object_key, sanitized_sha256
                """,
                claim.upload_id,
                claim.workflow_revision,
                object_key,
                image.media_type,
                len(image.payload),
                image.content_digest,
                image.width,
                image.height,
                sanitized_at,
            )
        if row is not None:
            return EvidenceSanitizationResult(
                upload_id=claim.upload_id,
                state=EvidenceUploadState(row["state"]),
                sanitized_object_key=row["sanitized_object_key"],
                sanitized_sha256=row["sanitized_sha256"],
            )
        existing = await self.result(claim.upload_id)
        if (
            existing.state
            in {
                EvidenceUploadState.SANITIZED,
                EvidenceUploadState.ATTACHED,
                EvidenceUploadState.PRESERVED,
            }
            and existing.sanitized_object_key == object_key
            and existing.sanitized_sha256 == image.content_digest
        ):
            return existing
        raise RuntimeError("Evidence sanitization result changed concurrently")

    async def record_failure(
        self,
        claim: EvidenceSanitizationClaim,
        *,
        failure_code: EvidenceUploadFailureCode,
        failed_at: datetime,
    ) -> None:
        async with self._pool.acquire() as connection:
            updated = await connection.fetchval(
                """
                UPDATE evidence_upload_sessions
                SET state = 'failed', failure_code = $3,
                    failed_at = GREATEST($4, created_at, updated_at),
                    version = version + 1,
                    updated_at = GREATEST($4, created_at, updated_at)
                WHERE id = $1 AND state = 'sanitizing' AND version = $2
                RETURNING id
                """,
                claim.upload_id,
                claim.workflow_revision,
                failure_code.value,
                failed_at,
            )
            if updated is not None:
                return
            existing = await connection.fetchrow(
                "SELECT state, failure_code FROM evidence_upload_sessions WHERE id = $1",
                claim.upload_id,
            )
        if existing is not None and existing["state"] == EvidenceUploadState.FAILED.value:
            if existing["failure_code"] == failure_code.value:
                return
        raise RuntimeError("Evidence sanitization terminal state already differs")

    async def result(self, upload_id: UUID) -> EvidenceSanitizationResult:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT state, sanitized_object_key, sanitized_sha256
                FROM evidence_upload_sessions WHERE id = $1
                """,
                upload_id,
            )
        if row is None:
            raise LookupError(f"Unknown evidence upload: {upload_id}")
        return EvidenceSanitizationResult(
            upload_id=upload_id,
            state=EvidenceUploadState(row["state"]),
            sanitized_object_key=row["sanitized_object_key"],
            sanitized_sha256=row["sanitized_sha256"],
        )


class EvidenceSanitizationJobProcessor:
    def __init__(
        self,
        repository: EvidenceSanitizationRepository,
        quarantine: EvidenceQuarantineSource,
        sanitized_store: EvidenceStore,
        scanner: EvidenceContentScanner,
        *,
        clock: Any,
        max_bytes: int,
    ) -> None:
        self._repository = repository
        self._quarantine = quarantine
        self._sanitized_store = sanitized_store
        self._scanner = scanner
        self._clock = clock
        self._max_bytes = max_bytes

    async def process(
        self,
        upload_id: UUID,
        *,
        workflow_revision: int,
    ) -> EvidenceSanitizationResult:
        claim = await self._repository.claim(
            upload_id,
            expected_revision=workflow_revision,
        )
        if claim is None:
            return await self._repository.result(upload_id)
        try:
            try:
                source = await self._quarantine.read(
                    claim.object_key,
                    max_bytes=self._max_bytes,
                )
            except EvidenceUploadObjectTooLargeError as error:
                raise _UploadFailure(EvidenceUploadFailureCode.SIZE_EXCEEDED) from error
            except EvidenceUploadStorageError as error:
                raise _UploadFailure(
                    EvidenceUploadFailureCode.STORAGE_UNAVAILABLE,
                    retryable=True,
                ) from error
            if source is None:
                raise _UploadFailure(
                    EvidenceUploadFailureCode.OBJECT_MISSING,
                    retryable=True,
                )
            observed = source.observation
            if (
                observed.size_bytes != claim.observed_byte_length
                or observed.content_digest != claim.observed_sha256
                or hashlib.sha256(observed.revision.encode("utf-8")).hexdigest()
                != claim.observed_revision_sha256
                or observed.media_type != claim.declared_media_type
            ):
                raise _UploadFailure(EvidenceUploadFailureCode.OBJECT_CHANGED)
            image = await asyncio.to_thread(
                sanitize_evidence_image,
                source.payload,
                declared_media_type=claim.declared_media_type,
                max_bytes=self._max_bytes,
            )
            try:
                await self._scanner.scan(image)
            except EvidenceSanitizationError as error:
                raise _UploadFailure(
                    EvidenceUploadFailureCode(error.code.value),
                    retryable=(
                        error.code is EvidenceSanitizationFailureCode.SCANNER_UNAVAILABLE
                    ),
                ) from error
            object_key = f"sanitized/{image.content_digest}.png"
            try:
                await self._sanitized_store.put_immutable(
                    object_key,
                    image.payload,
                    expected_digest=image.content_digest,
                )
            except ImmutableObjectConflictError as error:
                raise _UploadFailure(
                    EvidenceUploadFailureCode.SANITIZED_STORAGE_CONFLICT
                ) from error
            except EvidenceUploadStorageError as error:
                raise _UploadFailure(
                    EvidenceUploadFailureCode.SANITIZED_STORAGE_UNAVAILABLE,
                    retryable=True,
                ) from error
            result = await self._repository.record_sanitized(
                claim,
                image,
                object_key=object_key,
                sanitized_at=self._clock(),
            )
            try:
                await self._quarantine.delete(claim.object_key)
            except EvidenceUploadStorageError:
                pass
            return result
        except asyncio.CancelledError:
            raise
        except EvidenceSanitizationJobError:
            raise
        except _UploadFailure as error:
            raise EvidenceSanitizationJobError(
                claim,
                failure_code=error.code,
                retryable=error.retryable,
            ) from error
        except EvidenceSanitizationError as error:
            raise EvidenceSanitizationJobError(
                claim,
                failure_code=EvidenceUploadFailureCode(error.code.value),
                retryable=False,
            ) from error
        except Exception as error:
            raise EvidenceSanitizationJobError(
                claim,
                failure_code=EvidenceUploadFailureCode.METADATA_REWRITE_FAILED,
                retryable=False,
            ) from error

    async def record_terminal_failure(self, error: EvidenceSanitizationJobError) -> None:
        await self._repository.record_failure(
            error.claim,
            failure_code=error.failure_code,
            failed_at=self._clock(),
        )


class EvidenceSanitizationJobError(RuntimeError):
    def __init__(
        self,
        claim: EvidenceSanitizationClaim,
        *,
        failure_code: EvidenceUploadFailureCode,
        retryable: bool,
    ) -> None:
        self.claim = claim
        self.failure_code = failure_code
        self.retryable = retryable
        super().__init__(failure_code.value)


class _UploadFailure(RuntimeError):
    def __init__(
        self,
        code: EvidenceUploadFailureCode,
        *,
        retryable: bool = False,
    ) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(code.value)
