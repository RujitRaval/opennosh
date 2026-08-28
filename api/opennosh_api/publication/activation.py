"""Receipt-gated, pointer-last activation for one verified Commons release."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from opennosh_api.public.artifacts import (
    MAX_MANIFEST_BYTES,
    MAX_POINTER_BYTES,
    ArtifactDescriptor,
    PublicReadLatestPointer,
    PublicReadReleaseManifest,
)
from opennosh_api.public.r2 import (
    R2ImmutableConflictError,
    R2PublicationError,
)
from opennosh_api.public.refresh import (
    LATEST_POINTER_CACHE_CONTROL,
    LATEST_POINTER_MEDIA_TYPE,
    LATEST_POINTER_OBJECT_KEY,
)
from opennosh_api.public.signing import sign_envelope
from opennosh_api.public_commons.manifests import (
    ManifestKeyRing,
    ManifestVerificationError,
    SignedEnvelope,
    canonical_json,
)
from opennosh_api.publication.adapters import (
    PublicationEffectAdapter,
    PublicationEffectError,
)
from opennosh_api.publication.receipts import (
    PublicationReceiptKeyRing,
    ReceiptVerificationError,
    SignedPublicationReceipt,
    parse_signed_receipt,
    signed_receipt_digest,
)
from opennosh_api.publication.state import (
    EffectIntent,
    ExternalObservation,
    ObservationStatus,
    PublicationStepName,
)

_MAX_RECEIPT_BYTES = 256 * 1024


class PointerActivationWriter(Protocol):
    async def put_bytes(
        self,
        *,
        bucket: str,
        object_key: str,
        payload: bytes,
        media_type: str,
        cache_control: str,
        if_match: str | None = None,
        if_none_match: str | None = None,
    ) -> None: ...

    async def read_optional_bytes(
        self,
        *,
        bucket: str,
        object_key: str,
        max_bytes: int,
    ) -> bytes | None: ...

    async def read_revision(
        self,
        *,
        bucket: str,
        object_key: str,
        max_bytes: int,
    ) -> tuple[bytes, str]: ...


@dataclass(frozen=True, slots=True)
class _VerifiedActivationMaterial:
    receipt: SignedPublicationReceipt
    receipt_digest: str
    manifest: PublicReadReleaseManifest
    manifest_bytes: bytes
    manifest_descriptor: ArtifactDescriptor


class ReceiptGatedPointerActivationAdapter:
    """Copy the receipt durably, then advance latest with an ETag CAS."""

    identity = "opennosh.receipt-gated-pointer-activation"
    version = "1.0"

    def __init__(
        self,
        *,
        receipt_copy: PublicationEffectAdapter,
        writer: PointerActivationWriter,
        bucket: str,
        manifest_keys: ManifestKeyRing,
        receipt_keys: PublicationReceiptKeyRing,
        signing_key_id: str,
        signing_key: Ed25519PrivateKey,
        pointer_lifetime_seconds: int,
        clock: Callable[[], datetime],
    ) -> None:
        if not isinstance(receipt_copy, PublicationEffectAdapter):
            raise ValueError("Pointer activation requires a receipt-copy adapter")
        if not bucket:
            raise ValueError("Pointer activation requires an artifact bucket")
        if not 0 < pointer_lifetime_seconds <= 86_400:
            raise ValueError("Pointer activation lifetime must be at most 24 hours")
        self._receipt_copy = receipt_copy
        self._writer = writer
        self._bucket = bucket
        self._manifest_keys = manifest_keys
        self._receipt_keys = receipt_keys
        self._signing_key_id = signing_key_id
        self._signing_key = signing_key
        self._pointer_lifetime = timedelta(seconds=pointer_lifetime_seconds)
        self._clock = clock

    async def apply(self, intent: EffectIntent) -> None:
        self._require_intent(intent)
        await self._receipt_copy.apply(intent)
        durable_receipt = await self._receipt_copy.observe(intent)
        if durable_receipt.status is not ObservationStatus.VERIFIED:
            raise _effect_error(durable_receipt, "durable_receipt_not_verified")
        material = await self._verified_material(intent)
        await self._activate(material)

    async def observe(self, intent: EffectIntent) -> ExternalObservation:
        self._require_intent(intent)
        durable_receipt = await self._receipt_copy.observe(intent)
        if durable_receipt.status is not ObservationStatus.VERIFIED:
            return self._receipt_observation(durable_receipt)
        try:
            material = await self._verified_material(intent)
            pointer_bytes = await self._writer.read_optional_bytes(
                bucket=self._bucket,
                object_key=LATEST_POINTER_OBJECT_KEY,
                max_bytes=MAX_POINTER_BYTES,
            )
        except PublicationEffectError as error:
            return self._observation(
                intent,
                status=error.status,
                code=error.code,
                retry_at=error.retry_at,
                context=error.context,
            )
        except R2PublicationError:
            return self._observation(
                intent,
                status=ObservationStatus.RETRYABLE_FAILURE,
                code="pointer_activation_r2_unavailable",
            )
        if pointer_bytes is None:
            return self._observation(intent, status=ObservationStatus.ABSENT)
        try:
            pointer = self._verified_pointer(pointer_bytes)
            relationship = _pointer_relationship(pointer, material)
        except (ManifestVerificationError, ValueError):
            return self._observation(
                intent,
                status=ObservationStatus.CONFLICT,
                code="latest_pointer_untrusted",
            )
        if relationship == "before":
            return self._observation(intent, status=ObservationStatus.ABSENT)
        if relationship == "after":
            if not _valid_pointer_window(pointer, material.manifest):
                return self._observation(
                    intent,
                    status=ObservationStatus.CONFLICT,
                    code="latest_pointer_lifetime_invalid",
                )
            return self._verified_observation(
                intent,
                durable_receipt=durable_receipt,
                material=material,
                pointer_bytes=pointer_bytes,
                pointer=pointer,
                code="latest_pointer_superseded",
            )
        if relationship != "same":
            return self._observation(
                intent,
                status=ObservationStatus.CONFLICT,
                code="latest_pointer_identity_conflict",
            )
        if not _valid_pointer_window(pointer, material.manifest):
            return self._observation(
                intent,
                status=ObservationStatus.CONFLICT,
                code="latest_pointer_lifetime_invalid",
            )
        return self._verified_observation(
            intent,
            durable_receipt=durable_receipt,
            material=material,
            pointer_bytes=pointer_bytes,
            pointer=pointer,
        )

    def _verified_observation(
        self,
        intent: EffectIntent,
        *,
        durable_receipt: ExternalObservation,
        material: _VerifiedActivationMaterial,
        pointer_bytes: bytes,
        pointer: PublicReadLatestPointer,
        code: str | None = None,
    ) -> ExternalObservation:
        return ExternalObservation(
            step=intent.step,
            status=ObservationStatus.VERIFIED,
            observed_at=self._now(),
            destination=intent.destination,
            effect_idempotency_key=intent.idempotency_key,
            adapter_identity=self.identity,
            adapter_version=self.version,
            content_digest=durable_receipt.content_digest,
            external_reference=durable_receipt.external_reference,
            code=code,
            context={
                **dict(durable_receipt.context),
                "pointer_digest": hashlib.sha256(pointer_bytes).hexdigest(),
                "pointer_object_key": LATEST_POINTER_OBJECT_KEY,
                "pointer_release_version": pointer.release_version,
                "pointer_manifest_digest": pointer.manifest.digest,
                "pointer_issued_at": (pointer.issued_at or material.manifest.published_at)
                .astimezone(UTC)
                .isoformat(),
                "pointer_expires_at": pointer.expires_at.astimezone(UTC).isoformat(),
            },
        )

    async def _activate(self, material: _VerifiedActivationMaterial) -> None:
        try:
            current_bytes, current_etag = await self._writer.read_revision(
                bucket=self._bucket,
                object_key=LATEST_POINTER_OBJECT_KEY,
                max_bytes=MAX_POINTER_BYTES,
            )
            current = self._verified_pointer(current_bytes)
        except R2PublicationError as error:
            raise PublicationEffectError(
                status=ObservationStatus.RETRYABLE_FAILURE,
                code="latest_pointer_read_failed",
            ) from error
        relationship = _pointer_relationship(current, material)
        if relationship == "same":
            if not _valid_pointer_window(current, material.manifest):
                raise PublicationEffectError(
                    status=ObservationStatus.CONFLICT,
                    code="latest_pointer_lifetime_invalid",
                )
            return
        if relationship == "after":
            if not _valid_pointer_window(current, material.manifest):
                raise PublicationEffectError(
                    status=ObservationStatus.CONFLICT,
                    code="latest_pointer_lifetime_invalid",
                )
            return
        if relationship != "before":
            raise PublicationEffectError(
                status=ObservationStatus.CONFLICT,
                code="latest_pointer_identity_conflict",
            )

        now = self._now()
        if now < material.manifest.published_at:
            raise PublicationEffectError(
                status=ObservationStatus.RETRYABLE_FAILURE,
                code="pointer_activation_clock_behind_release",
            )
        candidate = PublicReadLatestPointer(
            release_version=material.manifest.release_version,
            manifest=material.manifest_descriptor,
            issued_at=now,
            expires_at=now + self._pointer_lifetime,
        )
        candidate_bytes = sign_envelope(
            candidate.model_dump(mode="json"),
            key_id=self._signing_key_id,
            private_key=self._signing_key,
        )
        self._verified_pointer(candidate_bytes)
        try:
            await self._writer.put_bytes(
                bucket=self._bucket,
                object_key=LATEST_POINTER_OBJECT_KEY,
                payload=candidate_bytes,
                media_type=LATEST_POINTER_MEDIA_TYPE,
                cache_control=LATEST_POINTER_CACHE_CONTROL,
                if_match=current_etag,
            )
        except R2ImmutableConflictError:
            await self._accept_same_release_after_conflict(current, material)
            return
        except R2PublicationError as error:
            raise PublicationEffectError(
                status=ObservationStatus.RETRYABLE_FAILURE,
                code="latest_pointer_write_failed",
            ) from error
        try:
            readback = await self._writer.read_optional_bytes(
                bucket=self._bucket,
                object_key=LATEST_POINTER_OBJECT_KEY,
                max_bytes=MAX_POINTER_BYTES,
            )
        except R2PublicationError as error:
            raise PublicationEffectError(
                status=ObservationStatus.RETRYABLE_FAILURE,
                code="latest_pointer_readback_failed",
            ) from error
        if readback != candidate_bytes:
            await self._accept_same_release_after_conflict(current, material)

    async def _accept_same_release_after_conflict(
        self,
        previous: PublicReadLatestPointer,
        material: _VerifiedActivationMaterial,
    ) -> None:
        try:
            payload, _ = await self._writer.read_revision(
                bucket=self._bucket,
                object_key=LATEST_POINTER_OBJECT_KEY,
                max_bytes=MAX_POINTER_BYTES,
            )
            current = self._verified_pointer(payload)
        except (R2PublicationError, ManifestVerificationError, ValueError) as error:
            raise PublicationEffectError(
                status=ObservationStatus.RETRYABLE_FAILURE,
                code="latest_pointer_cas_conflict",
            ) from error
        previous_issued = previous.issued_at or material.manifest.published_at
        current_issued = current.issued_at or material.manifest.published_at
        if (
            _pointer_relationship(current, material) == "same"
            and _valid_pointer_window(current, material.manifest)
            and current_issued >= previous_issued
            and current.expires_at >= previous.expires_at
        ):
            return
        if (
            _pointer_relationship(current, material) == "after"
            and _valid_pointer_window(current, material.manifest)
        ):
            return
        raise PublicationEffectError(
            status=ObservationStatus.RETRYABLE_FAILURE,
            code="latest_pointer_cas_conflict",
        )

    async def _verified_material(
        self,
        intent: EffectIntent,
    ) -> _VerifiedActivationMaterial:
        receipt = _receipt_from_intent(intent)
        self._receipt_keys.verify(receipt)
        receipt_digest = signed_receipt_digest(receipt)
        manifest_key = f"releases/v1/release-{receipt.receipt.release_version}.json"
        try:
            manifest_bytes = await self._writer.read_optional_bytes(
                bucket=self._bucket,
                object_key=manifest_key,
                max_bytes=MAX_MANIFEST_BYTES,
            )
            registry_receipt = await self._writer.read_optional_bytes(
                bucket=self._bucket,
                object_key=f"receipts/v1/{intent.publication_id}.json",
                max_bytes=_MAX_RECEIPT_BYTES,
            )
        except R2PublicationError as error:
            raise PublicationEffectError(
                status=ObservationStatus.RETRYABLE_FAILURE,
                code="activation_material_read_failed",
            ) from error
        if manifest_bytes is None or registry_receipt is None:
            raise PublicationEffectError(
                status=ObservationStatus.RETRYABLE_FAILURE,
                code="activation_material_absent",
            )
        try:
            envelope = SignedEnvelope.model_validate_json(manifest_bytes)
            self._manifest_keys.verify(envelope)
            if canonical_json(envelope.model_dump(mode="json")) != manifest_bytes:
                raise ValueError("release manifest is not canonical")
            manifest = PublicReadReleaseManifest.model_validate(envelope.payload)
            stored_receipt = parse_signed_receipt(registry_receipt)
            self._receipt_keys.verify(stored_receipt)
        except (
            ManifestVerificationError,
            ReceiptVerificationError,
            ValueError,
        ) as error:
            raise PublicationEffectError(
                status=ObservationStatus.CONFLICT,
                code="activation_material_untrusted",
            ) from error
        manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
        copy_release_digest = next(
            (
                proof.content_digest
                for proof in receipt.receipt.verified_steps
                if proof.step is PublicationStepName.COPY_RELEASE
            ),
            None,
        )
        if (
            stored_receipt != receipt
            or hashlib.sha256(registry_receipt).hexdigest() != receipt_digest
            or manifest.release_version != receipt.receipt.release_version
            or manifest.published_at != receipt.receipt.published_at
            or manifest.publication_receipt_key
            != f"receipts/v1/{intent.publication_id}.json"
            or manifest_digest != receipt.receipt.signed_release_metadata_digest
            or copy_release_digest != manifest_digest
        ):
            raise PublicationEffectError(
                status=ObservationStatus.CONFLICT,
                code="activation_material_binding_conflict",
            )
        return _VerifiedActivationMaterial(
            receipt=receipt,
            receipt_digest=receipt_digest,
            manifest=manifest,
            manifest_bytes=manifest_bytes,
            manifest_descriptor=ArtifactDescriptor(
                object_key=manifest_key,
                digest=manifest_digest,
                size_bytes=len(manifest_bytes),
                media_type="application/vnd.opennosh.release+json",
            ),
        )

    def _verified_pointer(self, payload: bytes) -> PublicReadLatestPointer:
        value = json.loads(payload)
        envelope = SignedEnvelope.model_validate(value)
        self._manifest_keys.verify(envelope)
        if canonical_json(envelope.model_dump(mode="json")) != payload:
            raise ValueError("latest pointer is not canonical")
        return PublicReadLatestPointer.model_validate(envelope.payload)

    def _receipt_observation(
        self,
        source: ExternalObservation,
    ) -> ExternalObservation:
        return ExternalObservation(
            step=source.step,
            status=source.status,
            observed_at=source.observed_at,
            destination=source.destination,
            effect_idempotency_key=source.effect_idempotency_key,
            adapter_identity=self.identity,
            adapter_version=self.version,
            content_digest=source.content_digest,
            external_reference=source.external_reference,
            retry_at=source.retry_at,
            code=source.code,
            context=source.context,
        )

    def _observation(
        self,
        intent: EffectIntent,
        *,
        status: ObservationStatus,
        code: str | None = None,
        retry_at: datetime | None = None,
        context: dict[str, object] | None = None,
    ) -> ExternalObservation:
        return ExternalObservation(
            step=intent.step,
            status=status,
            observed_at=self._now(),
            destination=intent.destination,
            effect_idempotency_key=intent.idempotency_key,
            adapter_identity=self.identity,
            adapter_version=self.version,
            retry_at=retry_at,
            code=code,
            context=context or {},
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Pointer activation clock must include a timezone")
        return value.astimezone(UTC)

    @staticmethod
    def _require_intent(intent: EffectIntent) -> None:
        if intent.step is not PublicationStepName.COPY_RECEIPT:
            raise ValueError("Pointer activation requires the copy-receipt step")


def _receipt_from_intent(intent: EffectIntent) -> SignedPublicationReceipt:
    value = intent.context.get("signed_receipt")
    if not isinstance(value, dict):
        raise ValueError("Pointer activation intent lacks its signed receipt")
    return SignedPublicationReceipt.model_validate(value)


def _pointer_relationship(
    pointer: PublicReadLatestPointer,
    material: _VerifiedActivationMaterial,
) -> str:
    current = tuple(int(part) for part in pointer.release_version.split("."))
    target = tuple(int(part) for part in material.manifest.release_version.split("."))
    if current < target:
        return "before"
    if current > target:
        return "after"
    if pointer.manifest == material.manifest_descriptor:
        return "same"
    return "conflict"


def _valid_pointer_window(
    pointer: PublicReadLatestPointer,
    manifest: PublicReadReleaseManifest,
) -> bool:
    issued_at = pointer.issued_at or manifest.published_at
    return (
        manifest.published_at
        <= issued_at
        < pointer.expires_at
        <= issued_at + timedelta(hours=24)
    )


def _effect_error(
    observation: ExternalObservation,
    fallback_code: str,
) -> PublicationEffectError:
    status = observation.status
    if status is ObservationStatus.ABSENT:
        status = ObservationStatus.RETRYABLE_FAILURE
    if status is ObservationStatus.VERIFIED:
        raise ValueError("Verified observations are not effect errors")
    return PublicationEffectError(
        status=status,
        code=observation.code or fallback_code,
        retry_at=observation.retry_at,
        context=observation.context,
    )
