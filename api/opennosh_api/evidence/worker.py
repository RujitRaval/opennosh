from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from opennosh_api.evidence.contracts import (
    EvidenceAcknowledgement,
    EvidenceAcknowledgementKind,
    EvidenceClass,
    EvidenceManifest,
    PublicDocumentManifest,
    SanitizedMediaManifest,
    VersionedPublicDatasetManifest,
    canonical_manifest_bytes,
    manifest_digest,
)
from opennosh_api.evidence.policy import required_acknowledgements, verify_durability
from opennosh_api.evidence.signing import EvidenceVerificationKeyRing
from opennosh_api.evidence.storage import EvidenceStore


class EvidenceSourceUnavailableError(RuntimeError):
    pass


class EvidencePreservationWorker:
    """Preserve one manifest without holding a database connection during storage I/O."""

    def __init__(
        self,
        store: EvidenceStore,
        *,
        key_ring: EvidenceVerificationKeyRing | None = None,
    ) -> None:
        self._store = store
        self._key_ring = key_ring or EvidenceVerificationKeyRing.empty()

    async def preserve(
        self,
        manifest: EvidenceManifest,
        *,
        payloads: Mapping[EvidenceAcknowledgementKind, bytes],
        now: datetime,
    ) -> tuple[EvidenceAcknowledgement, ...]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Evidence preservation time must include a timezone")
        self._key_ring.verify(manifest)
        digest = manifest_digest(manifest)
        acknowledgements: list[EvidenceAcknowledgement] = []
        for kind in required_acknowledgements(manifest):
            payload, content_digest = _payload_for(manifest, kind, payloads, digest)
            object_key = f"evidence/{manifest.evidence_id}/{kind.value}/{content_digest}"
            await self._store.put_immutable(
                object_key,
                payload,
                expected_digest=content_digest,
            )
            observation = await self._store.observe(object_key)
            if observation is None:
                raise EvidenceSourceUnavailableError(
                    f"Evidence destination did not acknowledge {kind.value}"
                )
            if observation.content_digest != content_digest:
                raise ValueError("Evidence destination returned a mismatched digest")
            acknowledgements.append(
                EvidenceAcknowledgement(
                    evidence_id=manifest.evidence_id,
                    evidence_class=manifest.evidence_class,
                    manifest_digest=digest,
                    kind=kind,
                    destination=observation.destination,
                    content_digest=observation.content_digest,
                    external_reference=observation.external_reference,
                    verified_at=now,
                    adapter_identity=self._store.identity,
                    adapter_version=self._store.version,
                )
            )
        result = tuple(acknowledgements)
        verify_durability(manifest, result)
        return result


def _payload_for(
    manifest: EvidenceManifest,
    kind: EvidenceAcknowledgementKind,
    payloads: Mapping[EvidenceAcknowledgementKind, bytes],
    digest: str,
) -> tuple[bytes, str]:
    if kind in {
        EvidenceAcknowledgementKind.SIGNED_DATASET_MANIFEST,
        EvidenceAcknowledgementKind.CITATION_MANIFEST,
        EvidenceAcknowledgementKind.SIGNED_ATTESTATION,
    }:
        return canonical_manifest_bytes(manifest), digest
    payload = payloads.get(kind)
    if payload is None:
        raise EvidenceSourceUnavailableError(f"Evidence payload is unavailable for {kind.value}")
    if isinstance(manifest, SanitizedMediaManifest):
        expected = manifest.content_digest
    elif isinstance(manifest, VersionedPublicDatasetManifest):
        expected = manifest.canonical_record_digest
    elif isinstance(manifest, PublicDocumentManifest):
        expected = manifest.observed_digest
    else:
        raise AssertionError(
            "Unexpected payload-bearing evidence class: "
            f"{EvidenceClass(manifest.evidence_class)}"
        )
    return payload, expected
