from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from opennosh_api.evidence.contracts import (
    DocumentRightsState,
    PublicDocumentManifest,
)
from opennosh_api.first_contribution.r2 import (
    FirstContributionEvidenceConflictError,
    R2FirstContributionEvidenceStore,
)
from opennosh_api.public.r2 import R2PublicationError

NOW = datetime(2026, 8, 28, 17, tzinfo=UTC)


class MemoryWriter:
    def __init__(
        self,
        *,
        lose_first_response: bool = False,
        race_payload: bytes | None = None,
    ) -> None:
        self.objects: dict[str, bytes] = {}
        self.put_calls = 0
        self.lose_first_response = lose_first_response
        self.race_payload = race_payload

    async def read_optional_bytes(
        self, *, bucket: str, object_key: str, max_bytes: int
    ) -> bytes | None:
        assert bucket == "evidence-bucket"
        value = self.objects.get(object_key)
        assert value is None or len(value) <= max_bytes
        return value

    async def put_bytes(self, **options: Any) -> None:
        self.put_calls += 1
        key = str(options["object_key"])
        payload = options["payload"]
        assert isinstance(payload, bytes)
        if self.race_payload is not None:
            self.objects[key] = self.race_payload
            raise R2PublicationError("simulated conditional-write race")
        self.objects[key] = payload
        if self.lose_first_response:
            self.lose_first_response = False
            raise R2PublicationError("simulated lost response")


def manifest() -> PublicDocumentManifest:
    return PublicDocumentManifest(
        evidence_id=uuid4(),
        canonical_uri=(
            "https://fdc.nal.usda.gov/fdc-app.html#/food-details/1105314/nutrients"
        ),
        publisher="USDA FoodData Central",
        license="CC0-1.0",
        title="Pinned banana record",
        observed_at=NOW,
        observed_digest="a" * 64,
        rights_state=DocumentRightsState.REFERENCE_ONLY,
    )


@pytest.mark.asyncio
async def test_r2_citation_is_observe_first_idempotent_and_recovers_lost_response() -> None:
    writer = MemoryWriter(lose_first_response=True)
    store = R2FirstContributionEvidenceStore(  # type: ignore[arg-type]
        writer=writer, bucket="evidence-bucket"
    )
    evidence = manifest()

    first = await store.preserve(evidence, now=NOW)
    second = await store.preserve(evidence, now=NOW)

    assert first == second
    assert first.kind.value == "citation_manifest"
    assert first.external_reference.startswith("r2://evidence-bucket/evidence/citations/v1/")
    assert writer.put_calls == 1
    assert len(writer.objects) == 1


@pytest.mark.asyncio
async def test_r2_citation_rejects_existing_different_bytes() -> None:
    writer = MemoryWriter()
    store = R2FirstContributionEvidenceStore(  # type: ignore[arg-type]
        writer=writer, bucket="evidence-bucket"
    )
    evidence = manifest()
    digest = __import__("opennosh_api.evidence.contracts", fromlist=["manifest_digest"])
    key = f"evidence/citations/v1/{digest.manifest_digest(evidence)}.json"
    writer.objects[key] = b"different"

    with pytest.raises(FirstContributionEvidenceConflictError, match="different bytes"):
        await store.preserve(evidence, now=NOW)


@pytest.mark.asyncio
async def test_r2_citation_maps_concurrent_different_bytes_to_terminal_conflict() -> None:
    writer = MemoryWriter(race_payload=b"competing immutable bytes")
    store = R2FirstContributionEvidenceStore(  # type: ignore[arg-type]
        writer=writer, bucket="evidence-bucket"
    )

    with pytest.raises(FirstContributionEvidenceConflictError, match="won a race"):
        await store.preserve(manifest(), now=NOW)
    assert writer.put_calls == 1
