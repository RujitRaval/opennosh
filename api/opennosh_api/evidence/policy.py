from __future__ import annotations

from collections.abc import Iterable

from opennosh_api.evidence.contracts import (
    EvidenceAcknowledgement,
    EvidenceAcknowledgementKind,
    EvidenceClass,
    EvidenceManifest,
    EvidencePublicState,
    PublicDocumentManifest,
    SanitizedMediaManifest,
    VersionedPublicDatasetManifest,
    manifest_digest,
)


class EvidenceDurabilityError(ValueError):
    def __init__(self, code: str, *, missing: tuple[EvidenceAcknowledgementKind, ...] = ()) -> None:
        super().__init__(code)
        self.code = code
        self.missing = missing


def required_acknowledgements(
    manifest: EvidenceManifest,
) -> tuple[EvidenceAcknowledgementKind, ...]:
    if manifest.evidence_class is EvidenceClass.SANITIZED_MEDIA:
        return (EvidenceAcknowledgementKind.IMMUTABLE_SANITIZED_COPY,)
    if manifest.evidence_class is EvidenceClass.VERSIONED_PUBLIC_DATASET:
        dataset = manifest
        assert isinstance(dataset, VersionedPublicDatasetManifest)
        return (
            (
                EvidenceAcknowledgementKind.DATASET_SNAPSHOT,
                EvidenceAcknowledgementKind.SIGNED_DATASET_MANIFEST,
            )
            if dataset.archival_permitted
            else (EvidenceAcknowledgementKind.SIGNED_DATASET_MANIFEST,)
        )
    if manifest.evidence_class is EvidenceClass.PUBLIC_DOCUMENT:
        document = manifest
        assert isinstance(document, PublicDocumentManifest)
        return (
            (EvidenceAcknowledgementKind.ARCHIVED_DOCUMENT,)
            if document.storage_reference is not None
            else (EvidenceAcknowledgementKind.CITATION_MANIFEST,)
        )
    return (EvidenceAcknowledgementKind.SIGNED_ATTESTATION,)


def verify_durability(
    manifest: EvidenceManifest,
    acknowledgements: Iterable[EvidenceAcknowledgement],
) -> EvidencePublicState:
    expected_manifest_digest = manifest_digest(manifest)
    indexed: dict[EvidenceAcknowledgementKind, EvidenceAcknowledgement] = {}
    for acknowledgement in acknowledgements:
        if acknowledgement.evidence_id != manifest.evidence_id:
            raise EvidenceDurabilityError("acknowledgement_evidence_mismatch")
        if acknowledgement.evidence_class is not manifest.evidence_class:
            raise EvidenceDurabilityError("acknowledgement_class_mismatch")
        if acknowledgement.manifest_digest != expected_manifest_digest:
            raise EvidenceDurabilityError("acknowledgement_manifest_digest_mismatch")
        if acknowledgement.kind in indexed:
            raise EvidenceDurabilityError("duplicate_acknowledgement_kind")
        indexed[acknowledgement.kind] = acknowledgement

    required = required_acknowledgements(manifest)
    missing = tuple(kind for kind in required if kind not in indexed)
    if missing:
        raise EvidenceDurabilityError("durable_acknowledgement_missing", missing=missing)

    if isinstance(manifest, SanitizedMediaManifest):
        if (
            indexed[EvidenceAcknowledgementKind.IMMUTABLE_SANITIZED_COPY].content_digest
            != manifest.content_digest
        ):
            raise EvidenceDurabilityError("sanitized_media_digest_mismatch")
        return EvidencePublicState.EVIDENCE_PRESERVED
    if isinstance(manifest, VersionedPublicDatasetManifest):
        snapshot = indexed.get(EvidenceAcknowledgementKind.DATASET_SNAPSHOT)
        if snapshot is not None and snapshot.content_digest != manifest.canonical_record_digest:
            raise EvidenceDurabilityError("dataset_snapshot_digest_mismatch")
        if (
            indexed[EvidenceAcknowledgementKind.SIGNED_DATASET_MANIFEST].content_digest
            != expected_manifest_digest
        ):
            raise EvidenceDurabilityError("dataset_manifest_digest_mismatch")
        return EvidencePublicState.SOURCE_VERIFIED
    if isinstance(manifest, PublicDocumentManifest):
        archived = indexed.get(EvidenceAcknowledgementKind.ARCHIVED_DOCUMENT)
        if archived is not None:
            if archived.content_digest != manifest.observed_digest:
                raise EvidenceDurabilityError("document_archive_digest_mismatch")
            return EvidencePublicState.REFERENCE_PRESERVED
        if (
            indexed[EvidenceAcknowledgementKind.CITATION_MANIFEST].content_digest
            != expected_manifest_digest
        ):
            raise EvidenceDurabilityError("citation_manifest_digest_mismatch")
        return EvidencePublicState.REFERENCE_ONLY
    if (
        indexed[EvidenceAcknowledgementKind.SIGNED_ATTESTATION].content_digest
        != expected_manifest_digest
    ):
        raise EvidenceDurabilityError("attestation_manifest_digest_mismatch")
    return EvidencePublicState.ATTESTED
