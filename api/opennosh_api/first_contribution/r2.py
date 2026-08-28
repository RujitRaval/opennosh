from __future__ import annotations

from datetime import datetime

from opennosh_api.evidence.contracts import (
    EvidenceAcknowledgement,
    EvidenceAcknowledgementKind,
    PublicDocumentManifest,
    canonical_manifest_bytes,
    manifest_digest,
)
from opennosh_api.public.r2 import (
    R2ImmutableConflictError,
    R2PublicationError,
    S3R2ObjectWriter,
)

MAX_CITATION_BYTES = 256 * 1024


class FirstContributionEvidenceConflictError(RuntimeError):
    pass


class R2FirstContributionEvidenceStore:
    identity = "opennosh.r2-first-contribution-citation"
    version = "1.0"

    def __init__(
        self,
        *,
        writer: S3R2ObjectWriter,
        bucket: str,
    ) -> None:
        if not bucket:
            raise ValueError("First-contribution evidence bucket is required")
        self._writer = writer
        self._bucket = bucket

    async def preserve(
        self,
        manifest: PublicDocumentManifest,
        *,
        now: datetime,
    ) -> EvidenceAcknowledgement:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Evidence verification time must include a timezone")
        digest = manifest_digest(manifest)
        payload = canonical_manifest_bytes(manifest)
        if len(payload) > MAX_CITATION_BYTES:
            raise ValueError("First-contribution citation manifest is too large")
        object_key = f"evidence/citations/v1/{digest}.json"
        existing = await self._writer.read_optional_bytes(
            bucket=self._bucket,
            object_key=object_key,
            max_bytes=MAX_CITATION_BYTES,
        )
        if existing is not None and existing != payload:
            raise FirstContributionEvidenceConflictError(
                "Immutable first-contribution evidence already has different bytes"
            )
        if existing is None:
            try:
                await self._writer.put_bytes(
                    bucket=self._bucket,
                    object_key=object_key,
                    payload=payload,
                    media_type="application/vnd.opennosh.evidence-manifest+json",
                    cache_control="private, max-age=31536000, immutable",
                    if_none_match="*",
                )
            except (R2ImmutableConflictError, R2PublicationError):
                recovered = await self._writer.read_optional_bytes(
                    bucket=self._bucket,
                    object_key=object_key,
                    max_bytes=MAX_CITATION_BYTES,
                )
                if recovered == payload:
                    pass
                elif recovered is not None:
                    raise FirstContributionEvidenceConflictError(
                        "Immutable first-contribution evidence won a race with different bytes"
                    ) from None
                else:
                    raise
        observed = await self._writer.read_optional_bytes(
            bucket=self._bucket,
            object_key=object_key,
            max_bytes=MAX_CITATION_BYTES,
        )
        if observed != payload:
            raise FirstContributionEvidenceConflictError(
                "First-contribution evidence read-back did not match"
            )
        reference = f"r2://{self._bucket}/{object_key}"
        return EvidenceAcknowledgement(
            evidence_id=manifest.evidence_id,
            evidence_class=manifest.evidence_class,
            manifest_digest=digest,
            kind=EvidenceAcknowledgementKind.CITATION_MANIFEST,
            destination=f"r2://{self._bucket}",
            content_digest=digest,
            external_reference=reference,
            verified_at=now,
            adapter_identity=self.identity,
            adapter_version=self.version,
        )
