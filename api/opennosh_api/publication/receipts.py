from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Protocol, runtime_checkable
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from opennosh_api.evidence.contracts import EvidenceAcknowledgement
from opennosh_api.publication.state import (
    DurableAcknowledgementSnapshot,
    PublicationSnapshot,
    PublicationStepName,
    PublicationStepState,
    publication_protocol,
)

_DOMAIN = b"opennosh:publication-receipt:1.0\0"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OBJECT_KEY = re.compile(r"^[a-z0-9][a-z0-9/._-]{0,1023}$")
_MAX_RECEIPT_BYTES = 256 * 1024
_PRE_RECEIPT_STEPS = tuple(PublicationStepName)[:7]


class ReceiptEventType(StrEnum):
    PUBLICATION = "publication"
    CORRECTION = "correction"
    REVOCATION = "revocation"


class ReceiptVerificationError(RuntimeError):
    pass


class ImmutableReceiptConflictError(RuntimeError):
    pass


class ReceiptStepProof(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    step: PublicationStepName
    destination: str = Field(min_length=1, max_length=512)
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    external_reference: str | None = Field(default=None, max_length=2048)
    verified_at: datetime
    adapter_identity: str = Field(min_length=1, max_length=255)
    adapter_version: str = Field(min_length=1, max_length=80)

    @field_validator("verified_at")
    @classmethod
    def require_aware_time(cls, value: datetime) -> datetime:
        return _aware(value, "Receipt proof time")


class PublicationReceiptDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    publication_id: UUID
    event_type: ReceiptEventType = ReceiptEventType.PUBLICATION
    prior_receipt_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    pack_id: str = Field(min_length=1, max_length=160)
    record_id: str = Field(min_length=1, max_length=160)
    reviewed_decision_id: UUID
    approving_actor_id: UUID
    approving_actor_scope: str = Field(min_length=1, max_length=512)
    approved_payload_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_base_commit: str = Field(pattern=r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
    merged_commit: str = Field(pattern=r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
    merged_tree_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_manifest_digests: Annotated[tuple[str, ...], Field(min_length=1, max_length=128)]
    evidence_acknowledgements: Annotated[
        tuple[EvidenceAcknowledgement, ...], Field(min_length=1, max_length=128)
    ]
    signed_release_metadata_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    release_version: str = Field(min_length=1, max_length=255)
    registry_acknowledgement_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    registry_result: str = Field(min_length=1, max_length=120)
    artifact_snapshot_digests: Annotated[tuple[str, ...], Field(min_length=1, max_length=16)]
    verified_steps: Annotated[tuple[ReceiptStepProof, ...], Field(min_length=7, max_length=7)]
    published_at: datetime
    idempotency_key_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator(
        "evidence_manifest_digests",
        "artifact_snapshot_digests",
    )
    @classmethod
    def require_ordered_unique_digests(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not _SHA256.fullmatch(item) for item in value):
            raise ValueError("Receipt digest collections must contain SHA-256 values")
        if value != tuple(sorted(set(value))):
            raise ValueError("Receipt digest collections must be sorted and unique")
        return value

    @field_validator("evidence_acknowledgements")
    @classmethod
    def require_ordered_unique_evidence_acknowledgements(
        cls, value: tuple[EvidenceAcknowledgement, ...]
    ) -> tuple[EvidenceAcknowledgement, ...]:
        identities = tuple((item.kind.value, item.destination) for item in value)
        if identities != tuple(sorted(set(identities))):
            raise ValueError("Evidence acknowledgements must be sorted and unique by destination")
        return value

    @field_validator("published_at")
    @classmethod
    def require_aware_publication_time(cls, value: datetime) -> datetime:
        return _aware(value, "Receipt publication time")

    @model_validator(mode="after")
    def validate_lineage_and_steps(self) -> PublicationReceiptDraft:
        if self.event_type is ReceiptEventType.PUBLICATION:
            if self.prior_receipt_digest is not None:
                raise ValueError("Initial publication cannot link a prior receipt")
        elif self.prior_receipt_digest is None:
            raise ValueError("Corrections and revocations require a prior receipt")
        acknowledged_manifests = {
            acknowledgement.manifest_digest for acknowledgement in self.evidence_acknowledgements
        }
        if acknowledged_manifests != set(self.evidence_manifest_digests):
            raise ValueError("Evidence acknowledgements must cover every bound evidence manifest")
        step_names = tuple(proof.step for proof in self.verified_steps)
        if step_names != _PRE_RECEIPT_STEPS:
            raise ValueError("Receipt proofs must match the canonical pre-receipt protocol")
        if self.published_at != max(proof.verified_at for proof in self.verified_steps):
            raise ValueError("Receipt publication time must equal the latest verified proof time")
        proofs = {proof.step: proof for proof in self.verified_steps}
        commit = proofs[PublicationStepName.COMMIT_RECORD]
        if commit.external_reference != self.merged_commit:
            raise ValueError("Receipt merged commit must match the verified commit proof")
        if (
            proofs[PublicationStepName.SIGN_RELEASE].content_digest
            != self.signed_release_metadata_digest
        ):
            raise ValueError("Signed release digest must match the verified release proof")
        if (
            proofs[PublicationStepName.CONFIRM_REGISTRY].content_digest
            != self.registry_acknowledgement_digest
        ):
            raise ValueError("Registry digest must match the verified registry proof")
        artifact_digests = tuple(
            sorted(
                {
                    proofs[PublicationStepName.COPY_COMMIT].content_digest,
                    proofs[PublicationStepName.COPY_EVIDENCE].content_digest,
                    proofs[PublicationStepName.COPY_RELEASE].content_digest,
                }
            )
        )
        if artifact_digests != self.artifact_snapshot_digests:
            raise ValueError("Artifact digests must match the verified copy proofs")
        expected_destinations = {
            definition.name: definition.destination
            for definition in publication_protocol(commit.destination)
        }
        if any(
            proof.destination != expected_destinations[proof.step] for proof in self.verified_steps
        ):
            raise ValueError("Receipt proof destinations must match the canonical protocol")
        return self


class PublicationReceipt(PublicationReceiptDraft):
    publisher_identity: str = Field(min_length=1, max_length=255)
    publisher_adapter_identity: str = Field(min_length=1, max_length=255)
    publisher_adapter_version: str = Field(min_length=1, max_length=80)


class SignedPublicationReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    receipt: PublicationReceipt
    signature_key_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    signature: str = Field(pattern=r"^[A-Za-z0-9_-]{86}$")


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def receipt_signature_material(receipt: PublicationReceipt) -> bytes:
    return _DOMAIN + canonical_json(receipt.model_dump(mode="json"))


def canonical_signed_receipt_bytes(envelope: SignedPublicationReceipt) -> bytes:
    return canonical_json(envelope.model_dump(mode="json"))


def signed_receipt_digest(envelope: SignedPublicationReceipt) -> str:
    return hashlib.sha256(canonical_signed_receipt_bytes(envelope)).hexdigest()


class Ed25519ReceiptSigner:
    algorithm = "Ed25519"
    adapter_version = "1.0"

    def __init__(
        self,
        *,
        key_id: str,
        publisher_identity: str,
        private_key: Ed25519PrivateKey,
        adapter_identity: str = "opennosh.ed25519-publication-signer",
    ) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", key_id):
            raise ValueError("Receipt signing key ID is invalid")
        if not publisher_identity or len(publisher_identity) > 255:
            raise ValueError("Receipt publisher identity is invalid")
        self.key_id = key_id
        self.publisher_identity = publisher_identity
        self.adapter_identity = adapter_identity
        self._private_key = private_key

    def sign(self, draft: PublicationReceiptDraft) -> SignedPublicationReceipt:
        receipt = PublicationReceipt(
            **draft.model_dump(mode="python"),
            publisher_identity=self.publisher_identity,
            publisher_adapter_identity=self.adapter_identity,
            publisher_adapter_version=self.adapter_version,
        )
        signature = _encode(self._private_key.sign(receipt_signature_material(receipt)))
        return SignedPublicationReceipt(
            receipt=receipt,
            signature_key_id=self.key_id,
            signature=signature,
        )


class PublicationReceiptKeyRing:
    def __init__(self, keys: Mapping[str, Ed25519PublicKey]) -> None:
        if not keys:
            raise ValueError("Receipt verification key ring cannot be empty")
        self._keys = dict(keys)

    @classmethod
    def from_json(cls, value: str) -> PublicationReceiptKeyRing:
        payload = json.loads(value)
        if not isinstance(payload, dict) or not payload:
            raise ValueError("Receipt key ring must be a non-empty object")
        keys: dict[str, Ed25519PublicKey] = {}
        for key_id, encoded in payload.items():
            if not isinstance(key_id, str) or not isinstance(encoded, str):
                raise ValueError("Receipt key ring entries must be strings")
            keys[key_id] = Ed25519PublicKey.from_public_bytes(_decode(encoded, expected=32))
        return cls(keys)

    def verify(self, envelope: SignedPublicationReceipt) -> None:
        key = self._keys.get(envelope.signature_key_id)
        if key is None:
            raise ReceiptVerificationError("receipt_signature_key_untrusted")
        try:
            key.verify(
                _decode(envelope.signature, expected=64),
                receipt_signature_material(envelope.receipt),
            )
        except InvalidSignature as error:
            raise ReceiptVerificationError("receipt_signature_invalid") from error


@dataclass(frozen=True, slots=True)
class StoredReceiptObservation:
    destination: str
    object_key: str
    receipt_digest: str
    external_reference: str
    size_bytes: int


@runtime_checkable
class PublicationReceiptStore(Protocol):
    @property
    def identity(self) -> str: ...

    @property
    def version(self) -> str: ...

    @property
    def destination(self) -> str: ...

    async def put_immutable(
        self,
        object_key: str,
        payload: bytes,
        *,
        expected_digest: str,
    ) -> None: ...

    async def observe(self, object_key: str) -> StoredReceiptObservation | None: ...

    async def list_keys(self) -> tuple[str, ...]: ...

    async def read(self, object_key: str) -> bytes | None: ...


class MemoryPublicationReceiptStore:
    identity = "opennosh.memory-publication-receipts"
    version = "1.0"

    def __init__(self, *, destination: str) -> None:
        self.destination = destination
        self.objects: dict[str, bytes] = {}

    async def put_immutable(
        self,
        object_key: str,
        payload: bytes,
        *,
        expected_digest: str,
    ) -> None:
        _validate_object_key(object_key)
        _require_digest(payload, expected_digest)
        existing = self.objects.get(object_key)
        if existing is not None and existing != payload:
            raise ImmutableReceiptConflictError(
                "Immutable receipt key already contains different bytes"
            )
        self.objects[object_key] = payload

    async def observe(self, object_key: str) -> StoredReceiptObservation | None:
        payload = self.objects.get(object_key)
        if payload is None:
            return None
        return _stored_observation(self.destination, object_key, payload, f"memory:{object_key}")

    async def list_keys(self) -> tuple[str, ...]:
        return tuple(sorted(self.objects))

    async def read(self, object_key: str) -> bytes | None:
        return self.objects.get(object_key)


class LocalImmutablePublicationReceiptStore:
    identity = "opennosh.local-immutable-publication-receipts"
    version = "1.0"

    def __init__(self, root: Path, *, destination: str) -> None:
        self._root = root.resolve(strict=False)
        self.destination = destination

    async def put_immutable(
        self,
        object_key: str,
        payload: bytes,
        *,
        expected_digest: str,
    ) -> None:
        await asyncio.to_thread(self._put_sync, object_key, payload, expected_digest)

    async def observe(self, object_key: str) -> StoredReceiptObservation | None:
        payload = await asyncio.to_thread(self._read_sync, object_key)
        if payload is None:
            return None
        return _stored_observation(self.destination, object_key, payload, f"file:{object_key}")

    async def list_keys(self) -> tuple[str, ...]:
        return await asyncio.to_thread(self._list_keys_sync)

    async def read(self, object_key: str) -> bytes | None:
        return await asyncio.to_thread(self._read_sync, object_key)

    def _list_keys_sync(self) -> tuple[str, ...]:
        if not self._root.is_dir():
            return ()
        return tuple(
            sorted(path.relative_to(self._root).as_posix() for path in self._root.rglob("*.json"))
        )

    def _put_sync(self, object_key: str, payload: bytes, expected_digest: str) -> None:
        _validate_object_key(object_key)
        _require_digest(payload, expected_digest)
        target = self._target(object_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=target.parent, prefix=f".{target.name}.", suffix=".pending"
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                existing = self._read_sync(object_key)
                if existing != payload:
                    raise ImmutableReceiptConflictError(
                        "Immutable receipt key already contains different bytes"
                    ) from None
            else:
                directory = os.open(target.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)

    def _read_sync(self, object_key: str) -> bytes | None:
        target = self._target(object_key)
        return target.read_bytes() if target.is_file() else None

    def _target(self, object_key: str) -> Path:
        _validate_object_key(object_key)
        target = (self._root / object_key).resolve(strict=False)
        if not target.is_relative_to(self._root):
            raise ValueError("Receipt object key escapes the configured root")
        return target


def receipt_object_key(publication_id: UUID) -> str:
    return f"receipts/v1/{publication_id}.json"


def receipt_draft_from_snapshot(
    snapshot: PublicationSnapshot,
) -> PublicationReceiptDraft:
    proofs = tuple(_proof(snapshot, step) for step in _PRE_RECEIPT_STEPS)
    published_at = max(proof.verified_at for proof in proofs)
    commit = _ack(snapshot, PublicationStepName.COMMIT_RECORD)
    signed_release = _ack(snapshot, PublicationStepName.SIGN_RELEASE)
    registry = _ack(snapshot, PublicationStepName.CONFIRM_REGISTRY)
    merged_tree_digest = _required_context_digest(commit.context, "merged_tree_digest")
    release_version = _required_context_text(signed_release.context, "release_version", 255)
    registry_result = _required_context_text(registry.context, "registry_result", 120)
    if commit.external_reference is None:
        raise ValueError("Receipt requires the merged commit reference")
    return PublicationReceiptDraft(
        publication_id=snapshot.publication_id,
        event_type=ReceiptEventType(snapshot.event_type),
        prior_receipt_digest=snapshot.prior_receipt_digest,
        pack_id=snapshot.pack_id,
        record_id=snapshot.record_id,
        reviewed_decision_id=snapshot.reviewed_decision_id,
        approving_actor_id=snapshot.approving_actor_id,
        approving_actor_scope=f"pack:{snapshot.pack_id}:steward",
        approved_payload_digest=snapshot.approved_payload_digest,
        expected_base_commit=snapshot.expected_base_commit,
        merged_commit=commit.external_reference,
        merged_tree_digest=merged_tree_digest,
        evidence_manifest_digests=snapshot.evidence_manifest_digests,
        evidence_acknowledgements=tuple(
            EvidenceAcknowledgement.model_validate(value)
            for value in snapshot.evidence_acknowledgements
        ),
        signed_release_metadata_digest=signed_release.content_digest,
        release_version=release_version,
        registry_acknowledgement_digest=registry.content_digest,
        registry_result=registry_result,
        artifact_snapshot_digests=tuple(
            sorted(
                {
                    _ack(snapshot, PublicationStepName.COPY_COMMIT).content_digest,
                    _ack(snapshot, PublicationStepName.COPY_EVIDENCE).content_digest,
                    _ack(snapshot, PublicationStepName.COPY_RELEASE).content_digest,
                }
            )
        ),
        verified_steps=proofs,
        published_at=published_at,
        idempotency_key_hash=snapshot.idempotency_key_hash,
    )


def validate_receipt_binding(
    envelope: SignedPublicationReceipt,
    snapshot: PublicationSnapshot,
) -> None:
    receipt = envelope.receipt
    expected = receipt_draft_from_snapshot(snapshot)
    actual = PublicationReceiptDraft.model_validate(
        receipt.model_dump(
            mode="python",
            exclude={
                "publisher_identity",
                "publisher_adapter_identity",
                "publisher_adapter_version",
            },
        )
    )
    if actual != expected:
        raise ReceiptVerificationError("receipt_does_not_match_publication")


def parse_signed_receipt(payload: bytes) -> SignedPublicationReceipt:
    if len(payload) > _MAX_RECEIPT_BYTES:
        raise ReceiptVerificationError("receipt_payload_too_large")
    try:
        parsed = json.loads(payload)
        envelope = SignedPublicationReceipt.model_validate(parsed)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
        raise ReceiptVerificationError("receipt_envelope_invalid") from error
    if canonical_signed_receipt_bytes(envelope) != payload:
        raise ReceiptVerificationError("receipt_envelope_not_canonical")
    return envelope


def _proof(snapshot: PublicationSnapshot, step: PublicationStepName) -> ReceiptStepProof:
    acknowledgement = _ack(snapshot, step)
    return ReceiptStepProof(
        step=step,
        destination=acknowledgement.destination,
        content_digest=acknowledgement.content_digest,
        external_reference=acknowledgement.external_reference,
        verified_at=acknowledgement.verified_at,
        adapter_identity=_required_context_text(acknowledgement.context, "adapter_identity", 255),
        adapter_version=_required_context_text(acknowledgement.context, "adapter_version", 80),
    )


def _ack(
    snapshot: PublicationSnapshot,
    step: PublicationStepName,
) -> DurableAcknowledgementSnapshot:
    matches = [item for item in snapshot.acknowledgements if item.step is step]
    if len(matches) != 1:
        raise ValueError(f"Receipt requires one verified {step.value} acknowledgement")
    state = next(item.state for item in snapshot.steps if item.name is step)
    if state is not PublicationStepState.VERIFIED:
        raise ValueError(f"Receipt cannot bind unverified step {step.value}")
    return matches[0]


def _required_context_text(context: Mapping[str, object], key: str, maximum: int) -> str:
    value = context.get(key)
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"Receipt proof requires bounded {key}")
    return value


def _required_context_digest(context: Mapping[str, object], key: str) -> str:
    value = _required_context_text(context, key, 64)
    if not _SHA256.fullmatch(value):
        raise ValueError(f"Receipt proof requires SHA-256 {key}")
    return value


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    return value


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str, *, expected: int) -> bytes:
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, TypeError) as error:
        raise ReceiptVerificationError("receipt_signature_encoding_invalid") from error
    if len(decoded) != expected:
        raise ReceiptVerificationError("receipt_signature_length_invalid")
    return decoded


def _validate_object_key(object_key: str) -> None:
    if not _OBJECT_KEY.fullmatch(object_key) or ".." in object_key.split("/"):
        raise ValueError("Receipt object key is invalid")


def _require_digest(payload: bytes, expected_digest: str) -> None:
    if hashlib.sha256(payload).hexdigest() != expected_digest:
        raise ValueError("Receipt payload digest does not match the expected digest")


def _stored_observation(
    destination: str,
    object_key: str,
    payload: bytes,
    external_reference: str,
) -> StoredReceiptObservation:
    return StoredReceiptObservation(
        destination=destination,
        object_key=object_key,
        receipt_digest=hashlib.sha256(payload).hexdigest(),
        external_reference=external_reference,
        size_bytes=len(payload),
    )
