from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from opennosh_api.acceptance.adapters import acceptance_evidence_copy_digest
from opennosh_api.acceptance.fixtures import (
    _FORGE_TARGET,
    ACCEPTANCE_PUBLICATION_ID,
    ACCEPTANCE_RELEASE_VERSION,
    ACCEPTANCE_SOURCE,
    ACCEPTANCE_SOURCE_ID,
    AcceptanceFixtureMetadata,
    _atomic_write,
    _record,
)
from opennosh_api.contributions.models import ContributionDraft
from opennosh_api.database import build_administration_engine
from opennosh_api.evidence.contracts import (
    EvidenceAcknowledgement,
    EvidenceAcknowledgementKind,
    EvidenceClass,
)
from opennosh_api.jobs.pgqueuer import PgQueuerJobQueue
from opennosh_api.models.auth import User
from opennosh_api.nonproduction_keys import (
    ACCEPTANCE_MANIFEST_KEY_ID,
    ACCEPTANCE_MANIFEST_VERIFYING_KEY,
    ACCEPTANCE_RECEIPT_KEY_ID,
    ACCEPTANCE_RECEIPT_VERIFYING_KEY,
)
from opennosh_api.public.artifacts import (
    LocalArtifactStore,
    PublicArtifactReadService,
)
from opennosh_api.public_commons.manifests import ManifestKeyRing, canonical_json
from opennosh_api.publication.models import (
    AcceptedEvent,
    PublicationIntent,
    PublicationReceiptRecord,
    PublicationStep,
)
from opennosh_api.publication.receipts import PublicationReceiptKeyRing, receipt_object_key
from opennosh_api.publication.service import CreatePublicationIntent, create_publication_intent
from opennosh_api.publication.state import PublicationState, PublicationStepName

ACCEPTANCE_CONTRIBUTOR_ID = UUID("22222222-2222-4222-8222-222222222222")
ACCEPTANCE_DRAFT_ID = UUID("33333333-3333-4333-8333-333333333333")
ACCEPTANCE_DECISION_ID = UUID("44444444-4444-4444-8444-444444444444")
ACCEPTANCE_STEWARD_ID = UUID("55555555-5555-4555-8555-555555555555")
ACCEPTANCE_EVIDENCE_ID = UUID("66666666-6666-4666-8666-666666666666")
_EVIDENCE_MANIFEST_DIGEST = "e" * 64


async def run_browser_acceptance_pipeline(
    *,
    database_url: str,
    capacity_manifest_path: Path,
    artifact_directory: Path,
    state_directory: Path,
    published_at: datetime,
    timeout_seconds: float,
) -> AcceptanceFixtureMetadata:
    engine = build_administration_engine(
        database_url,
        manifest_path=capacity_manifest_path,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _seed_and_enqueue(factory, published_at)
        await _wait_for_published(factory, timeout_seconds)
        metadata = await _verify_and_write_metadata(
            factory,
            artifact_directory=artifact_directory,
            state_directory=state_directory,
        )
    finally:
        await engine.dispose()
    return metadata


async def _seed_and_enqueue(
    factory: async_sessionmaker[AsyncSession],
    published_at: datetime,
) -> None:
    async with factory() as session, session.begin():
        await session.execute(
            insert(User)
            .values(
                id=ACCEPTANCE_CONTRIBUTOR_ID,
                email="acceptance-contributor@opennosh.invalid",
                password_hash="acceptance-only-not-a-login",
            )
            .on_conflict_do_nothing(index_elements=[User.id])
        )
        await session.execute(
            insert(ContributionDraft)
            .values(
                id=ACCEPTANCE_DRAFT_ID,
                user_id=ACCEPTANCE_CONTRIBUTOR_ID,
                client_draft_id="browser-acceptance-v1",
                fields_json={"pack_id": "north-india-home-foods"},
            )
            .on_conflict_do_nothing(index_elements=[ContributionDraft.id])
        )
        evidence = EvidenceAcknowledgement(
            evidence_id=ACCEPTANCE_EVIDENCE_ID,
            evidence_class=EvidenceClass.SANITIZED_MEDIA,
            manifest_digest=_EVIDENCE_MANIFEST_DIGEST,
            kind=EvidenceAcknowledgementKind.IMMUTABLE_SANITIZED_COPY,
            destination="urn:opennosh:durability:evidence",
            content_digest=acceptance_evidence_copy_digest(),
            external_reference="acceptance:durable-evidence",
            verified_at=published_at,
            adapter_identity="opennosh.acceptance.evidence",
            adapter_version="1.0",
        )
        await create_publication_intent(
            session,
            PgQueuerJobQueue(clock=lambda: published_at),
            CreatePublicationIntent(
                source_draft_id=ACCEPTANCE_DRAFT_ID,
                source_draft_version=1,
                reviewed_decision_id=ACCEPTANCE_DECISION_ID,
                approving_actor_id=ACCEPTANCE_STEWARD_ID,
                pack_id="north-india-home-foods",
                record_id=ACCEPTANCE_SOURCE_ID,
                approved_payload_digest=hashlib.sha256(canonical_json(_record())).hexdigest(),
                expected_base_commit="a" * 40,
                required_checks=("acceptance-contract",),
                forge_target=_FORGE_TARGET,
                idempotency_key="browser-acceptance-publication-v1",
                evidence_manifest_digests=(_EVIDENCE_MANIFEST_DIGEST,),
                evidence_acknowledgements=(evidence,),
            ),
            now=published_at,
            id_generator=lambda: ACCEPTANCE_PUBLICATION_ID,
        )


async def _wait_for_published(
    factory: async_sessionmaker[AsyncSession], timeout_seconds: float
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    terminal_failures = {
        PublicationState.BLOCKED.value,
        PublicationState.FAILED.value,
        PublicationState.PUBLISH_BLOCKED.value,
        PublicationState.QUARANTINED.value,
    }
    while loop.time() < deadline:
        async with factory() as session:
            intent = await session.get(PublicationIntent, ACCEPTANCE_PUBLICATION_ID)
            if intent is not None and intent.state == PublicationState.PUBLISHED.value:
                return
            if intent is not None and intent.state in terminal_failures:
                raise RuntimeError(
                    "Acceptance publication terminated as "
                    f"{intent.state}: {intent.last_failure_code or 'unknown failure'}"
                )
        await asyncio.sleep(0.1)
    raise TimeoutError("Acceptance publication did not reach published before its deadline")


async def _verify_and_write_metadata(
    factory: async_sessionmaker[AsyncSession],
    *,
    artifact_directory: Path,
    state_directory: Path,
) -> AcceptanceFixtureMetadata:
    async with factory() as session:
        verified_steps = await session.scalar(
            select(func.count())
            .select_from(PublicationStep)
            .where(
                PublicationStep.publication_intent_id == ACCEPTANCE_PUBLICATION_ID,
                PublicationStep.state == "verified",
            )
        )
        receipt = await session.scalar(
            select(PublicationReceiptRecord).where(
                PublicationReceiptRecord.publication_intent_id == ACCEPTANCE_PUBLICATION_ID
            )
        )
        accepted = await session.scalar(
            select(AcceptedEvent).where(
                AcceptedEvent.publication_intent_id == ACCEPTANCE_PUBLICATION_ID
            )
        )
    if verified_steps != len(PublicationStepName):
        raise RuntimeError("Acceptance publication did not verify every protocol step")
    if receipt is None or accepted is None or accepted.receipt_digest != receipt.receipt_digest:
        raise RuntimeError("Acceptance publication lacks its durable receipt and accepted event")

    service = PublicArtifactReadService(
        store=LocalArtifactStore(artifact_directory),
        manifest_keys=ManifestKeyRing.from_config(
            f"{ACCEPTANCE_MANIFEST_KEY_ID}:{ACCEPTANCE_MANIFEST_VERIFYING_KEY}"
        ),
        receipt_keys=PublicationReceiptKeyRing.from_json(
            json.dumps({ACCEPTANCE_RECEIPT_KEY_ID: ACCEPTANCE_RECEIPT_VERIFYING_KEY})
        ),
        checkpoint_path=state_directory / "checkpoint.json",
    )
    try:
        verified = await service.food(ACCEPTANCE_SOURCE, ACCEPTANCE_SOURCE_ID)
    finally:
        await service.aclose()
    metadata = AcceptanceFixtureMetadata(
        release_version=ACCEPTANCE_RELEASE_VERSION,
        source=ACCEPTANCE_SOURCE.value,
        source_id=ACCEPTANCE_SOURCE_ID,
        record_name=verified.record.name,
        immutable_url=verified.immutable_url,
        provenance_url=verified.provenance_url,
        manifest_url=f"/api/v1/public/releases/{ACCEPTANCE_RELEASE_VERSION}/manifest",
        receipt_object_key=receipt_object_key(ACCEPTANCE_PUBLICATION_ID),
        receipt_digest=receipt.receipt_digest,
        published_at=receipt.published_at,
        manifest_key_id=ACCEPTANCE_MANIFEST_KEY_ID,
        manifest_verifying_key=ACCEPTANCE_MANIFEST_VERIFYING_KEY,
        receipt_key_id=ACCEPTANCE_RECEIPT_KEY_ID,
        receipt_verifying_key=ACCEPTANCE_RECEIPT_VERIFYING_KEY,
    )
    _atomic_write(
        state_directory / "fixture.json",
        canonical_json(metadata.model_dump(mode="json")),
    )
    return metadata
