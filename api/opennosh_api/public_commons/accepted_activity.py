from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

import yaml
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from opennosh_api.foods.schemas import FoodSource
from opennosh_api.governance.contracts import ApprovedChangeSet
from opennosh_api.governance.models import GovernanceDecision
from opennosh_api.public.artifacts import PublicFoodRecordResponse, ResolvedRelease
from opennosh_api.public_commons.manifests import canonical_json
from opennosh_api.public_commons.schemas import (
    AcceptedActivityEvent,
    AcceptedEventType,
    MostRecentVerifiedRecord,
)
from opennosh_api.publication.models import (
    AcceptedEvent,
    PublicationIntent,
    PublicationReceiptRecord,
)
from opennosh_api.publication.receipts import (
    PublicationReceiptKeyRing,
    ReceiptEventType,
    SignedPublicationReceipt,
    signed_receipt_digest,
)
from opennosh_api.publication.state import PublicationStepName

MAX_ACTIVITY_EVENTS = 10_000


class ActivityProjectionUnavailable(RuntimeError):
    """The accepted-event ledger cannot prove one complete public projection."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class PublicFoodResolver(Protocol):
    async def food(
        self,
        source: FoodSource,
        source_id: str,
        *,
        release_version: str | None = None,
        now: datetime | None = None,
    ) -> PublicFoodRecordResponse: ...


@dataclass(frozen=True, slots=True)
class CanonicalAcceptedActivityProjection:
    accepted_count: int
    events: tuple[AcceptedActivityEvent, ...]
    most_recent_verified_record: MostRecentVerifiedRecord | None
    event_checkpoint: str


class CanonicalAcceptedActivitySource:
    """Build a bounded public view from append-only accepted events and signed receipts."""

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        *,
        receipt_keys: PublicationReceiptKeyRing,
        artifact_reader: PublicFoodResolver,
    ) -> None:
        self._factory = factory
        self._receipt_keys = receipt_keys
        self._artifact_reader = artifact_reader

    async def project(
        self,
        *,
        release: ResolvedRelease,
        checked_at: datetime,
    ) -> CanonicalAcceptedActivityProjection:
        accepted_through = release.publication_receipt_published_at
        if accepted_through is None:
            raise ActivityProjectionUnavailable("release_receipt_cutoff_unavailable")
        cutoff = min(checked_at, accepted_through)
        window_start = checked_at - timedelta(hours=24)

        async with self._factory() as session, session.begin():
            await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
            current_event_id = await session.scalar(
                select(AcceptedEvent.id).where(
                    AcceptedEvent.receipt_digest == release.publication_receipt_digest
                )
            )
            newer_event_id = await session.scalar(
                select(AcceptedEvent.id)
                .where(AcceptedEvent.published_at > cutoff)
                .order_by(AcceptedEvent.published_at, AcceptedEvent.id)
                .limit(1)
            )
            identity_rows = (
                await session.execute(
                    select(
                        AcceptedEvent.id,
                        AcceptedEvent.receipt_digest,
                        AcceptedEvent.published_at,
                        AcceptedEvent.event_type,
                        AcceptedEvent.record_id,
                        AcceptedEvent.pack_id,
                        AcceptedEvent.commit_sha,
                        AcceptedEvent.repository,
                    )
                    .where(
                        AcceptedEvent.published_at >= window_start,
                        AcceptedEvent.published_at <= cutoff,
                    )
                    .order_by(AcceptedEvent.published_at.desc(), AcceptedEvent.id.desc())
                    .limit(MAX_ACTIVITY_EVENTS + 1)
                )
            ).all()
            recent_event_id = await session.scalar(
                select(AcceptedEvent.id)
                .where(
                    AcceptedEvent.published_at <= cutoff,
                    AcceptedEvent.event_type != "record.revoked",
                )
                .order_by(AcceptedEvent.published_at.desc(), AcceptedEvent.id.desc())
                .limit(1)
            )

            if current_event_id is None or newer_event_id is not None:
                raise ActivityProjectionUnavailable("accepted_event_release_boundary_incomplete")
            if len(identity_rows) > MAX_ACTIVITY_EVENTS:
                raise ActivityProjectionUnavailable("accepted_event_window_too_large")

            event_ids = [row[0] for row in identity_rows[:4]]
            detail_ids = set(event_ids)
            if recent_event_id is not None:
                detail_ids.add(recent_event_id)
            detail_rows = (
                await session.execute(
                    select(AcceptedEvent, PublicationReceiptRecord, GovernanceDecision)
                    .join(
                        PublicationReceiptRecord,
                        PublicationReceiptRecord.receipt_digest == AcceptedEvent.receipt_digest,
                    )
                    .join(
                        PublicationIntent,
                        PublicationIntent.id == AcceptedEvent.publication_intent_id,
                    )
                    .join(
                        GovernanceDecision,
                        GovernanceDecision.id == PublicationIntent.reviewed_decision_id,
                    )
                    .where(AcceptedEvent.id.in_(detail_ids))
                )
            ).all()

        details = {
            accepted.id: (accepted, receipt, decision)
            for accepted, receipt, decision in detail_rows
        }
        if set(details) != detail_ids:
            raise ActivityProjectionUnavailable("accepted_event_governance_proof_missing")

        public_events: list[AcceptedActivityEvent] = []
        for event_id in event_ids:
            accepted, receipt, decision = details[event_id]
            public_events.append(
                await self._public_event(
                    accepted,
                    receipt,
                    decision,
                    release=release,
                )
            )

        recent: MostRecentVerifiedRecord | None = None
        if recent_event_id is not None:
            accepted, receipt, decision = details[recent_event_id]
            recent_event = next(
                (
                    event
                    for event in public_events
                    if event.event_id == str(recent_event_id)
                ),
                None,
            )
            if recent_event is None:
                recent_event = await self._public_event(
                    accepted,
                    receipt,
                    decision,
                    release=release,
                )
            recent = MostRecentVerifiedRecord(
                record_id=recent_event.food_or_pack_id,
                name=(await self._record(accepted, release=release)).record.name,
                food_locale=recent_event.food_locale,
                verified_at=recent_event.accepted_at,
                href=recent_event.href or "",
            )

        checkpoint = hashlib.sha256(
            canonical_json(
                [
                    {
                        "event_id": str(event_id),
                        "receipt_digest": str(receipt_digest),
                        "published_at": published_at.isoformat(),
                        "event_type": str(event_type),
                        "record_id": str(record_id),
                        "pack_id": str(pack_id),
                        "commit_sha": str(commit_sha),
                        "repository": str(repository),
                    }
                    for (
                        event_id,
                        receipt_digest,
                        published_at,
                        event_type,
                        record_id,
                        pack_id,
                        commit_sha,
                        repository,
                    ) in identity_rows
                ]
            )
        ).hexdigest()
        return CanonicalAcceptedActivityProjection(
            accepted_count=len(identity_rows),
            events=tuple(public_events),
            most_recent_verified_record=recent,
            event_checkpoint=checkpoint,
        )

    async def _public_event(
        self,
        accepted: AcceptedEvent,
        receipt_row: PublicationReceiptRecord,
        decision: GovernanceDecision,
        *,
        release: ResolvedRelease,
    ) -> AcceptedActivityEvent:
        envelope = SignedPublicationReceipt.model_validate(receipt_row.envelope_json)
        self._receipt_keys.verify(envelope)
        receipt = envelope.receipt
        expected_type = {
            ReceiptEventType.PUBLICATION: "record.published",
            ReceiptEventType.CORRECTION: "record.corrected",
            ReceiptEventType.REVOCATION: "record.revoked",
        }[receipt.event_type]
        commit_proof = next(
            (
                proof
                for proof in receipt.verified_steps
                if proof.step is PublicationStepName.COMMIT_RECORD
            ),
            None,
        )
        if (
            signed_receipt_digest(envelope) != accepted.receipt_digest
            or receipt_row.receipt_digest != accepted.receipt_digest
            or receipt.pack_id != accepted.pack_id
            or receipt.record_id != accepted.record_id
            or receipt.merged_commit != accepted.commit_sha
            or receipt.published_at != accepted.published_at
            or expected_type != accepted.event_type
            or commit_proof is None
            or commit_proof.destination != accepted.repository
            or decision.approved_payload_digest != receipt.approved_payload_digest
            or decision.approved_changes_json is None
            or _version(receipt.release_version) > _version(release.manifest.release_version)
        ):
            raise ActivityProjectionUnavailable("accepted_event_receipt_binding_invalid")

        changes = ApprovedChangeSet.from_json(decision.approved_changes_json)
        if changes.digest != receipt.approved_payload_digest or changes.pack_id != accepted.pack_id:
            raise ActivityProjectionUnavailable("accepted_event_governance_binding_invalid")
        food_locale = _pack_locale(changes)
        record = await self._record(accepted, release=release)
        if record.record.attribution.pack_id != accepted.pack_id:
            raise ActivityProjectionUnavailable("accepted_event_artifact_binding_invalid")

        summary = {
            ReceiptEventType.PUBLICATION: (
                f"Accepted {record.record.name} as a verified food record."
            ),
            ReceiptEventType.CORRECTION: f"Accepted a verified update to {record.record.name}.",
            ReceiptEventType.REVOCATION: (
                f"Accepted the verified withdrawal of {record.record.name}."
            ),
        }[receipt.event_type]
        href = (
            f"/en/explore/foods/community/{accepted.record_id}"
            f"?version={release.manifest.release_version}"
        )
        return AcceptedActivityEvent(
            event_id=str(accepted.id),
            event_type=AcceptedEventType.FOOD,
            food_or_pack_id=accepted.record_id,
            food_locale=food_locale,
            accepted_at=accepted.published_at,
            source_commit=accepted.commit_sha,
            href=href,
            summary=summary,
            public_contributor_credit=record.record.attribution.contributed_by,
        )

    async def _record(
        self,
        accepted: AcceptedEvent,
        *,
        release: ResolvedRelease,
    ) -> PublicFoodRecordResponse:
        try:
            return await self._artifact_reader.food(
                FoodSource.COMMUNITY,
                accepted.record_id,
                release_version=release.manifest.release_version,
            )
        except Exception as error:
            raise ActivityProjectionUnavailable("accepted_event_artifact_unavailable") from error


def _pack_locale(changes: ApprovedChangeSet) -> str:
    expected = f"packs/{changes.pack_id}/pack.yaml"
    source = next((item.content for item in changes.files if item.path == expected), None)
    if source is None:
        raise ActivityProjectionUnavailable("accepted_event_pack_manifest_missing")
    try:
        payload = yaml.safe_load(source)
    except yaml.YAMLError as error:
        raise ActivityProjectionUnavailable("accepted_event_pack_manifest_invalid") from error
    if not isinstance(payload, dict) or payload.get("id") != changes.pack_id:
        raise ActivityProjectionUnavailable("accepted_event_pack_manifest_invalid")
    locale = payload.get("locale")
    if not isinstance(locale, str) or not 1 <= len(locale) <= 80:
        raise ActivityProjectionUnavailable("accepted_event_pack_locale_invalid")
    return locale


def _version(value: str) -> tuple[int, int, int, int]:
    try:
        parts = tuple(int(part) for part in value.split("."))
    except ValueError as error:
        raise ActivityProjectionUnavailable("accepted_event_release_version_invalid") from error
    if len(parts) != 4:
        raise ActivityProjectionUnavailable("accepted_event_release_version_invalid")
    return parts[0], parts[1], parts[2], parts[3]
