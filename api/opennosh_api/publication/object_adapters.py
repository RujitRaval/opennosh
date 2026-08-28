from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from opennosh_api.public.artifacts import PublicReadReleaseManifest
from opennosh_api.public.r2 import (
    R2ImmutableConflictError,
    R2PublicationError,
    S3R2ObjectWriter,
)
from opennosh_api.public.signing import public_key_text, sign_envelope
from opennosh_api.public_commons.manifests import ManifestKeyRing, SignedEnvelope
from opennosh_api.publication.adapters import PublicationEffectError
from opennosh_api.publication.receipts import (
    ImmutableReceiptConflictError,
    StoredReceiptObservation,
)
from opennosh_api.publication.state import (
    EffectIntent,
    ExternalObservation,
    ObservationStatus,
    PublicationStepName,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OBJECT_KEY = re.compile(r"^[a-z0-9][a-z0-9/._-]{0,1023}$")
_MAX_OBJECT_BYTES = 64 * 1024 * 1024
_MAX_RECEIPT_BYTES = 256 * 1024


@dataclass(frozen=True, slots=True)
class PublicationObject:
    """Canonical bytes and identity returned by a read-only material source."""

    object_key: str
    payload: bytes
    media_type: str
    context: dict[str, object]
    external_reference: str | None = None

    def __post_init__(self) -> None:
        if not _OBJECT_KEY.fullmatch(self.object_key) or ".." in self.object_key.split("/"):
            raise ValueError("Publication object key is invalid")
        if not self.payload or len(self.payload) > _MAX_OBJECT_BYTES:
            raise ValueError("Publication object payload is empty or too large")
        if not self.media_type or len(self.media_type) > 127:
            raise ValueError("Publication object media type is invalid")

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.payload).hexdigest()


@runtime_checkable
class PublicationObjectSource(Protocol):
    """Read-only provider that deterministically materializes one publication object."""

    @property
    def identity(self) -> str: ...

    @property
    def version(self) -> str: ...

    async def materialize(self, intent: EffectIntent) -> PublicationObject: ...


@runtime_checkable
class ReleaseManifestDraftSource(Protocol):
    """Read-only source for one canonical, unsigned public release manifest."""

    @property
    def identity(self) -> str: ...

    @property
    def version(self) -> str: ...

    async def materialize_manifest(
        self, intent: EffectIntent
    ) -> PublicReadReleaseManifest: ...


class Ed25519ReleaseManifestSource:
    """Sign and self-verify a canonical release manifest with the online role."""

    version = "1.0"

    def __init__(
        self,
        *,
        source: ReleaseManifestDraftSource,
        key_id: str,
        signing_key: Ed25519PrivateKey,
    ) -> None:
        if not isinstance(source, ReleaseManifestDraftSource):
            raise ValueError("Release signer requires a canonical manifest source")
        self._source = source
        self._key_id = key_id
        self._signing_key = signing_key
        self._key_ring = ManifestKeyRing.from_config(
            f"{key_id}:{public_key_text(signing_key)}"
        )
        self.identity = f"opennosh.ed25519-release-signer.{source.identity}"

    async def materialize(self, intent: EffectIntent) -> PublicationObject:
        manifest = await self._source.materialize_manifest(intent)
        payload = sign_envelope(
            manifest.model_dump(mode="json"),
            key_id=self._key_id,
            private_key=self._signing_key,
        )
        envelope = SignedEnvelope.model_validate_json(payload)
        self._key_ring.verify(envelope)
        return PublicationObject(
            object_key=(
                "signatures/releases/v1/"
                f"release-{manifest.release_version}.json"
            ),
            payload=payload,
            media_type="application/vnd.opennosh.release+json",
            context={
                "release_version": manifest.release_version,
                "signature_key_id": self._key_id,
            },
        )


class R2ImmutablePublicationAdapter:
    """Observe-first, conditional-create adapter for one immutable R2 effect."""

    version = "1.0"

    def __init__(
        self,
        *,
        step: PublicationStepName,
        destination: str,
        source: PublicationObjectSource,
        writer: S3R2ObjectWriter,
        bucket: str,
        clock: Callable[[], datetime],
    ) -> None:
        if step in {
            PublicationStepName.COMMIT_RECORD,
            PublicationStepName.SIGN_RECEIPT,
            PublicationStepName.PUBLISH_RECEIPT_REGISTRY,
            PublicationStepName.COPY_RECEIPT,
        }:
            raise ValueError("R2 object adapter does not support this protocol step")
        if not destination:
            raise ValueError("R2 object adapter destination cannot be empty")
        if not isinstance(source, PublicationObjectSource):
            raise ValueError("R2 object adapter requires a canonical material source")
        self._step = step
        self._destination = destination
        self._source = source
        self._writer = writer
        self._bucket = bucket
        self._clock = clock
        self.identity = f"opennosh.r2.{step.value}.{source.identity}"

    async def apply(self, intent: EffectIntent) -> None:
        self._require_intent(intent)
        material = await self._source.materialize(intent)
        existing = await self._read(material.object_key, max_bytes=_MAX_OBJECT_BYTES)
        if existing is not None:
            if existing != material.payload:
                raise PublicationEffectError(
                    status=ObservationStatus.CONFLICT,
                    code="immutable_r2_object_conflict",
                    context={"object_key": material.object_key},
                )
            return
        try:
            await self._writer.put_bytes(
                bucket=self._bucket,
                object_key=material.object_key,
                payload=material.payload,
                media_type=material.media_type,
                cache_control="public, max-age=31536000, immutable",
                if_none_match="*",
            )
        except R2ImmutableConflictError as error:
            existing = await self._read(
                material.object_key,
                max_bytes=_MAX_OBJECT_BYTES,
            )
            if existing != material.payload:
                raise PublicationEffectError(
                    status=ObservationStatus.CONFLICT,
                    code="immutable_r2_object_conflict",
                    context={"object_key": material.object_key},
                ) from error

    async def observe(self, intent: EffectIntent) -> ExternalObservation:
        self._require_intent(intent)
        observed_at = self._now()
        try:
            material = await self._source.materialize(intent)
            payload = await self._read(material.object_key, max_bytes=_MAX_OBJECT_BYTES)
        except R2PublicationError:
            return self._observation(
                intent,
                status=ObservationStatus.RETRYABLE_FAILURE,
                observed_at=observed_at,
                code="r2_object_unavailable",
            )
        if payload is None:
            return self._observation(
                intent,
                status=ObservationStatus.ABSENT,
                observed_at=observed_at,
            )
        if payload != material.payload:
            return self._observation(
                intent,
                status=ObservationStatus.CONFLICT,
                observed_at=observed_at,
                code="immutable_r2_object_conflict",
                context={"object_key": material.object_key},
            )
        return self._observation(
            intent,
            status=ObservationStatus.VERIFIED,
            observed_at=observed_at,
            content_digest=material.digest,
            external_reference=(
                material.external_reference
                or f"r2://{self._bucket}/{material.object_key}"
            ),
            context={"object_key": material.object_key, **material.context},
        )

    async def _read(self, object_key: str, *, max_bytes: int) -> bytes | None:
        return await self._writer.read_optional_bytes(
            bucket=self._bucket,
            object_key=object_key,
            max_bytes=max_bytes,
        )

    def _require_intent(self, intent: EffectIntent) -> None:
        if intent.step is not self._step:
            raise ValueError("R2 object adapter received the wrong publication step")
        if intent.destination != self._destination:
            raise ValueError("R2 object adapter destination does not match the intent")

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Publication adapter clock must include a timezone")
        return value

    def _observation(
        self,
        intent: EffectIntent,
        *,
        status: ObservationStatus,
        observed_at: datetime,
        content_digest: str | None = None,
        external_reference: str | None = None,
        code: str | None = None,
        context: dict[str, object] | None = None,
    ) -> ExternalObservation:
        if content_digest is not None and not _SHA256.fullmatch(content_digest):
            raise ValueError("Publication object digest is invalid")
        return ExternalObservation(
            step=intent.step,
            status=status,
            observed_at=observed_at,
            destination=intent.destination,
            effect_idempotency_key=intent.idempotency_key,
            adapter_identity=self.identity,
            adapter_version=self.version,
            content_digest=content_digest,
            external_reference=external_reference,
            code=code,
            context=context or {},
        )


class R2PublicationReceiptStore:
    """Immutable receipt storage with conditional create and independent read-back."""

    identity = "opennosh.r2-publication-receipts"
    version = "1.0"

    def __init__(
        self,
        *,
        writer: S3R2ObjectWriter,
        bucket: str,
        destination: str,
        list_prefix: str,
    ) -> None:
        if not destination:
            raise ValueError("R2 receipt destination cannot be empty")
        if not _OBJECT_KEY.fullmatch(list_prefix):
            raise ValueError("R2 receipt list prefix is invalid")
        self._writer = writer
        self._bucket = bucket
        self.destination = destination
        self._list_prefix = list_prefix.rstrip("/") + "/"

    async def put_immutable(
        self,
        object_key: str,
        payload: bytes,
        *,
        expected_digest: str,
    ) -> None:
        self._validate(object_key, payload, expected_digest)
        existing = await self.read(object_key)
        if existing is not None:
            if existing != payload:
                raise ImmutableReceiptConflictError(
                    "Immutable receipt key already contains different bytes"
                )
            return
        try:
            await self._writer.put_bytes(
                bucket=self._bucket,
                object_key=object_key,
                payload=payload,
                media_type="application/vnd.opennosh.publication-receipt+json",
                cache_control="public, max-age=31536000, immutable",
                if_none_match="*",
            )
        except R2ImmutableConflictError:
            existing = await self.read(object_key)
            if existing != payload:
                raise ImmutableReceiptConflictError(
                    "Immutable receipt key already contains different bytes"
                ) from None
        readback = await self.read(object_key)
        if readback != payload:
            raise R2PublicationError("R2 receipt read-back did not match the write")

    async def observe(self, object_key: str) -> StoredReceiptObservation | None:
        payload = await self.read(object_key)
        if payload is None:
            return None
        return StoredReceiptObservation(
            destination=self.destination,
            object_key=object_key,
            receipt_digest=hashlib.sha256(payload).hexdigest(),
            external_reference=f"r2://{self._bucket}/{object_key}",
            size_bytes=len(payload),
        )

    async def list_keys(self) -> tuple[str, ...]:
        return await self._writer.list_keys(
            bucket=self._bucket,
            prefix=self._list_prefix,
        )

    async def read(self, object_key: str) -> bytes | None:
        self._validate_key(object_key)
        return await self._writer.read_optional_bytes(
            bucket=self._bucket,
            object_key=object_key,
            max_bytes=_MAX_RECEIPT_BYTES,
        )

    @staticmethod
    def _validate_key(object_key: str) -> None:
        if not _OBJECT_KEY.fullmatch(object_key) or ".." in object_key.split("/"):
            raise ValueError("Receipt object key is invalid")

    @classmethod
    def _validate(cls, object_key: str, payload: bytes, expected_digest: str) -> None:
        cls._validate_key(object_key)
        if not payload or len(payload) > _MAX_RECEIPT_BYTES:
            raise ValueError("Receipt payload is empty or too large")
        if not _SHA256.fullmatch(expected_digest):
            raise ValueError("Receipt digest is invalid")
        if hashlib.sha256(payload).hexdigest() != expected_digest:
            raise ValueError("Receipt payload digest does not match the expected digest")
