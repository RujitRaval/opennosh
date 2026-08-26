from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from opennosh_api.acceptance.fixtures import (
    ACCEPTANCE_RELEASE_VERSION,
    ACCEPTANCE_SOURCE,
    ACCEPTANCE_SOURCE_ID,
    _record,
    _signed_envelope,
)
from opennosh_api.nonproduction_keys import (
    ACCEPTANCE_MANIFEST_KEY_ID,
    ACCEPTANCE_MANIFEST_VERIFYING_KEY,
    ACCEPTANCE_RECEIPT_KEY_ID,
    ACCEPTANCE_RECEIPT_VERIFYING_KEY,
)
from opennosh_api.public.artifacts import (
    LocalArtifactStore,
    PublicArtifactReadService,
    PublicFoodArtifact,
    PublicReadLatestPointer,
    PublicReadReleaseManifest,
    activate_verified_release,
    artifact_descriptor,
)
from opennosh_api.public_commons.manifests import ManifestKeyRing, canonical_json
from opennosh_api.publication.adapters import (
    PublicationAdapterRegistry,
    PublicationEffectAdapter,
)
from opennosh_api.publication.receipt_adapters import (
    ReceiptReplicationAdapter,
    ReceiptSigningAdapter,
)
from opennosh_api.publication.receipts import (
    Ed25519ReceiptSigner,
    LocalImmutablePublicationReceiptStore,
    PublicationReceiptKeyRing,
    SignedPublicationReceipt,
    canonical_signed_receipt_bytes,
    receipt_object_key,
)
from opennosh_api.publication.state import (
    EffectIntent,
    ExternalObservation,
    ObservationStatus,
    PublicationStepName,
)

_EVIDENCE_COPY_DIGEST = hashlib.sha256(b"opennosh-acceptance-evidence-copy-v1").hexdigest()
_COPY_COMMIT_DIGEST = hashlib.sha256(b"opennosh-acceptance-commit-copy-v1").hexdigest()
_MERGED_COMMIT = "b" * 40
_MERGED_TREE_DIGEST = "c" * 64


class AcceptanceStepAdapter:
    """Persistent, contract-faithful external adapter for browser acceptance only."""

    identity = "opennosh.acceptance.external"
    version = "1.0"

    def __init__(
        self,
        step: PublicationStepName,
        *,
        state_root: Path,
        clock: Callable[[], datetime],
    ) -> None:
        self._step = step
        self._state_root = state_root
        self._clock = clock

    async def apply(self, intent: EffectIntent) -> None:
        self._require_intent(intent)
        digest, external_reference, context = self._effect(intent)
        payload = canonical_json(
            {
                "content_digest": digest,
                "external_reference": external_reference,
                "context": context,
            }
        )
        _write_immutable(self._effect_path(intent), payload)

    async def observe(self, intent: EffectIntent) -> ExternalObservation:
        self._require_intent(intent)
        try:
            raw = self._effect_path(intent).read_bytes()
        except FileNotFoundError:
            return self._observation(intent, ObservationStatus.ABSENT)
        try:
            value = json.loads(raw)
            digest = value["content_digest"]
            external_reference = value["external_reference"]
            context = value["context"]
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise ValueError("Acceptance adapter state is invalid") from error
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError("Acceptance adapter digest is invalid")
        if external_reference is not None and not isinstance(external_reference, str):
            raise ValueError("Acceptance adapter reference is invalid")
        if not isinstance(context, dict):
            raise ValueError("Acceptance adapter context is invalid")
        return self._observation(
            intent,
            ObservationStatus.VERIFIED,
            content_digest=digest,
            external_reference=external_reference,
            context=context,
        )

    def _effect(self, intent: EffectIntent) -> tuple[str, str | None, dict[str, object]]:
        if self._step is PublicationStepName.COMMIT_RECORD:
            return (
                intent.approved_payload_digest,
                _MERGED_COMMIT,
                {"merged_tree_digest": _MERGED_TREE_DIGEST},
            )
        if self._step is PublicationStepName.COPY_COMMIT:
            return _COPY_COMMIT_DIGEST, "acceptance:durable-commit", {}
        if self._step is PublicationStepName.COPY_EVIDENCE:
            return _EVIDENCE_COPY_DIGEST, "acceptance:durable-evidence", {}
        if self._step is PublicationStepName.SIGN_RELEASE:
            manifest = _manifest_bytes(intent.publication_id, self._clock())
            _write_immutable(_manifest_path(self._state_root, intent), manifest)
            return (
                hashlib.sha256(manifest).hexdigest(),
                f"acceptance:signed-release:{intent.publication_id}",
                {"release_version": ACCEPTANCE_RELEASE_VERSION},
            )
        if self._step in {
            PublicationStepName.PUBLISH_RELEASE,
            PublicationStepName.COPY_RELEASE,
        }:
            manifest = _manifest_path(self._state_root, intent).read_bytes()
            return (
                hashlib.sha256(manifest).hexdigest(),
                f"acceptance:{self._step.value}:{intent.publication_id}",
                {},
            )
        if self._step is PublicationStepName.CONFIRM_REGISTRY:
            digest = hashlib.sha256(
                f"accepted:{intent.publication_id}".encode()
            ).hexdigest()
            return digest, f"acceptance:registry:{intent.publication_id}", {
                "registry_result": "accepted"
            }
        raise AssertionError(f"Unsupported acceptance step: {self._step.value}")

    def _effect_path(self, intent: EffectIntent) -> Path:
        return (
            self._state_root
            / "effects"
            / str(intent.publication_id)
            / f"{self._step.value}.json"
        )

    def _require_intent(self, intent: EffectIntent) -> None:
        if intent.step is not self._step:
            raise ValueError("Acceptance adapter received the wrong publication step")

    def _observation(
        self,
        intent: EffectIntent,
        status: ObservationStatus,
        *,
        content_digest: str | None = None,
        external_reference: str | None = None,
        context: dict[str, object] | None = None,
    ) -> ExternalObservation:
        return ExternalObservation(
            step=intent.step,
            status=status,
            observed_at=self._clock(),
            destination=intent.destination,
            effect_idempotency_key=intent.idempotency_key,
            adapter_identity=self.identity,
            adapter_version=self.version,
            content_digest=content_digest,
            external_reference=external_reference,
            context=context or {},
        )


class ActivatingReceiptCopyAdapter:
    def __init__(
        self,
        delegate: ReceiptReplicationAdapter,
        *,
        state_root: Path,
        artifact_root: Path,
    ) -> None:
        self._delegate = delegate
        self._state_root = state_root
        self._artifact_root = artifact_root

    @property
    def identity(self) -> str:
        return self._delegate.identity

    @property
    def version(self) -> str:
        return self._delegate.version

    async def apply(self, intent: EffectIntent) -> None:
        await self._delegate.apply(intent)
        value = intent.context.get("signed_receipt")
        if not isinstance(value, dict):
            raise ValueError("Acceptance receipt activation requires a signed receipt")
        receipt = SignedPublicationReceipt.model_validate(value)
        receipt_bytes = canonical_signed_receipt_bytes(receipt)
        manifest_bytes = _manifest_path(self._state_root, intent).read_bytes()
        record_bytes = canonical_json(_record())
        provenance_bytes = _provenance_bytes()
        manifest = PublicReadReleaseManifest.model_validate(json.loads(manifest_bytes)["payload"])
        pointer = PublicReadLatestPointer(
            release_version=manifest.release_version,
            manifest=artifact_descriptor(
                f"releases/v1/release-{manifest.release_version}.json",
                manifest_bytes,
                "application/vnd.opennosh.release+json",
            ),
            expires_at=receipt.receipt.published_at + timedelta(hours=23),
        )
        store = LocalArtifactStore(self._artifact_root)
        service = _read_service(store)
        try:
            await activate_verified_release(
                service=service,
                store=store,
                immutable_objects={
                    manifest.foods[0].record.object_key: record_bytes,
                    manifest.foods[0].provenance.object_key: provenance_bytes,
                },
                manifest_bytes=manifest_bytes,
                receipt_bytes=receipt_bytes,
                pointer_bytes=_signed_envelope(pointer.model_dump(mode="json")),
            )
        finally:
            await service.aclose()

    async def observe(self, intent: EffectIntent) -> ExternalObservation:
        observation = await self._delegate.observe(intent)
        if observation.status is not ObservationStatus.VERIFIED:
            return observation
        latest = await LocalArtifactStore(self._artifact_root).read(
            "latest/v1.json", max_bytes=256 * 1024
        )
        if latest is None:
            return ExternalObservation(
                step=intent.step,
                status=ObservationStatus.RETRYABLE_FAILURE,
                observed_at=observation.observed_at,
                destination=intent.destination,
                effect_idempotency_key=intent.idempotency_key,
                adapter_identity=self.identity,
                adapter_version=self.version,
                code="acceptance_release_not_activated",
            )
        return observation


def acceptance_publication_adapter_registry(
    *,
    state_root: Path,
    artifact_root: Path,
    clock: Callable[[], datetime],
) -> PublicationAdapterRegistry:
    _require_acceptance_environment()
    key_ring = PublicationReceiptKeyRing.from_json(
        json.dumps({ACCEPTANCE_RECEIPT_KEY_ID: ACCEPTANCE_RECEIPT_VERIFYING_KEY})
    )
    signer = Ed25519ReceiptSigner(
        key_id=ACCEPTANCE_RECEIPT_KEY_ID,
        publisher_identity="opennosh:browser-acceptance",
        private_key=_receipt_signing_key(),
        adapter_identity="opennosh.acceptance.receipt-signer",
    )
    signing_store = LocalImmutablePublicationReceiptStore(
        state_root / "signed-receipts",
        destination="urn:opennosh:receipt:signer",
    )
    registry_store = LocalImmutablePublicationReceiptStore(
        state_root / "registry-receipts",
        destination="urn:opennosh:registry:receipt",
    )
    copy_store = LocalImmutablePublicationReceiptStore(
        artifact_root,
        destination="urn:opennosh:durability:receipt",
    )
    adapters: dict[PublicationStepName, PublicationEffectAdapter] = {
        step: AcceptanceStepAdapter(step, state_root=state_root, clock=clock)
        for step in tuple(PublicationStepName)[:7]
    }
    adapters[PublicationStepName.SIGN_RECEIPT] = ReceiptSigningAdapter(
        signer=signer,
        store=signing_store,
        key_ring=key_ring,
        clock=clock,
    )
    adapters[PublicationStepName.PUBLISH_RECEIPT_REGISTRY] = ReceiptReplicationAdapter(
        step=PublicationStepName.PUBLISH_RECEIPT_REGISTRY,
        store=registry_store,
        key_ring=key_ring,
        clock=clock,
    )
    copy = ReceiptReplicationAdapter(
        step=PublicationStepName.COPY_RECEIPT,
        store=copy_store,
        key_ring=key_ring,
        clock=clock,
    )
    adapters[PublicationStepName.COPY_RECEIPT] = ActivatingReceiptCopyAdapter(
        copy,
        state_root=state_root,
        artifact_root=artifact_root,
    )
    return adapters


def acceptance_evidence_copy_digest() -> str:
    return _EVIDENCE_COPY_DIGEST


def _require_acceptance_environment() -> None:
    environment = os.environ.get("APP_ENVIRONMENT", "").lower()
    enabled = os.environ.get("OPENNOSH_ACCEPTANCE_FIXTURES") == "1"
    if environment not in {"development", "test", "testing"} or not enabled:
        raise RuntimeError(
            "Acceptance adapters require a development/test environment and explicit opt-in"
        )


def _manifest_bytes(publication_id: UUID, published_at: datetime) -> bytes:
    record_bytes = canonical_json(_record())
    provenance_bytes = _provenance_bytes()
    manifest = PublicReadReleaseManifest(
        release_version=ACCEPTANCE_RELEASE_VERSION,
        published_at=published_at,
        publication_receipt_key=receipt_object_key(publication_id),
        foods=(
            PublicFoodArtifact(
                source=ACCEPTANCE_SOURCE,
                source_id=ACCEPTANCE_SOURCE_ID,
                record=artifact_descriptor(
                    f"records/v1/{hashlib.sha256(record_bytes).hexdigest()}.json",
                    record_bytes,
                    "application/json",
                ),
                provenance=artifact_descriptor(
                    f"provenance/v1/{hashlib.sha256(provenance_bytes).hexdigest()}.html",
                    provenance_bytes,
                    "text/html",
                ),
            ),
        ),
    )
    return _signed_envelope(manifest.model_dump(mode="json"))


def _provenance_bytes() -> bytes:
    return (
        b'<!doctype html><html lang="en"><meta charset="utf-8">'
        b"<title>Rajma masala provenance</title><h1>Verified evidence</h1>"
        b"<p>Recipe analysis checked against two household preparations.</p></html>"
    )


def _manifest_path(state_root: Path, intent: EffectIntent) -> Path:
    return state_root / "manifests" / f"{intent.publication_id}.json"


def _read_service(store: LocalArtifactStore) -> PublicArtifactReadService:
    return PublicArtifactReadService(
        store=store,
        manifest_keys=ManifestKeyRing.from_config(
            f"{ACCEPTANCE_MANIFEST_KEY_ID}:{ACCEPTANCE_MANIFEST_VERIFYING_KEY}"
        ),
        receipt_keys=PublicationReceiptKeyRing.from_json(
            json.dumps({ACCEPTANCE_RECEIPT_KEY_ID: ACCEPTANCE_RECEIPT_VERIFYING_KEY})
        ),
    )


def _receipt_signing_key() -> Ed25519PrivateKey:
    from opennosh_api.acceptance.fixtures import _RECEIPT_SIGNING_KEY

    return _RECEIPT_SIGNING_KEY


def _write_immutable(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        if path.read_bytes() != payload:
            raise ValueError("Acceptance adapter state conflicts with existing bytes") from None
        return
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
