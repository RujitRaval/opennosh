from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import Any
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError
from opennosh_api.evidence.contracts import RedactionState, SanitizedMediaManifest
from opennosh_api.evidence.storage import (
    EvidenceUploadStorageError,
    ImmutableObjectConflictError,
    MemoryEvidenceUploadBroker,
    S3EvidenceQuarantineSource,
    S3EvidenceUploadBroker,
    S3ImmutableEvidenceStore,
    S3PrivateEvidenceSource,
    S3SanitizedEvidenceStore,
)


class FakeS3Client:
    def __init__(self) -> None:
        self.presign_calls: list[tuple[str, dict[str, Any]]] = []
        self.get_calls: list[dict[str, object]] = []
        self.delete_calls: list[dict[str, object]] = []
        self.put_calls: list[dict[str, object]] = []
        self.payload = b"evidence"

    def generate_presigned_url(self, operation: str, **arguments: Any) -> str:
        self.presign_calls.append((operation, arguments))
        return "https://uploads.example.test/quarantine/upload"

    def get_object(self, **arguments: object) -> dict[str, object]:
        self.get_calls.append(arguments)
        return {
            "ContentLength": len(self.payload),
            "ContentType": "image/png",
            "ETag": '"revision-1"',
            "Body": BytesIO(self.payload),
        }

    def delete_object(self, **arguments: object) -> None:
        self.delete_calls.append(arguments)

    def put_object(self, **arguments: object) -> None:
        self.put_calls.append(arguments)
        payload = arguments["Body"]
        assert isinstance(payload, bytes)
        self.payload = payload


def _broker(client: object) -> S3EvidenceUploadBroker:
    return S3EvidenceUploadBroker(
        endpoint="https://account.r2.cloudflarestorage.com",
        region="auto",
        bucket="opennosh-evidence-quarantine",
        access_key_id="quarantine-access",
        secret_access_key="quarantine-secret",
        client=client,
    )


@pytest.mark.asyncio
async def test_memory_upload_broker_is_private_bounded_and_deletable() -> None:
    broker = MemoryEvidenceUploadBroker()
    key = "quarantine/00000000-0000-4000-8000-000000000001"
    expiry = datetime.now(UTC) + timedelta(minutes=10)
    instruction = await broker.create_upload(
        key,
        media_type="image/png",
        byte_length=4,
        expires_at=expiry,
        expires_in_seconds=600,
    )
    broker.put_for_test(key, b"test", media_type="image/png")
    observed = await broker.observe(key, max_bytes=4)

    assert instruction.url.startswith("https://")
    assert observed is not None
    assert observed.content_digest == hashlib.sha256(b"test").hexdigest()
    private = await broker.read(key, max_bytes=4)
    assert private is not None
    assert private.payload == b"test"
    assert private.observation == observed
    await broker.delete(key)
    assert await broker.observe(key, max_bytes=4) is None
    assert await broker.read(key, max_bytes=4) is None


@pytest.mark.asyncio
async def test_memory_upload_broker_refuses_unbounded_read() -> None:
    broker = MemoryEvidenceUploadBroker()
    key = "quarantine/00000000-0000-4000-8000-000000000001"
    broker.put_for_test(key, b"too large", media_type="image/png")
    with pytest.raises(EvidenceUploadStorageError, match="bounded read"):
        await broker.observe(key, max_bytes=2)


@pytest.mark.asyncio
async def test_s3_upload_capability_is_conditional_and_declaration_bound() -> None:
    client = FakeS3Client()
    broker = _broker(client)
    expiry = datetime.now(UTC) + timedelta(minutes=10)

    instruction = await broker.create_upload(
        "quarantine/00000000-0000-4000-8000-000000000001",
        media_type="image/png",
        byte_length=8,
        expires_at=expiry,
        expires_in_seconds=600,
    )

    operation, arguments = client.presign_calls[0]
    assert operation == "put_object"
    assert arguments["ExpiresIn"] == 600
    assert arguments["Params"]["IfNoneMatch"] == "*"
    assert instruction.headers == {
        "content-type": "image/png",
        "content-length": "8",
        "if-none-match": "*",
    }


@pytest.mark.asyncio
async def test_s3_upload_capability_never_outlives_the_absolute_session_expiry() -> None:
    client = FakeS3Client()
    broker = _broker(client)

    with pytest.raises(EvidenceUploadStorageError, match="expiry has already elapsed"):
        await broker.create_upload(
            "quarantine/00000000-0000-4000-8000-000000000001",
            media_type="image/png",
            byte_length=8,
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
            expires_in_seconds=600,
        )

    assert client.presign_calls == []


@pytest.mark.asyncio
async def test_s3_upload_capability_and_delete_normalize_provider_failures() -> None:
    class FailingClient(FakeS3Client):
        def generate_presigned_url(self, operation: str, **arguments: Any) -> str:
            del operation, arguments
            raise OSError("signing unavailable")

        def delete_object(self, **arguments: object) -> None:
            del arguments
            raise OSError("delete unavailable")

    expiry = datetime.now(UTC) + timedelta(minutes=10)
    broker = _broker(FailingClient())
    with pytest.raises(EvidenceUploadStorageError, match="create an upload"):
        await broker.create_upload(
            "quarantine/00000000-0000-4000-8000-000000000001",
            media_type="image/png",
            byte_length=8,
            expires_at=expiry,
            expires_in_seconds=600,
        )
    with pytest.raises(EvidenceUploadStorageError, match="delete quarantined"):
        await broker.delete("quarantine/00000000-0000-4000-8000-000000000001")

    invalid_url = FakeS3Client()
    invalid_url.generate_presigned_url = lambda *args, **kwargs: "http://unsafe.test"  # type: ignore[method-assign]
    with pytest.raises(EvidenceUploadStorageError, match="invalid upload capability"):
        await _broker(invalid_url).create_upload(
            "quarantine/00000000-0000-4000-8000-000000000001",
            media_type="image/png",
            byte_length=8,
            expires_at=expiry,
            expires_in_seconds=600,
        )


@pytest.mark.asyncio
async def test_s3_upload_observation_hashes_an_exact_bounded_read() -> None:
    client = FakeS3Client()
    observation = await _broker(client).observe(
        "quarantine/00000000-0000-4000-8000-000000000001",
        max_bytes=100,
    )

    assert observation is not None
    assert observation.size_bytes == len(client.payload)
    assert observation.content_digest == hashlib.sha256(client.payload).hexdigest()
    assert observation.revision == '"revision-1"'


@pytest.mark.asyncio
async def test_s3_upload_observation_returns_none_only_for_missing_object() -> None:
    class MissingClient(FakeS3Client):
        def get_object(self, **arguments: object) -> dict[str, object]:
            del arguments
            raise ClientError(
                {
                    "Error": {"Code": "NoSuchKey", "Message": "missing"},
                    "ResponseMetadata": {"HTTPStatusCode": 404},
                },
                "GetObject",
            )

    assert (
        await _broker(MissingClient()).observe(
            "quarantine/00000000-0000-4000-8000-000000000001",
            max_bytes=100,
        )
        is None
    )


@pytest.mark.asyncio
async def test_s3_upload_observation_normalizes_provider_and_body_failures() -> None:
    class ProviderFailureClient(FakeS3Client):
        def get_object(self, **arguments: object) -> dict[str, object]:
            del arguments
            raise OSError("network unavailable")

    class FailingBody:
        def read(self, size: int) -> bytes:
            del size
            raise OSError("stream interrupted")

        def close(self) -> None:
            pass

    class BodyFailureClient(FakeS3Client):
        def get_object(self, **arguments: object) -> dict[str, object]:
            del arguments
            return {
                "ContentLength": 8,
                "ContentType": "image/png",
                "ETag": '"revision-1"',
                "Body": FailingBody(),
            }

    for client in (ProviderFailureClient(), BodyFailureClient()):
        with pytest.raises(EvidenceUploadStorageError, match="Could not read private evidence"):
            await _broker(client).observe(
                "quarantine/00000000-0000-4000-8000-000000000001",
                max_bytes=100,
            )


@pytest.mark.asyncio
async def test_s3_upload_observation_rejects_invalid_provider_metadata_and_reads() -> None:
    key = "quarantine/00000000-0000-4000-8000-000000000001"

    class ProviderErrorClient(FakeS3Client):
        def get_object(self, **arguments: object) -> dict[str, object]:
            del arguments
            raise ClientError(
                {
                    "Error": {"Code": "AccessDenied", "Message": "denied"},
                    "ResponseMetadata": {"HTTPStatusCode": 403},
                },
                "GetObject",
            )

    class InvalidResponseClient(FakeS3Client):
        def get_object(self, **arguments: object):  # type: ignore[no-untyped-def]
            del arguments
            return "not-a-mapping"

    class InvalidMetadataClient(FakeS3Client):
        def get_object(self, **arguments: object) -> dict[str, object]:
            del arguments
            return {"ContentLength": -1, "Body": BytesIO(b"")}

    class SizeDriftClient(FakeS3Client):
        def get_object(self, **arguments: object) -> dict[str, object]:
            del arguments
            return {
                "ContentLength": 9,
                "ContentType": "image/png",
                "ETag": '"revision"',
                "Body": BytesIO(b"evidence"),
            }

    for client, message in (
        (ProviderErrorClient(), "Could not read private evidence"),
        (InvalidResponseClient(), "invalid evidence metadata"),
        (InvalidMetadataClient(), "invalid evidence metadata"),
        (SizeDriftClient(), "size changed"),
    ):
        with pytest.raises(EvidenceUploadStorageError, match=message):
            await _broker(client).observe(key, max_bytes=100)

    with pytest.raises(ValueError, match="bound must be positive"):
        await _broker(FakeS3Client()).observe(key, max_bytes=0)


@pytest.mark.asyncio
async def test_s3_upload_observation_rejects_size_drift() -> None:
    client = FakeS3Client()
    client.payload = b"larger than declared"
    with pytest.raises(EvidenceUploadStorageError, match="bounded read"):
        await _broker(client).observe(
            "quarantine/00000000-0000-4000-8000-000000000001",
            max_bytes=2,
        )


@pytest.mark.asyncio
async def test_s3_upload_delete_is_bucket_and_key_bounded() -> None:
    client = FakeS3Client()
    key = "quarantine/00000000-0000-4000-8000-000000000001"
    await _broker(client).delete(key)
    assert client.delete_calls == [{"Bucket": "opennosh-evidence-quarantine", "Key": key}]


@pytest.mark.asyncio
async def test_s3_quarantine_source_has_read_delete_but_no_upload_authority() -> None:
    client = FakeS3Client()
    source = S3EvidenceQuarantineSource(
        endpoint="https://account.r2.cloudflarestorage.com",
        region="auto",
        bucket="opennosh-evidence-quarantine",
        access_key_id="worker-read-access",
        secret_access_key="worker-read-secret",
        client=client,
    )
    key = "quarantine/00000000-0000-4000-8000-000000000001"

    private = await source.read(key, max_bytes=100)
    assert private is not None
    assert private.payload == client.payload
    assert private.observation.content_digest == hashlib.sha256(client.payload).hexdigest()
    assert not hasattr(source, "create_upload")

    await source.delete(key)
    assert client.delete_calls == [{"Bucket": "opennosh-evidence-quarantine", "Key": key}]


@pytest.mark.asyncio
async def test_s3_quarantine_source_maps_missing_and_delete_failure() -> None:
    class MissingAndFailingClient(FakeS3Client):
        def get_object(self, **arguments: object) -> dict[str, object]:
            del arguments
            raise ClientError(
                {
                    "Error": {"Code": "NoSuchKey", "Message": "missing"},
                    "ResponseMetadata": {"HTTPStatusCode": 404},
                },
                "GetObject",
            )

        def delete_object(self, **arguments: object) -> None:
            del arguments
            raise OSError("delete unavailable")

    source = S3EvidenceQuarantineSource(
        endpoint="https://account.r2.cloudflarestorage.com",
        region="auto",
        bucket="opennosh-evidence-quarantine",
        access_key_id="worker-read-access",
        secret_access_key="worker-read-secret",
        client=MissingAndFailingClient(),
    )
    key = "quarantine/00000000-0000-4000-8000-000000000001"
    assert await source.read(key, max_bytes=100) is None
    with pytest.raises(EvidenceUploadStorageError, match="delete quarantined"):
        await source.delete(key)


@pytest.mark.parametrize(
    ("argument", "value", "message"),
    [
        ("endpoint", "http://unsafe.example.test", "HTTPS"),
        ("region", "", "configuration is incomplete"),
        ("access_key_id", "", "configuration is incomplete"),
        ("operation_timeout_seconds", 6.0, "between zero and five"),
    ],
)
def test_s3_upload_broker_rejects_unsafe_configuration(
    argument: str, value: object, message: str
) -> None:
    arguments: dict[str, object] = {
        "endpoint": "https://account.r2.cloudflarestorage.com",
        "region": "auto",
        "bucket": "opennosh-evidence-quarantine",
        "access_key_id": "access",
        "secret_access_key": "secret",
        "client": FakeS3Client(),
    }
    arguments[argument] = value
    with pytest.raises(ValueError, match=message):
        S3EvidenceUploadBroker(**arguments)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_s3_private_source_reads_only_the_opaque_sanitized_reference() -> None:
    client = FakeS3Client()
    digest = hashlib.sha256(client.payload).hexdigest()
    manifest = SanitizedMediaManifest(
        evidence_id=uuid4(),
        content_digest=digest,
        safe_format="image/png",
        source_description="Package label",
        rights_acknowledged=True,
        redaction_state=RedactionState.REVIEWED,
        storage_reference="private:sanitized/example.png",
    )
    source = S3PrivateEvidenceSource(
        endpoint="https://account.r2.cloudflarestorage.com",
        region="auto",
        bucket="opennosh-evidence-sanitized",
        access_key_id="sanitized-access",
        secret_access_key="sanitized-secret",
        client=client,
    )

    payloads = await source.payloads_for(manifest)

    assert tuple(payloads.values()) == (client.payload,)
    assert client.get_calls[0]["Bucket"] == "opennosh-evidence-sanitized"
    assert client.get_calls[0]["Key"] == "sanitized/example.png"


@pytest.mark.asyncio
async def test_s3_private_source_fails_closed_for_invalid_or_missing_references() -> None:
    manifest = SanitizedMediaManifest(
        evidence_id=uuid4(),
        content_digest=hashlib.sha256(b"evidence").hexdigest(),
        safe_format="image/png",
        source_description="Package label",
        rights_acknowledged=True,
        redaction_state=RedactionState.REVIEWED,
        storage_reference="public:unsafe",
    )
    arguments = {
        "endpoint": "https://account.r2.cloudflarestorage.com",
        "region": "auto",
        "bucket": "opennosh-evidence-sanitized",
        "access_key_id": "sanitized-access",
        "secret_access_key": "sanitized-secret",
    }
    with pytest.raises(ValueError, match="bound must be positive"):
        S3PrivateEvidenceSource(**arguments, max_bytes=0, client=FakeS3Client())

    source = S3PrivateEvidenceSource(**arguments, client=FakeS3Client())
    with pytest.raises(FileNotFoundError, match="Private source is unavailable"):
        await source.payloads_for(manifest)

    class MissingClient(FakeS3Client):
        def get_object(self, **arguments: object) -> dict[str, object]:
            del arguments
            raise ClientError(
                {
                    "Error": {"Code": "NoSuchKey", "Message": "missing"},
                    "ResponseMetadata": {"HTTPStatusCode": 404},
                },
                "GetObject",
            )

    missing_manifest = manifest.model_copy(update={"storage_reference": "private:missing"})
    missing_source = S3PrivateEvidenceSource(**arguments, client=MissingClient())
    with pytest.raises(FileNotFoundError, match="Private source is unavailable"):
        await missing_source.payloads_for(missing_manifest)


@pytest.mark.asyncio
async def test_s3_immutable_store_conditionally_writes_and_independently_observes() -> None:
    client = FakeS3Client()
    store = S3ImmutableEvidenceStore(
        endpoint="https://account.r2.cloudflarestorage.com",
        region="auto",
        bucket="opennosh-evidence-immutable",
        access_key_id="immutable-access",
        secret_access_key="immutable-secret",
        client=client,
    )
    payload = b"sanitized evidence"
    digest = hashlib.sha256(payload).hexdigest()

    await store.put_immutable("sha256/example", payload, expected_digest=digest)

    assert client.put_calls[0]["IfNoneMatch"] == "*"
    assert client.get_calls == [{"Bucket": "opennosh-evidence-immutable", "Key": "sha256/example"}]
    observation = await store.observe("sha256/example")
    assert observation is not None
    assert observation.content_digest == digest


@pytest.mark.asyncio
async def test_sanitized_store_has_distinct_identity_and_verified_conditional_write() -> None:
    client = FakeS3Client()
    store = S3SanitizedEvidenceStore(
        endpoint="https://account.r2.cloudflarestorage.com",
        region="auto",
        bucket="opennosh-evidence-sanitized",
        access_key_id="sanitized-access",
        secret_access_key="sanitized-secret",
        client=client,
    )
    payload = b"safe rewritten image"
    digest = hashlib.sha256(payload).hexdigest()

    await store.put_immutable(f"sanitized/{digest}.png", payload, expected_digest=digest)

    assert store.identity == "opennosh.s3-sanitized-evidence"
    assert client.put_calls[0]["IfNoneMatch"] == "*"
    observation = await store.observe(f"sanitized/{digest}.png")
    assert observation is not None
    assert observation.content_digest == digest


@pytest.mark.asyncio
async def test_s3_immutable_store_rejects_existing_different_bytes() -> None:
    class ConflictClient(FakeS3Client):
        def put_object(self, **arguments: object) -> None:
            del arguments
            raise ClientError(
                {
                    "Error": {"Code": "PreconditionFailed", "Message": "exists"},
                    "ResponseMetadata": {"HTTPStatusCode": 412},
                },
                "PutObject",
            )

    client = ConflictClient()
    store = S3ImmutableEvidenceStore(
        endpoint="https://account.r2.cloudflarestorage.com",
        region="auto",
        bucket="opennosh-evidence-immutable",
        access_key_id="immutable-access",
        secret_access_key="immutable-secret",
        client=client,
    )

    with pytest.raises(ImmutableObjectConflictError, match="different bytes"):
        await store.put_immutable(
            "sha256/example",
            b"new bytes",
            expected_digest=hashlib.sha256(b"new bytes").hexdigest(),
        )


@pytest.mark.asyncio
async def test_s3_immutable_store_rejects_invalid_bounds_writes_and_readback() -> None:
    arguments = {
        "endpoint": "https://account.r2.cloudflarestorage.com",
        "region": "auto",
        "bucket": "opennosh-evidence-immutable",
        "access_key_id": "immutable-access",
        "secret_access_key": "immutable-secret",
    }
    with pytest.raises(ValueError, match="bound must be positive"):
        S3ImmutableEvidenceStore(**arguments, max_bytes=0, client=FakeS3Client())

    store = S3ImmutableEvidenceStore(**arguments, client=FakeS3Client())
    with pytest.raises(ValueError, match="digest does not match"):
        await store.put_immutable(
            "sha256/empty", b"", expected_digest=hashlib.sha256(b"").hexdigest()
        )
    with pytest.raises(ValueError, match="digest does not match"):
        await store.put_immutable("sha256/bad", b"data", expected_digest="0" * 64)

    class ProviderFailureClient(FakeS3Client):
        def put_object(self, **arguments: object) -> None:
            del arguments
            raise OSError("provider unavailable")

    with pytest.raises(EvidenceUploadStorageError, match="write failed"):
        await S3ImmutableEvidenceStore(**arguments, client=ProviderFailureClient()).put_immutable(
            "sha256/provider",
            b"data",
            expected_digest=hashlib.sha256(b"data").hexdigest(),
        )

    class MissingReadbackClient(FakeS3Client):
        def put_object(self, **arguments: object) -> None:
            del arguments

        def get_object(self, **arguments: object) -> dict[str, object]:
            del arguments
            raise ClientError(
                {
                    "Error": {"Code": "NoSuchKey", "Message": "missing"},
                    "ResponseMetadata": {"HTTPStatusCode": 404},
                },
                "GetObject",
            )

    missing_store = S3ImmutableEvidenceStore(**arguments, client=MissingReadbackClient())
    assert await missing_store.observe("sha256/missing") is None
    with pytest.raises(EvidenceUploadStorageError, match="read-back did not verify"):
        await missing_store.put_immutable(
            "sha256/missing",
            b"data",
            expected_digest=hashlib.sha256(b"data").hexdigest(),
        )


def test_s3_configuration_rejects_invalid_bucket() -> None:
    with pytest.raises(ValueError, match="bucket is invalid"):
        S3EvidenceUploadBroker(
            endpoint="https://account.r2.cloudflarestorage.com",
            region="auto",
            bucket="INVALID_BUCKET",
            access_key_id="access",
            secret_access_key="secret",
            client=FakeS3Client(),
        )
