from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from opennosh_api.evidence.contracts import (
    DocumentRightsState,
    EvidenceAcknowledgement,
    EvidenceAcknowledgementKind,
    EvidenceClass,
    EvidencePublicState,
    EvidenceTombstone,
    MaintainerAttestationManifest,
    PublicDocumentManifest,
    RedactionState,
    SanitizedMediaManifest,
    VersionedPublicDatasetManifest,
    canonical_manifest_bytes,
    manifest_digest,
    parse_manifest,
)
from opennosh_api.evidence.policy import EvidenceDurabilityError, verify_durability

NOW = datetime(2026, 8, 26, 12, tzinfo=UTC)
MEDIA = b"sanitized image"
DOCUMENT = b"archived source"
DATASET = b'{"record":"verified"}'


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def manifests() -> tuple[
    SanitizedMediaManifest,
    VersionedPublicDatasetManifest,
    PublicDocumentManifest,
    PublicDocumentManifest,
    MaintainerAttestationManifest,
]:
    return (
        SanitizedMediaManifest(
            evidence_id=uuid4(),
            content_digest=_digest(MEDIA),
            safe_format="image/webp",
            source_description="Rewritten package nutrition panel",
            rights_acknowledged=True,
            redaction_state=RedactionState.REVIEWED,
            storage_reference="private:media/fixture.webp",
        ),
        VersionedPublicDatasetManifest(
            evidence_id=uuid4(),
            dataset_id="usda-fdc",
            release_version="2026-08",
            record_id="12345",
            publisher="USDA",
            license="public-domain",
            source_uri="https://fdc.nal.usda.gov/fdc-app.html#/food-details/12345",
            canonical_record_digest=_digest(DATASET),
            signature_key_id="usda-2026",
            signature="A" * 86,
            archival_permitted=True,
            storage_reference="private:datasets/usda-2026-08/12345.json",
        ),
        PublicDocumentManifest(
            evidence_id=uuid4(),
            canonical_uri="https://example.test/nutrition.pdf",
            publisher="Example public health office",
            license="CC-BY-4.0",
            title="Nutrition facts",
            observed_at=NOW,
            observed_digest=_digest(DOCUMENT),
            rights_state=DocumentRightsState.ARCHIVE_PERMITTED,
            storage_reference="private:documents/nutrition.pdf",
        ),
        PublicDocumentManifest(
            evidence_id=uuid4(),
            canonical_uri="https://example.test/reference-only",
            publisher="Example publisher",
            license="all-rights-reserved",
            title="Restricted reference",
            observed_at=NOW,
            observed_digest=_digest(b"observed bytes"),
            rights_state=DocumentRightsState.REFERENCE_ONLY,
        ),
        MaintainerAttestationManifest(
            evidence_id=uuid4(),
            authority_id="maintainer:global-core:alice",
            scope="record:global-core/test-dal",
            signed_statement="I attest that the reviewed values match the named source.",
            signature_key_id="alice-2026",
            signature="A" * 86,
            attested_at=NOW,
            license="CC0-1.0",
            supporting_reference="https://example.test/attestation/source",
        ),
    )


@pytest.mark.parametrize("manifest", manifests())
def test_manifest_round_trip_and_digest_are_canonical(manifest: object) -> None:
    parsed = parse_manifest(manifest)
    reparsed = parse_manifest(parse_manifest(manifest).model_dump(mode="json"))

    assert parsed == reparsed
    assert manifest_digest(parsed) == _digest(canonical_manifest_bytes(parsed))


def test_sanitized_media_normalizes_and_rejects_blank_source_descriptions() -> None:
    media = manifests()[0]
    reparsed = SanitizedMediaManifest.model_validate(
        {**media.model_dump(), "source_description": "  Package label  "}
    )
    assert reparsed.source_description == "Package label"
    with pytest.raises(ValueError, match="Source description cannot be blank"):
        SanitizedMediaManifest.model_validate(
            {**media.model_dump(), "source_description": "   "}
        )


@pytest.mark.parametrize(
    ("manifest", "kind", "content_digest", "expected_state"),
    [
        (
            manifests()[0],
            EvidenceAcknowledgementKind.IMMUTABLE_SANITIZED_COPY,
            _digest(MEDIA),
            EvidencePublicState.EVIDENCE_PRESERVED,
        ),
        (
            manifests()[2],
            EvidenceAcknowledgementKind.ARCHIVED_DOCUMENT,
            _digest(DOCUMENT),
            EvidencePublicState.REFERENCE_PRESERVED,
        ),
    ],
)
def test_payload_backed_classes_require_matching_durable_digest(
    manifest: object,
    kind: EvidenceAcknowledgementKind,
    content_digest: str,
    expected_state: EvidencePublicState,
) -> None:
    parsed = parse_manifest(manifest)
    acknowledgement = EvidenceAcknowledgement(
        evidence_id=parsed.evidence_id,
        evidence_class=parsed.evidence_class,
        manifest_digest=manifest_digest(parsed),
        kind=kind,
        destination="urn:fixture:immutable",
        content_digest=content_digest,
        external_reference="fixture:object/1",
        verified_at=NOW,
        adapter_identity="fixture",
        adapter_version="1",
    )

    assert verify_durability(parsed, [acknowledgement]) is expected_state


def test_dataset_requires_snapshot_and_signed_manifest_when_archival_is_permitted() -> None:
    dataset = manifests()[1]
    signed = _ack(
        dataset,
        EvidenceAcknowledgementKind.SIGNED_DATASET_MANIFEST,
        manifest_digest(dataset),
    )

    with pytest.raises(EvidenceDurabilityError, match="durable_acknowledgement_missing") as error:
        verify_durability(dataset, [signed])

    assert error.value.missing == (EvidenceAcknowledgementKind.DATASET_SNAPSHOT,)
    assert verify_durability(
        dataset,
        [
            _ack(
                dataset,
                EvidenceAcknowledgementKind.DATASET_SNAPSHOT,
                dataset.canonical_record_digest,
            ),
            signed,
        ],
    ) is EvidencePublicState.SOURCE_VERIFIED


def test_reference_only_document_is_honest_without_archived_bytes() -> None:
    document = manifests()[3]
    acknowledgement = _ack(
        document,
        EvidenceAcknowledgementKind.CITATION_MANIFEST,
        manifest_digest(document),
    )

    assert verify_durability(document, [acknowledgement]) is EvidencePublicState.REFERENCE_ONLY


def test_attestation_never_claims_preserved_primary_evidence() -> None:
    attestation = manifests()[4]
    acknowledgement = _ack(
        attestation,
        EvidenceAcknowledgementKind.SIGNED_ATTESTATION,
        manifest_digest(attestation),
    )

    assert verify_durability(attestation, [acknowledgement]) is EvidencePublicState.ATTESTED


def test_tampered_manifest_and_payload_are_rejected() -> None:
    media = manifests()[0]
    acknowledgement = _ack(
        media,
        EvidenceAcknowledgementKind.IMMUTABLE_SANITIZED_COPY,
        _digest(b"tampered"),
    )

    with pytest.raises(EvidenceDurabilityError, match="sanitized_media_digest_mismatch"):
        verify_durability(media, [acknowledgement])

    changed = media.model_copy(update={"source_description": "Changed after review"})
    with pytest.raises(EvidenceDurabilityError, match="acknowledgement_manifest_digest_mismatch"):
        verify_durability(changed, [_ack(media, acknowledgement.kind, media.content_digest)])


def test_wrong_evidence_and_duplicate_acknowledgements_are_rejected() -> None:
    media = manifests()[0]
    acknowledgement = _ack(
        media,
        EvidenceAcknowledgementKind.IMMUTABLE_SANITIZED_COPY,
        media.content_digest,
    )
    wrong = acknowledgement.model_copy(update={"evidence_id": uuid4()})

    with pytest.raises(EvidenceDurabilityError, match="acknowledgement_evidence_mismatch"):
        verify_durability(media, [wrong])
    with pytest.raises(EvidenceDurabilityError, match="duplicate_acknowledgement_kind"):
        verify_durability(media, [acknowledgement, acknowledgement])


def test_public_document_requires_explicit_license() -> None:
    with pytest.raises(ValueError, match="license"):
        PublicDocumentManifest(
            evidence_id=uuid4(),
            canonical_uri="https://example.test/unlicensed",
            publisher="Publisher",
            title="Unlicensed",
            observed_at=NOW,
            observed_digest=_digest(b"source"),
            rights_state=DocumentRightsState.REFERENCE_ONLY,
        )



def test_rights_restricted_document_cannot_claim_archived_storage() -> None:
    with pytest.raises(ValueError, match="cannot claim a stored copy"):
        PublicDocumentManifest(
            evidence_id=uuid4(),
            canonical_uri="https://example.test/restricted",
            publisher="Publisher",
            license="all-rights-reserved",
            title="Restricted",
            observed_at=NOW,
            observed_digest=_digest(b"source"),
            rights_state=DocumentRightsState.REFERENCE_ONLY,
            storage_reference="private:documents/forbidden",
        )


def test_governed_tombstone_preserves_manifest_identity_and_prior_truth() -> None:
    media = manifests()[0]
    tombstone = EvidenceTombstone(
        evidence_id=media.evidence_id,
        manifest_digest=manifest_digest(media),
        prior_state=EvidencePublicState.EVIDENCE_PRESERVED,
        reason="Contributor proved the retained image contained private information.",
        removed_by_actor_id=uuid4(),
        removed_at=NOW,
    )

    assert tombstone.prior_state is EvidencePublicState.EVIDENCE_PRESERVED
    with pytest.raises(ValueError, match="cannot tombstone another tombstone"):
        EvidenceTombstone.model_validate(
            {
                **tombstone.model_dump(mode="json"),
                "prior_state": EvidencePublicState.TOMBSTONED,
            }
        )


def _ack(
    manifest: object,
    kind: EvidenceAcknowledgementKind,
    content_digest: str,
) -> EvidenceAcknowledgement:
    parsed = parse_manifest(manifest)
    return EvidenceAcknowledgement(
        evidence_id=parsed.evidence_id,
        evidence_class=EvidenceClass(parsed.evidence_class),
        manifest_digest=manifest_digest(parsed),
        kind=kind,
        destination="urn:fixture:immutable",
        content_digest=content_digest,
        external_reference=f"fixture:{kind.value}",
        verified_at=NOW,
        adapter_identity="fixture",
        adapter_version="1",
    )
