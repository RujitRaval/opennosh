from __future__ import annotations

import asyncio
import hashlib
import os
import re
import tempfile
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from math import ceil
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from opennosh_api.evidence.contracts import (
    EvidenceAcknowledgementKind,
    EvidenceManifest,
    PublicDocumentManifest,
    SanitizedMediaManifest,
    VersionedPublicDatasetManifest,
)
from opennosh_api.evidence.policy import required_acknowledgements
from opennosh_api.public.r2 import _run_daemon_with_deadline

_OBJECT_KEY = re.compile(r"^[a-z0-9][a-z0-9/._-]{0,1023}$")
_S3_BUCKET = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])$")


@dataclass(frozen=True, slots=True)
class StoredEvidenceObservation:
    destination: str
    object_key: str
    content_digest: str
    external_reference: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class EvidenceUploadInstruction:
    method: Literal["PUT"]
    url: str
    headers: Mapping[str, str]
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class QuarantinedEvidenceObservation:
    object_key: str
    media_type: str
    size_bytes: int
    content_digest: str
    revision: str


@dataclass(frozen=True, slots=True)
class QuarantinedEvidenceObject:
    payload: bytes
    observation: QuarantinedEvidenceObservation


class EvidenceUploadStorageError(RuntimeError):
    """A bounded quarantine operation could not be completed."""


class EvidenceUploadObjectTooLargeError(EvidenceUploadStorageError):
    """Provider metadata proves the object exceeds the configured hard limit."""


@runtime_checkable
class EvidenceUploadBroker(Protocol):
    async def create_upload(
        self,
        object_key: str,
        *,
        media_type: str,
        byte_length: int,
        expires_at: datetime,
        expires_in_seconds: int,
    ) -> EvidenceUploadInstruction: ...

    async def observe(
        self,
        object_key: str,
        *,
        max_bytes: int,
    ) -> QuarantinedEvidenceObservation | None: ...

    async def delete(self, object_key: str) -> None: ...


@runtime_checkable
class EvidenceQuarantineSource(Protocol):
    async def read(
        self,
        object_key: str,
        *,
        max_bytes: int,
    ) -> QuarantinedEvidenceObject | None: ...

    async def delete(self, object_key: str) -> None: ...


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
class PrivateEvidenceSource(Protocol):
    async def payloads_for(
        self,
        manifest: EvidenceManifest,
    ) -> Mapping[EvidenceAcknowledgementKind, bytes]: ...


# Compatibility name retained for the existing evidence worker.
EvidenceSource = PrivateEvidenceSource


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

    def _put_immutable_sync(self, object_key: str, payload: bytes, expected_digest: str) -> None:
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


class MemoryEvidenceUploadBroker:
    """Deterministic quarantine adapter used by tests and local composition."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str, str]] = {}
        self.operations: list[tuple[str, str]] = []

    async def create_upload(
        self,
        object_key: str,
        *,
        media_type: str,
        byte_length: int,
        expires_at: datetime,
        expires_in_seconds: int,
    ) -> EvidenceUploadInstruction:
        _validate_key(object_key)
        self.operations.append(("create", object_key))
        return EvidenceUploadInstruction(
            method="PUT",
            url=f"https://upload.invalid/{object_key}",
            headers={"content-type": media_type, "content-length": str(byte_length)},
            expires_at=expires_at,
        )

    async def observe(
        self,
        object_key: str,
        *,
        max_bytes: int,
    ) -> QuarantinedEvidenceObservation | None:
        _validate_key(object_key)
        self.operations.append(("observe", object_key))
        stored = self.objects.get(object_key)
        if stored is None:
            return None
        payload, media_type, revision = stored
        if len(payload) > max_bytes:
            raise EvidenceUploadObjectTooLargeError(
                "Quarantined object exceeds the bounded read size"
            )
        return QuarantinedEvidenceObservation(
            object_key=object_key,
            media_type=media_type,
            size_bytes=len(payload),
            content_digest=hashlib.sha256(payload).hexdigest(),
            revision=revision,
        )

    async def delete(self, object_key: str) -> None:
        _validate_key(object_key)
        self.operations.append(("delete", object_key))
        self.objects.pop(object_key, None)

    async def read(
        self,
        object_key: str,
        *,
        max_bytes: int,
    ) -> QuarantinedEvidenceObject | None:
        observation = await self.observe(object_key, max_bytes=max_bytes)
        if observation is None:
            return None
        payload, _media_type, _revision = self.objects[object_key]
        return QuarantinedEvidenceObject(payload=payload, observation=observation)

    def put_for_test(
        self,
        object_key: str,
        payload: bytes,
        *,
        media_type: str,
        revision: str = '"test-revision"',
    ) -> None:
        _validate_key(object_key)
        self.objects[object_key] = (payload, media_type, revision)


class S3EvidenceUploadBroker:
    """Bounded SigV4 quarantine adapter with no sanitized or immutable authority."""

    def __init__(
        self,
        *,
        endpoint: str,
        region: str,
        bucket: str,
        access_key_id: str,
        secret_access_key: str,
        client: Any | None = None,
        operation_timeout_seconds: float = 2.5,
    ) -> None:
        _validate_s3_configuration(
            endpoint=endpoint,
            region=region,
            bucket=bucket,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            operation_timeout_seconds=operation_timeout_seconds,
        )
        self._bucket = bucket
        self._timeout = operation_timeout_seconds
        self._client = client or boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=Config(
                signature_version="s3v4",
                connect_timeout=1,
                read_timeout=1.5,
                retries={"mode": "standard", "total_max_attempts": 1},
            ),
        )

    async def create_upload(
        self,
        object_key: str,
        *,
        media_type: str,
        byte_length: int,
        expires_at: datetime,
        expires_in_seconds: int,
    ) -> EvidenceUploadInstruction:
        _validate_key(object_key)
        remaining_seconds = ceil((expires_at - datetime.now(UTC)).total_seconds())
        if remaining_seconds < 1:
            raise EvidenceUploadStorageError("Upload capability expiry has already elapsed")
        bounded_expiry_seconds = min(expires_in_seconds, remaining_seconds)
        try:
            url = self._client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": self._bucket,
                    "Key": object_key,
                    "ContentType": media_type,
                    "ContentLength": byte_length,
                    "IfNoneMatch": "*",
                },
                ExpiresIn=bounded_expiry_seconds,
                HttpMethod="PUT",
            )
        except Exception as error:
            raise EvidenceUploadStorageError("Could not create an upload capability") from error
        if not isinstance(url, str) or not url.startswith("https://"):
            raise EvidenceUploadStorageError("Object store returned an invalid upload capability")
        return EvidenceUploadInstruction(
            method="PUT",
            url=url,
            headers={
                "content-type": media_type,
                "content-length": str(byte_length),
                "if-none-match": "*",
            },
            expires_at=expires_at,
        )

    async def observe(
        self,
        object_key: str,
        *,
        max_bytes: int,
    ) -> QuarantinedEvidenceObservation | None:
        _validate_key(object_key)
        observed = await _read_s3_payload(
            self._client,
            bucket=self._bucket,
            object_key=object_key,
            max_bytes=max_bytes,
            timeout_seconds=self._timeout,
        )
        if observed is None:
            return None
        payload, media_type, revision = observed
        return QuarantinedEvidenceObservation(
            object_key=object_key,
            media_type=media_type,
            size_bytes=len(payload),
            content_digest=hashlib.sha256(payload).hexdigest(),
            revision=revision,
        )

    async def delete(self, object_key: str) -> None:
        _validate_key(object_key)
        try:
            await _run_daemon_with_deadline(
                partial(self._client.delete_object, Bucket=self._bucket, Key=object_key),
                timeout_seconds=self._timeout,
            )
        except Exception as error:
            raise EvidenceUploadStorageError("Could not delete quarantined evidence") from error


class S3EvidenceQuarantineSource:
    """Read/delete-only quarantine adapter for the isolated evidence worker."""

    def __init__(
        self,
        *,
        endpoint: str,
        region: str,
        bucket: str,
        access_key_id: str,
        secret_access_key: str,
        client: Any | None = None,
        operation_timeout_seconds: float = 2.5,
    ) -> None:
        _validate_s3_configuration(
            endpoint=endpoint,
            region=region,
            bucket=bucket,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            operation_timeout_seconds=operation_timeout_seconds,
        )
        self._bucket = bucket
        self._timeout = operation_timeout_seconds
        self._client = client or _build_s3_client(
            endpoint=endpoint,
            region=region,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
        )

    async def read(
        self,
        object_key: str,
        *,
        max_bytes: int,
    ) -> QuarantinedEvidenceObject | None:
        observed = await _read_s3_payload(
            self._client,
            bucket=self._bucket,
            object_key=object_key,
            max_bytes=max_bytes,
            timeout_seconds=self._timeout,
        )
        if observed is None:
            return None
        payload, media_type, revision = observed
        return QuarantinedEvidenceObject(
            payload=payload,
            observation=QuarantinedEvidenceObservation(
                object_key=object_key,
                media_type=media_type,
                size_bytes=len(payload),
                content_digest=hashlib.sha256(payload).hexdigest(),
                revision=revision,
            ),
        )

    async def delete(self, object_key: str) -> None:
        _validate_key(object_key)
        try:
            await _run_daemon_with_deadline(
                partial(self._client.delete_object, Bucket=self._bucket, Key=object_key),
                timeout_seconds=self._timeout,
            )
        except Exception as error:
            raise EvidenceUploadStorageError("Could not delete quarantined evidence") from error


async def _read_s3_payload(
    client: Any,
    *,
    bucket: str,
    object_key: str,
    max_bytes: int,
    timeout_seconds: float,
) -> tuple[bytes, str, str] | None:
    _validate_key(object_key)
    if max_bytes < 1:
        raise ValueError("Private object read bound must be positive")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    try:
        response = await _run_daemon_with_deadline(
            partial(client.get_object, Bucket=bucket, Key=object_key),
            timeout_seconds=timeout_seconds,
        )
    except ClientError as error:
        status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        code = error.response.get("Error", {}).get("Code")
        if status == 404 or code in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise EvidenceUploadStorageError("Could not read private evidence") from error
    except Exception as error:
        raise EvidenceUploadStorageError("Could not read private evidence") from error
    if not isinstance(response, Mapping):
        raise EvidenceUploadStorageError("Object store returned invalid evidence metadata")
    declared = response.get("ContentLength")
    media_type = response.get("ContentType", "application/octet-stream")
    revision = response.get("VersionId") or response.get("ETag")
    body = response.get("Body")
    if (
        not isinstance(declared, int)
        or declared < 0
        or not isinstance(media_type, str)
        or not isinstance(revision, str)
        or body is None
        or not hasattr(body, "read")
    ):
        raise EvidenceUploadStorageError("Object store returned invalid evidence metadata")
    if declared > max_bytes:
        raise EvidenceUploadObjectTooLargeError("Quarantined object exceeds the bounded read size")
    try:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise EvidenceUploadStorageError("Evidence observation exceeded its absolute deadline")
        payload = await _run_daemon_with_deadline(
            partial(body.read, max_bytes + 1),
            timeout_seconds=remaining,
        )
    except EvidenceUploadObjectTooLargeError:
        raise
    except EvidenceUploadStorageError:
        raise
    except Exception as error:
        raise EvidenceUploadStorageError("Could not read private evidence") from error
    finally:
        close = getattr(body, "close", None)
        if callable(close):
            threading.Thread(
                target=close,
                name="opennosh-evidence-close",
                daemon=True,
            ).start()
    if not isinstance(payload, bytes) or len(payload) > max_bytes:
        raise EvidenceUploadObjectTooLargeError("Quarantined object exceeds the bounded read size")
    if len(payload) != declared:
        raise EvidenceUploadStorageError("Private evidence size changed during read-back")
    return payload, media_type.split(";", 1)[0].strip().lower(), revision


class S3PrivateEvidenceSource:
    """Read only opaque private references from one sanitized-source bucket."""

    def __init__(
        self,
        *,
        endpoint: str,
        region: str,
        bucket: str,
        access_key_id: str,
        secret_access_key: str,
        max_bytes: int = 10_485_760,
        client: Any | None = None,
        operation_timeout_seconds: float = 2.5,
    ) -> None:
        _validate_s3_configuration(
            endpoint=endpoint,
            region=region,
            bucket=bucket,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            operation_timeout_seconds=operation_timeout_seconds,
        )
        if max_bytes < 1:
            raise ValueError("Private evidence read bound must be positive")
        self._bucket = bucket
        self._max_bytes = max_bytes
        self._timeout = operation_timeout_seconds
        self._client = client or _build_s3_client(
            endpoint=endpoint,
            region=region,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
        )

    async def payloads_for(
        self,
        manifest: EvidenceManifest,
    ) -> Mapping[EvidenceAcknowledgementKind, bytes]:
        reference = (
            manifest.storage_reference
            if isinstance(
                manifest,
                SanitizedMediaManifest | VersionedPublicDatasetManifest | PublicDocumentManifest,
            )
            else None
        )
        payloads: dict[EvidenceAcknowledgementKind, bytes] = {}
        for kind in required_acknowledgements(manifest):
            if kind in {
                EvidenceAcknowledgementKind.SIGNED_DATASET_MANIFEST,
                EvidenceAcknowledgementKind.CITATION_MANIFEST,
                EvidenceAcknowledgementKind.SIGNED_ATTESTATION,
            }:
                continue
            if reference is None or not reference.startswith("private:"):
                raise FileNotFoundError(f"Private source is unavailable for {kind.value}")
            object_key = reference.removeprefix("private:").lstrip("/")
            observed = await _read_s3_payload(
                self._client,
                bucket=self._bucket,
                object_key=object_key,
                max_bytes=self._max_bytes,
                timeout_seconds=self._timeout,
            )
            if observed is None:
                raise FileNotFoundError(f"Private source is unavailable for {kind.value}")
            payloads[kind] = observed[0]
        return payloads


class S3ImmutableEvidenceStore:
    """Conditional, independently observed immutable evidence destination."""

    identity = "opennosh.s3-immutable-evidence"
    version = "1.0"

    def __init__(
        self,
        *,
        endpoint: str,
        region: str,
        bucket: str,
        access_key_id: str,
        secret_access_key: str,
        max_bytes: int = 10_485_760,
        client: Any | None = None,
        operation_timeout_seconds: float = 2.5,
    ) -> None:
        _validate_s3_configuration(
            endpoint=endpoint,
            region=region,
            bucket=bucket,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            operation_timeout_seconds=operation_timeout_seconds,
        )
        if max_bytes < 1:
            raise ValueError("Immutable evidence read bound must be positive")
        self._bucket = bucket
        self._timeout = operation_timeout_seconds
        self._max_bytes = max_bytes
        self.destination = f"s3://{bucket}"
        self._client = client or _build_s3_client(
            endpoint=endpoint,
            region=region,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
        )

    async def put_immutable(
        self,
        object_key: str,
        payload: bytes,
        *,
        expected_digest: str,
    ) -> None:
        _validate_key(object_key)
        if (
            not payload
            or len(payload) > self._max_bytes
            or hashlib.sha256(payload).hexdigest() != expected_digest
        ):
            raise ValueError("Evidence payload digest does not match the expected digest")
        try:
            await _run_daemon_with_deadline(
                partial(
                    self._client.put_object,
                    Bucket=self._bucket,
                    Key=object_key,
                    Body=payload,
                    ContentType="application/octet-stream",
                    IfNoneMatch="*",
                ),
                timeout_seconds=self._timeout,
            )
        except ClientError as error:
            status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status not in {409, 412}:
                raise EvidenceUploadStorageError("Immutable evidence write failed") from error
            existing = await self.observe(object_key)
            if existing is None or existing.content_digest != expected_digest:
                raise ImmutableObjectConflictError(
                    "Immutable evidence key already contains different bytes"
                ) from error
            return
        except Exception as error:
            raise EvidenceUploadStorageError("Immutable evidence write failed") from error
        observed = await self.observe(object_key)
        if (
            observed is None
            or observed.content_digest != expected_digest
            or observed.size_bytes != len(payload)
        ):
            raise EvidenceUploadStorageError("Immutable evidence read-back did not verify")

    async def observe(self, object_key: str) -> StoredEvidenceObservation | None:
        observed = await _read_s3_payload(
            self._client,
            bucket=self._bucket,
            object_key=object_key,
            max_bytes=self._max_bytes,
            timeout_seconds=self._timeout,
        )
        if observed is None:
            return None
        payload, _media_type, revision = observed
        return StoredEvidenceObservation(
            destination=self.destination,
            object_key=object_key,
            content_digest=hashlib.sha256(payload).hexdigest(),
            external_reference=f"s3:{self._bucket}/{object_key}?revision={revision}",
            size_bytes=len(payload),
        )


class S3SanitizedEvidenceStore(S3ImmutableEvidenceStore):
    """Content-addressed private sanitized source with conditional create/read-back."""

    identity = "opennosh.s3-sanitized-evidence"


def _build_s3_client(
    *,
    endpoint: str,
    region: str,
    access_key_id: str,
    secret_access_key: str,
) -> Any:
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        config=Config(
            signature_version="s3v4",
            connect_timeout=1,
            read_timeout=1.5,
            retries={"mode": "standard", "total_max_attempts": 1},
        ),
    )


def _validate_s3_configuration(
    *,
    endpoint: str,
    region: str,
    bucket: str,
    access_key_id: str,
    secret_access_key: str,
    operation_timeout_seconds: float,
) -> None:
    if not endpoint.startswith("https://"):
        raise ValueError("Evidence endpoint must use HTTPS")
    if not _S3_BUCKET.fullmatch(bucket):
        raise ValueError("Evidence bucket is invalid")
    if not region.strip() or not access_key_id.strip() or not secret_access_key.strip():
        raise ValueError("Evidence S3 configuration is incomplete")
    if not 0 < operation_timeout_seconds <= 5:
        raise ValueError("Evidence operation timeout must be between zero and five seconds")


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
