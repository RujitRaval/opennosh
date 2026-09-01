from __future__ import annotations

import asyncio
import json
import signal
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg  # type: ignore[import-untyped]
from pgqueuer import PgQueuer
from pgqueuer.db import AsyncpgPoolDriver
from pgqueuer.errors import RetryRequested
from pgqueuer.models import Channel, Job

from opennosh_api.capacity import ProcessRole, load_capacity_manifest
from opennosh_api.evidence.contracts import (
    EvidenceAcknowledgement,
    EvidenceAcknowledgementKind,
    EvidenceClass,
    EvidenceManifest,
    EvidencePublicState,
    parse_manifest,
)
from opennosh_api.evidence.policy import verify_durability
from opennosh_api.evidence.signing import EvidenceSignatureError, EvidenceVerificationKeyRing
from opennosh_api.evidence.storage import (
    EvidenceSource,
    EvidenceStore,
    ImmutableObjectConflictError,
    LocalImmutableEvidenceStore,
    LocalPrivateEvidenceSource,
    S3ImmutableEvidenceStore,
    S3PrivateEvidenceSource,
)
from opennosh_api.evidence.worker import (
    EvidencePreservationWorker,
    EvidenceSourceUnavailableError,
)
from opennosh_api.jobs.pgqueuer import (
    EVIDENCE_ENTRYPOINT,
    PGQUEUER_SETTINGS,
    build_queries,
    decode_message,
)
from opennosh_api.jobs.worker import PgQueuerRoleDriver, asyncpg_dsn
from opennosh_api.runtime import supervise_role
from opennosh_api.settings import Settings, get_settings

EVIDENCE_DRAIN_TIMEOUT_SECONDS = 30.0
EVIDENCE_FAILURE_RETRY_DELAY = timedelta(seconds=65)
EVIDENCE_MAX_UNEXPECTED_ATTEMPTS = 5


class EvidenceWorkerRepository:
    """Keep database sections on either side of evidence-store I/O."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def load_manifest(self, evidence_id: UUID) -> EvidenceManifest:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT manifest_json, public_state, preservation_failure_code
                FROM evidence_manifests
                WHERE id = $1
                """,
                evidence_id,
            )
        if row is None:
            raise LookupError(f"Unknown evidence manifest: {evidence_id}")
        if row["public_state"] == EvidencePublicState.TOMBSTONED.value:
            raise RuntimeError("Tombstoned evidence cannot be preserved")
        if row["preservation_failure_code"] is not None:
            raise RuntimeError("Terminally failed evidence cannot be preserved")
        return parse_manifest(_json_object(row["manifest_json"]))

    async def record_acknowledgements(
        self,
        manifest: EvidenceManifest,
        acknowledgements: tuple[EvidenceAcknowledgement, ...],
    ) -> EvidencePublicState:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                manifest_row = await connection.fetchrow(
                    """
                    SELECT public_state, preservation_failure_code
                    FROM evidence_manifests
                    WHERE id = $1
                    FOR UPDATE
                    """,
                    manifest.evidence_id,
                )
                if manifest_row is None:
                    raise LookupError(f"Unknown evidence manifest: {manifest.evidence_id}")
                state = manifest_row["public_state"]
                if state == EvidencePublicState.TOMBSTONED.value:
                    raise RuntimeError("Tombstoned evidence cannot receive acknowledgements")
                if manifest_row["preservation_failure_code"] is not None:
                    raise RuntimeError("Terminally failed evidence cannot receive acknowledgements")
                for acknowledgement in acknowledgements:
                    await self._insert_or_compare(connection, acknowledgement)
                rows = await connection.fetch(
                    """
                    SELECT schema_version, evidence_id, evidence_class, manifest_digest,
                           acknowledgement_kind, destination, content_digest,
                           external_reference, verified_at, adapter_identity, adapter_version
                    FROM evidence_durable_acknowledgements
                    WHERE evidence_id = $1
                    ORDER BY acknowledgement_kind, destination
                    """,
                    manifest.evidence_id,
                )
                public_state = verify_durability(
                    manifest,
                    tuple(_acknowledgement(row) for row in rows),
                )
                await connection.execute(
                    "UPDATE evidence_manifests SET public_state = $2 WHERE id = $1",
                    manifest.evidence_id,
                    public_state.value,
                )
                return public_state

    async def record_terminal_failure(
        self,
        evidence_id: UUID,
        *,
        failure_code: str,
        failed_at: datetime,
    ) -> None:
        async with self._pool.acquire() as connection:
            updated = await connection.fetchrow(
                """
                UPDATE evidence_manifests
                SET preservation_failure_code = $2, preservation_failed_at = $3
                WHERE id = $1
                  AND public_state IS NULL
                  AND preservation_failure_code IS NULL
                RETURNING id
                """,
                evidence_id,
                failure_code,
                failed_at,
            )
            if updated is not None:
                return
            existing = await connection.fetchrow(
                """
                SELECT public_state, preservation_failure_code
                FROM evidence_manifests
                WHERE id = $1
                """,
                evidence_id,
            )
        if existing is None:
            raise LookupError(f"Unknown evidence manifest: {evidence_id}")
        if existing["preservation_failure_code"] == failure_code:
            return
        raise RuntimeError("Evidence terminal state already differs")

    @staticmethod
    async def _insert_or_compare(
        connection: Any,
        acknowledgement: EvidenceAcknowledgement,
    ) -> None:
        inserted = await connection.fetchrow(
            """
            INSERT INTO evidence_durable_acknowledgements (
                evidence_id, schema_version, evidence_class, manifest_digest,
                acknowledgement_kind, destination, content_digest, external_reference,
                verified_at, adapter_identity, adapter_version
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            ON CONFLICT (evidence_id, acknowledgement_kind, destination) DO NOTHING
            RETURNING id
            """,
            acknowledgement.evidence_id,
            acknowledgement.schema_version,
            acknowledgement.evidence_class.value,
            acknowledgement.manifest_digest,
            acknowledgement.kind.value,
            acknowledgement.destination,
            acknowledgement.content_digest,
            acknowledgement.external_reference,
            acknowledgement.verified_at,
            acknowledgement.adapter_identity,
            acknowledgement.adapter_version,
        )
        if inserted is not None:
            return
        existing = await connection.fetchrow(
            """
            SELECT schema_version, evidence_id, evidence_class, manifest_digest,
                   acknowledgement_kind, destination, content_digest, external_reference,
                   verified_at, adapter_identity, adapter_version
            FROM evidence_durable_acknowledgements
            WHERE evidence_id = $1 AND acknowledgement_kind = $2 AND destination = $3
            """,
            acknowledgement.evidence_id,
            acknowledgement.kind.value,
            acknowledgement.destination,
        )
        if existing is None or not _same_durable_proof(_acknowledgement(existing), acknowledgement):
            raise RuntimeError(
                "Durable evidence acknowledgement already exists with different proof"
            )


class EvidenceJobProcessor:
    def __init__(
        self,
        repository: EvidenceWorkerRepository,
        source: EvidenceSource,
        store: EvidenceStore,
        *,
        key_ring: EvidenceVerificationKeyRing | None = None,
        clock: Any = None,
    ) -> None:
        self._repository = repository
        self._source = source
        self._preserver = EvidencePreservationWorker(store, key_ring=key_ring)
        self._clock = clock or (lambda: datetime.now(UTC))

    async def process(self, evidence_id: UUID) -> EvidencePublicState:
        manifest = await self._repository.load_manifest(evidence_id)
        payloads = await self._source.payloads_for(manifest)
        acknowledgements = await self._preserver.preserve(
            manifest,
            payloads=payloads,
            now=self._clock(),
        )
        return await self._repository.record_acknowledgements(manifest, acknowledgements)

    async def record_terminal_failure(self, evidence_id: UUID, error: Exception) -> None:
        await self._repository.record_terminal_failure(
            evidence_id,
            failure_code=_terminal_failure_code(error),
            failed_at=self._clock(),
        )


async def process_evidence_wakeup(processor: EvidenceJobProcessor, job: Job) -> None:
    message = decode_message(job.payload)
    if message.job_type != "evidence.preserve":
        raise ValueError("Evidence worker received a non-evidence job")
    try:
        await processor.process(message.subject_id)
    except asyncio.CancelledError as error:
        raise RetryRequested(
            delay=EVIDENCE_FAILURE_RETRY_DELAY,
            reason="evidence recovery after worker cancellation",
        ) from error
    except Exception as error:
        if int(job.attempts) >= EVIDENCE_MAX_UNEXPECTED_ATTEMPTS:
            await processor.record_terminal_failure(message.subject_id, error)
            raise
        raise RetryRequested(
            delay=EVIDENCE_FAILURE_RETRY_DELAY,
            reason=f"evidence recovery after {type(error).__name__}",
        ) from error


def _terminal_failure_code(error: Exception) -> str:
    if isinstance(error, EvidenceSignatureError):
        return "signature_verification_failed"
    if isinstance(error, EvidenceSourceUnavailableError | FileNotFoundError):
        return "source_unavailable"
    if isinstance(error, ImmutableObjectConflictError):
        return "immutable_storage_conflict"
    if isinstance(error, ValueError):
        return "evidence_verification_failed"
    return "unexpected_preservation_failure"


async def create_evidence_role_driver(
    settings: Settings | None = None,
    *,
    source: EvidenceSource | None = None,
    store: EvidenceStore | None = None,
) -> PgQueuerRoleDriver:
    configured = settings or get_settings()
    manifest = load_capacity_manifest(configured.database_capacity_manifest_path)
    budget = manifest.active_role_budget(ProcessRole.EVIDENCE)
    if source is None:
        if configured.evidence_sanitized_endpoint is not None:
            assert configured.evidence_sanitized_region is not None
            assert configured.evidence_sanitized_bucket is not None
            assert configured.evidence_sanitized_access_key_id is not None
            assert configured.evidence_sanitized_secret_access_key is not None
            source = S3PrivateEvidenceSource(
                endpoint=configured.evidence_sanitized_endpoint,
                region=configured.evidence_sanitized_region,
                bucket=configured.evidence_sanitized_bucket,
                access_key_id=(configured.evidence_sanitized_access_key_id.get_secret_value()),
                secret_access_key=(
                    configured.evidence_sanitized_secret_access_key.get_secret_value()
                ),
                max_bytes=configured.evidence_upload_max_bytes,
            )
        elif configured.evidence_private_source_directory is not None:
            source = LocalPrivateEvidenceSource(configured.evidence_private_source_directory)
        else:
            raise ValueError("Evidence worker requires a private source adapter")
    if store is None:
        if configured.evidence_immutable_endpoint is not None:
            assert configured.evidence_immutable_region is not None
            assert configured.evidence_immutable_bucket is not None
            assert configured.evidence_immutable_access_key_id is not None
            assert configured.evidence_immutable_secret_access_key is not None
            store = S3ImmutableEvidenceStore(
                endpoint=configured.evidence_immutable_endpoint,
                region=configured.evidence_immutable_region,
                bucket=configured.evidence_immutable_bucket,
                access_key_id=(configured.evidence_immutable_access_key_id.get_secret_value()),
                secret_access_key=(
                    configured.evidence_immutable_secret_access_key.get_secret_value()
                ),
                max_bytes=configured.evidence_upload_max_bytes,
            )
        elif configured.evidence_immutable_directory is not None:
            store = LocalImmutableEvidenceStore(configured.evidence_immutable_directory)
        else:
            raise ValueError("Evidence worker requires an immutable destination adapter")
    pool = await asyncpg.create_pool(
        dsn=asyncpg_dsn(configured.process_database_url(ProcessRole.EVIDENCE)),
        min_size=1,
        max_size=budget.pool_size,
        timeout=budget.acquisition_timeout_ms / 1000,
        server_settings={
            "application_name": (
                f"opennosh:{manifest.deployment_id}:{ProcessRole.EVIDENCE.value}"[:63]
            ),
            "statement_timeout": str(budget.statement_timeout_ms),
        },
    )
    if pool is None:
        raise RuntimeError("asyncpg did not create the evidence pool")
    driver = AsyncpgPoolDriver(pool)
    queue = PgQueuer(
        connection=driver,
        channel=Channel(PGQUEUER_SETTINGS.channel),
        queries=build_queries(driver),
    )
    processor = EvidenceJobProcessor(
        EvidenceWorkerRepository(pool),
        source,
        store,
        key_ring=EvidenceVerificationKeyRing.from_config(
            configured.evidence_verifying_keys.get_secret_value()
        ),
    )

    @queue.entrypoint(
        EVIDENCE_ENTRYPOINT,
        concurrency_limit=max(
            1,
            min(
                budget.max_in_flight_database_sections,
                budget.pool_size - 1,
            ),
        ),
        on_failure="hold",
    )
    async def evidence_preservation(job: Job) -> None:
        await process_evidence_wakeup(processor, job)

    return PgQueuerRoleDriver(
        queue,
        pool,
        worker_concurrency=budget.worker_concurrency,
        task_name="opennosh-evidence-pgqueuer",
    )


async def _run_evidence_worker() -> None:
    driver = await create_evidence_role_driver()
    shutdown_requested = asyncio.Event()
    loop = asyncio.get_running_loop()
    for shutdown_signal in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(shutdown_signal, shutdown_requested.set)
    await supervise_role(
        driver,
        shutdown_requested,
        drain_timeout_seconds=EVIDENCE_DRAIN_TIMEOUT_SECONDS,
    )


def run_evidence_worker() -> int:
    asyncio.run(_run_evidence_worker())
    return 0


def _json_object(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    parsed = json.loads(value) if isinstance(value, str) else None
    if not isinstance(parsed, dict):
        raise ValueError("Evidence manifest storage must be a JSON object")
    return parsed


def _acknowledgement(row: Any) -> EvidenceAcknowledgement:
    return EvidenceAcknowledgement(
        schema_version="1.0",
        evidence_id=row["evidence_id"],
        evidence_class=EvidenceClass(str(row["evidence_class"])),
        manifest_digest=str(row["manifest_digest"]),
        kind=EvidenceAcknowledgementKind(str(row["acknowledgement_kind"])),
        destination=str(row["destination"]),
        content_digest=str(row["content_digest"]),
        external_reference=str(row["external_reference"]),
        verified_at=row["verified_at"],
        adapter_identity=str(row["adapter_identity"]),
        adapter_version=str(row["adapter_version"]),
    )


def _same_durable_proof(left: EvidenceAcknowledgement, right: EvidenceAcknowledgement) -> bool:
    return left.model_copy(update={"verified_at": right.verified_at}) == right
