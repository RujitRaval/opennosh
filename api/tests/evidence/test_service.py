from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import uuid4

from opennosh_api.evidence.contracts import DocumentRightsState, PublicDocumentManifest
from opennosh_api.evidence.service import preservation_request
from opennosh_api.jobs import JobLane


def test_preservation_request_contains_only_typed_identity_and_digest() -> None:
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    manifest = PublicDocumentManifest(
        evidence_id=uuid4(),
        canonical_uri="https://example.test/reference",
        publisher="Publisher",
        license="CC-BY-4.0",
        title="Reference",
        observed_at=now,
        observed_digest=hashlib.sha256(b"observed source").hexdigest(),
        rights_state=DocumentRightsState.REFERENCE_ONLY,
    )

    request = preservation_request(manifest, run_after=now)

    assert request.message.lane is JobLane.EVIDENCE
    assert request.message.job_type == "evidence.preserve"
    assert request.message.subject_id == manifest.evidence_id
    assert request.message.idempotency_key == request.deduplication_key
    encoded = request.message.model_dump_json()
    assert "observed source" not in encoded
    assert "canonical_uri" not in encoded
