from __future__ import annotations

import asyncio
import hashlib
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from opennosh_api.evidence.contracts import (
    EvidenceAcknowledgementKind,
    EvidenceManifest,
    PublicDocumentManifest,
    SanitizedMediaManifest,
    VersionedPublicDatasetManifest,
)
from opennosh_api.evidence.policy import required_acknowledgements

_OBJECT_KEY = re.compile(r"^[a-z0-9][a-z0-9/._-]{0,1023}$")


@dataclass(frozen=True, slots=True)
class StoredEvidenceObservation:
    destination: str
    object_key: str
    content_digest: str
    external_reference: str
    size_bytes: int


@runtime_checkable
class EvidenceStore(Protocol):
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

    async def observe(self, object_key: str) -> StoredEvidenceObservation | None: ...


@runtime_checkable
class EvidenceSource(Protocol):
    async def payloads_for(
        self,
        manifest: EvidenceManifest,
    ) -> Mapping[EvidenceAcknowledgementKind, bytes]: ...


class ImmutableObjectConflictError(RuntimeError):
    pass


class LocalImmutableEvidenceStore:
    """Content-addressed local adapter for development and self-hosted deployments."""

    identity = "opennosh.local-immutable-evidence"
    version = "1.0"

    def __init__(self, root: Path, *, destination: str = "urn:opennosh:evidence:local") -> None:
        self._root = root.resolve(strict=False)
        self.destination = destination

    async def put_immutable(
        self,
        object_key: str,
        payload: bytes,
        *,
        expected_digest: str,
    ) -> None:
        await asyncio.to_thread(self._put_immutable_sync, object_key, payload, expected_digest)

    async def observe(self, object_key: str) -> StoredEvidenceObservation | None:
        return await asyncio.to_thread(self._observe_sync, object_key)

    def _put_immutable_sync(
        self, object_key: str, payload: bytes, expected_digest: str
    ) -> None:
        _validate_key(object_key)
        if hashlib.sha256(payload).hexdigest() != expected_digest:
            raise ValueError("Evidence payload digest does not match the expected digest")
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
                existing = self._observe_sync(object_key)
                if existing is None or existing.content_digest != expected_digest:
                    raise ImmutableObjectConflictError(
                        "Immutable evidence key already contains different bytes"
                    ) from None
            else:
                directory = os.open(target.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)

    def _observe_sync(self, object_key: str) -> StoredEvidenceObservation | None:
        target = self._target(object_key)
        if not target.is_file():
            return None
        payload = target.read_bytes()
        return StoredEvidenceObservation(
            destination=self.destination,
            object_key=object_key,
            content_digest=hashlib.sha256(payload).hexdigest(),
            external_reference=f"file:{object_key}",
            size_bytes=len(payload),
        )

    def _target(self, object_key: str) -> Path:
        _validate_key(object_key)
        target = (self._root / object_key).resolve(strict=False)
        if not target.is_relative_to(self._root):
            raise ValueError("Evidence object key escapes the configured root")
        return target


class MemoryEvidenceStore:
    identity = "opennosh.memory-evidence"
    version = "1.0"

    def __init__(self, *, destination: str = "urn:opennosh:evidence:memory") -> None:
        self.destination = destination
        self.objects: dict[str, bytes] = {}

    async def put_immutable(
        self,
        object_key: str,
        payload: bytes,
        *,
        expected_digest: str,
    ) -> None:
        _validate_key(object_key)
        if hashlib.sha256(payload).hexdigest() != expected_digest:
            raise ValueError("Evidence payload digest does not match the expected digest")
        existing = self.objects.get(object_key)
        if existing is not None and existing != payload:
            raise ImmutableObjectConflictError(
                "Immutable evidence key already contains different bytes"
            )
        self.objects[object_key] = payload

    async def observe(self, object_key: str) -> StoredEvidenceObservation | None:
        payload = self.objects.get(object_key)
        if payload is None:
            return None
        return StoredEvidenceObservation(
            destination=self.destination,
            object_key=object_key,
            content_digest=hashlib.sha256(payload).hexdigest(),
            external_reference=f"memory:{object_key}",
            size_bytes=len(payload),
        )


class LocalPrivateEvidenceSource:
    """Read draft evidence from a private local root without accepting arbitrary paths."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve(strict=False)

    async def payloads_for(
        self,
        manifest: EvidenceManifest,
    ) -> Mapping[EvidenceAcknowledgementKind, bytes]:
        payloads: dict[EvidenceAcknowledgementKind, bytes] = {}
        for kind in required_acknowledgements(manifest):
            if kind in {
                EvidenceAcknowledgementKind.SIGNED_DATASET_MANIFEST,
                EvidenceAcknowledgementKind.CITATION_MANIFEST,
                EvidenceAcknowledgementKind.SIGNED_ATTESTATION,
            }:
                continue
            reference: str | None
            if isinstance(manifest, SanitizedMediaManifest):
                reference = manifest.storage_reference
            elif isinstance(manifest, VersionedPublicDatasetManifest):
                reference = manifest.storage_reference
            elif isinstance(manifest, PublicDocumentManifest):
                reference = manifest.storage_reference
            else:
                reference = None
            if reference is None or not reference.startswith("private:"):
                raise FileNotFoundError(f"Private source is unavailable for {kind.value}")
            relative = reference.removeprefix("private:").lstrip("/")
            _validate_key(relative)
            source = (self._root / relative).resolve(strict=False)
            if not source.is_relative_to(self._root) or not source.is_file():
                raise FileNotFoundError(f"Private source is unavailable for {kind.value}")
            payloads[kind] = await asyncio.to_thread(source.read_bytes)
        return payloads


def _validate_key(object_key: str) -> None:
    if not _OBJECT_KEY.fullmatch(object_key) or ".." in object_key.split("/"):
        raise ValueError("Evidence object key is invalid")
