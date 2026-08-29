from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.evidence.contracts import (
    EvidenceAcknowledgement,
    EvidenceAcknowledgementKind,
    EvidenceClass,
    EvidenceManifest,
    EvidencePublicState,
    EvidenceTombstone,
    manifest_digest,
    parse_manifest,
)
from opennosh_api.evidence.models import (
    EvidenceDurableAcknowledgement,
    EvidenceManifestRecord,
    EvidenceRemovalTombstone,
)
from opennosh_api.evidence.policy import EvidenceDurabilityError, verify_durability


class EvidenceConflictError(RuntimeError):
    pass


class EvidenceNotFoundError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    manifest: EvidenceManifest
    acknowledgements: tuple[EvidenceAcknowledgement, ...]
    public_state: EvidencePublicState | None
    tombstone: EvidenceTombstone | None


async def create_manifest(
    session: AsyncSession,
    *,
    source_draft_id: UUID,
    source_draft_version: int,
    manifest: EvidenceManifest,
) -> EvidenceManifestRecord:
    if source_draft_version < 1:
        raise ValueError("Evidence source draft version must be positive")
    digest = manifest_digest(manifest)
    existing: EvidenceManifestRecord | None = await session.scalar(
        select(EvidenceManifestRecord).where(
            EvidenceManifestRecord.source_draft_id == source_draft_id,
            EvidenceManifestRecord.source_draft_version == source_draft_version,
        )
    )
    if existing is not None:
        _assert_same_manifest(existing, manifest, digest)
        return existing
    record = EvidenceManifestRecord(
        id=manifest.evidence_id,
        source_draft_id=source_draft_id,
        source_draft_version=source_draft_version,
        schema_version=manifest.schema_version,
        evidence_class=manifest.evidence_class.value,
        manifest_digest=digest,
        manifest_json=manifest.model_dump(mode="json"),
    )
    try:
        async with session.begin_nested():
            session.add(record)
            await session.flush()
        return record
    except IntegrityError:
        existing = await session.scalar(
            select(EvidenceManifestRecord).where(
                EvidenceManifestRecord.source_draft_id == source_draft_id,
                EvidenceManifestRecord.source_draft_version == source_draft_version,
            )
        )
        if existing is None:
            raise EvidenceConflictError("Evidence insert conflict was not visible") from None
        _assert_same_manifest(existing, manifest, digest)
        return existing


async def record_acknowledgements(
    session: AsyncSession,
    evidence_id: UUID,
    acknowledgements: tuple[EvidenceAcknowledgement, ...],
) -> EvidenceBundle:
    record = await session.scalar(
        select(EvidenceManifestRecord)
        .where(EvidenceManifestRecord.id == evidence_id)
        .with_for_update()
    )
    if record is None:
        raise EvidenceNotFoundError("Evidence manifest was not found")
    if record.public_state == EvidencePublicState.TOMBSTONED.value:
        raise EvidenceConflictError("Tombstoned evidence cannot receive acknowledgements")
    for acknowledgement in acknowledgements:
        await _insert_or_compare_acknowledgement(session, record, acknowledgement)
    bundle = await load_bundle(session, evidence_id)
    try:
        public_state = verify_durability(bundle.manifest, bundle.acknowledgements)
    except EvidenceDurabilityError as error:
        if error.code != "durable_acknowledgement_missing":
            raise
        public_state = None
    record.public_state = public_state.value if public_state is not None else None
    await session.flush()
    return EvidenceBundle(
        manifest=bundle.manifest,
        acknowledgements=bundle.acknowledgements,
        public_state=public_state,
        tombstone=None,
    )


async def load_bundle(session: AsyncSession, evidence_id: UUID) -> EvidenceBundle:
    record = await session.scalar(
        select(EvidenceManifestRecord).where(EvidenceManifestRecord.id == evidence_id)
    )
    if record is None:
        raise EvidenceNotFoundError("Evidence manifest was not found")
    acknowledgement_rows = (
        await session.scalars(
            select(EvidenceDurableAcknowledgement)
            .where(EvidenceDurableAcknowledgement.evidence_id == evidence_id)
            .order_by(
                EvidenceDurableAcknowledgement.acknowledgement_kind,
                EvidenceDurableAcknowledgement.destination,
            )
        )
    ).all()
    tombstone_row = await session.scalar(
        select(EvidenceRemovalTombstone).where(
            EvidenceRemovalTombstone.evidence_id == evidence_id
        )
    )
    return EvidenceBundle(
        manifest=parse_manifest(record.manifest_json),
        acknowledgements=tuple(_acknowledgement(row) for row in acknowledgement_rows),
        public_state=(
            EvidencePublicState(record.public_state) if record.public_state is not None else None
        ),
        tombstone=_tombstone(tombstone_row) if tombstone_row is not None else None,
    )


async def require_verified_evidence(
    session: AsyncSession,
    *,
    source_draft_id: UUID,
    source_draft_version: int,
) -> EvidenceBundle:
    record = await session.scalar(
        select(EvidenceManifestRecord)
        .where(
            EvidenceManifestRecord.source_draft_id == source_draft_id,
            EvidenceManifestRecord.source_draft_version == source_draft_version,
        )
        .with_for_update(read=True)
    )
    if record is None:
        raise EvidenceDurabilityError("evidence_manifest_missing")
    bundle = await load_bundle(session, record.id)
    if bundle.tombstone is not None:
        raise EvidenceDurabilityError("evidence_tombstoned")
    verified = verify_durability(bundle.manifest, bundle.acknowledgements)
    if bundle.public_state is not verified:
        raise EvidenceDurabilityError("evidence_public_state_stale")
    return bundle


async def tombstone_evidence(
    session: AsyncSession,
    *,
    evidence_id: UUID,
    removed_by_actor_id: UUID,
    reason: str,
    now: datetime,
) -> EvidenceTombstone:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Evidence removal time must include a timezone")
    record = await session.scalar(
        select(EvidenceManifestRecord)
        .where(EvidenceManifestRecord.id == evidence_id)
        .with_for_update()
    )
    if record is None:
        raise EvidenceNotFoundError("Evidence manifest was not found")
    existing = await session.scalar(
        select(EvidenceRemovalTombstone).where(
            EvidenceRemovalTombstone.evidence_id == evidence_id
        )
    )
    if existing is not None:
        tombstone = _tombstone(existing)
        if tombstone.removed_by_actor_id != removed_by_actor_id or tombstone.reason != reason:
            raise EvidenceConflictError("Evidence already has a different governed tombstone")
        return tombstone
    if record.public_state is None or record.public_state == EvidencePublicState.TOMBSTONED.value:
        raise EvidenceConflictError("Only verified accessible evidence can be tombstoned")
    authorized_steward = await session.scalar(
        text(
            """
            SELECT role.actor_id
            FROM contribution_drafts draft
            JOIN governance_role_assignments role
              ON role.pack_id = draft.fields_json ->> 'pack_id'
             AND role.actor_id = :actor_id
             AND role.role = 'steward'
             AND role.granted_at <= :removed_at
             AND (role.revoked_at IS NULL OR role.revoked_at > :removed_at)
            WHERE draft.id = :source_draft_id
            LIMIT 1
            """
        ),
        {
            "actor_id": removed_by_actor_id,
            "removed_at": now,
            "source_draft_id": record.source_draft_id,
        },
    )
    if authorized_steward is None:
        raise EvidenceConflictError("Evidence removal requires an active pack steward")
    pending_authorization = await session.scalar(
        text(
            """
            SELECT gma.publication_intent_id
            FROM governance_merge_authorizations gma
            JOIN publication_intents pi ON pi.id = gma.publication_intent_id
            WHERE pi.source_draft_id = :source_draft_id
              AND pi.source_draft_version = :source_draft_version
              AND pi.state != 'published'
            LIMIT 1
            """
        ),
        {
            "source_draft_id": record.source_draft_id,
            "source_draft_version": record.source_draft_version,
        },
    )
    if pending_authorization is not None:
        raise EvidenceConflictError(
            "Evidence cannot be removed while merge authorization is active"
        )
    tombstone = EvidenceTombstone(
        evidence_id=evidence_id,
        manifest_digest=record.manifest_digest,
        prior_state=EvidencePublicState(record.public_state),
        reason=reason,
        removed_by_actor_id=removed_by_actor_id,
        removed_at=now,
    )
    session.add(
        EvidenceRemovalTombstone(
            evidence_id=tombstone.evidence_id,
            schema_version=tombstone.schema_version,
            manifest_digest=tombstone.manifest_digest,
            prior_state=tombstone.prior_state.value,
            reason=tombstone.reason,
            removed_by_actor_id=tombstone.removed_by_actor_id,
            removed_at=tombstone.removed_at,
            updated_at=now,
        )
    )
    record.public_state = EvidencePublicState.TOMBSTONED.value
    await session.flush()
    return tombstone


async def _insert_or_compare_acknowledgement(
    session: AsyncSession,
    record: EvidenceManifestRecord,
    acknowledgement: EvidenceAcknowledgement,
) -> None:
    if acknowledgement.evidence_id != record.id:
        raise EvidenceConflictError("Acknowledgement belongs to different evidence")
    row = EvidenceDurableAcknowledgement(
        evidence_id=acknowledgement.evidence_id,
        schema_version=acknowledgement.schema_version,
        evidence_class=acknowledgement.evidence_class.value,
        manifest_digest=acknowledgement.manifest_digest,
        acknowledgement_kind=acknowledgement.kind.value,
        destination=acknowledgement.destination,
        content_digest=acknowledgement.content_digest,
        external_reference=acknowledgement.external_reference,
        verified_at=acknowledgement.verified_at,
        adapter_identity=acknowledgement.adapter_identity,
        adapter_version=acknowledgement.adapter_version,
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
        return
    except IntegrityError:
        existing = await session.scalar(
            select(EvidenceDurableAcknowledgement).where(
                EvidenceDurableAcknowledgement.evidence_id == acknowledgement.evidence_id,
                EvidenceDurableAcknowledgement.acknowledgement_kind
                == acknowledgement.kind.value,
                EvidenceDurableAcknowledgement.destination == acknowledgement.destination,
            )
        )
        if existing is None or not _same_durable_proof(
            _acknowledgement(existing), acknowledgement
        ):
            raise EvidenceConflictError(
                "Durable evidence acknowledgement already exists with different proof"
            ) from None


def _same_durable_proof(
    left: EvidenceAcknowledgement, right: EvidenceAcknowledgement
) -> bool:
    """A replay may observe the same object later; time is not proof identity."""

    return left.model_copy(update={"verified_at": right.verified_at}) == right


def _acknowledgement(row: EvidenceDurableAcknowledgement) -> EvidenceAcknowledgement:
    return EvidenceAcknowledgement(
        schema_version="1.0",
        evidence_id=row.evidence_id,
        evidence_class=EvidenceClass(row.evidence_class),
        manifest_digest=row.manifest_digest,
        kind=EvidenceAcknowledgementKind(row.acknowledgement_kind),
        destination=row.destination,
        content_digest=row.content_digest,
        external_reference=row.external_reference,
        verified_at=row.verified_at,
        adapter_identity=row.adapter_identity,
        adapter_version=row.adapter_version,
    )


def _assert_same_manifest(
    existing: EvidenceManifestRecord,
    manifest: EvidenceManifest,
    digest: str,
) -> None:
    if (
        existing.id != manifest.evidence_id
        or existing.manifest_digest != digest
        or existing.manifest_json != manifest.model_dump(mode="json")
    ):
        raise EvidenceConflictError("Draft version already has different immutable evidence")


def _tombstone(row: EvidenceRemovalTombstone) -> EvidenceTombstone:
    return EvidenceTombstone(
        schema_version="1.0",
        evidence_id=row.evidence_id,
        manifest_digest=row.manifest_digest,
        prior_state=EvidencePublicState(row.prior_state),
        reason=row.reason,
        removed_by_actor_id=row.removed_by_actor_id,
        removed_at=row.removed_at,
    )
