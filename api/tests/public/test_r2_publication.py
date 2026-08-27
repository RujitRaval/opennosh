from __future__ import annotations

import asyncio
import hashlib
import subprocess
import threading
import time
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from opennosh_api.public.bootstrap import StarterReleaseInventory, StarterReleaseObject
from opennosh_api.public.r2 import (
    R2ImmutableConflictError,
    R2PublicationError,
    S3R2ObjectWriter,
    WranglerR2ObjectWriter,
    publish_starter_release_to_r2,
)
from opennosh_api.public.refresh import MAX_R2_OPERATIONS_PER_REFRESH


class FakeWriter:
    def __init__(self, remote: dict[str, bytes], *, persist: bool = True) -> None:
        self.remote = remote
        self.persist = persist
        self.calls: list[tuple[str, str, str]] = []

    async def put(
        self,
        *,
        bucket: str,
        object_key: str,
        source: Path,
        media_type: str,
        cache_control: str,
    ) -> None:
        del media_type
        self.calls.append((bucket, object_key, cache_control))
        if self.persist:
            self.remote[object_key] = await asyncio.to_thread(source.read_bytes)


def _object(key: str, payload: bytes, *, pointer: bool = False) -> StarterReleaseObject:
    return StarterReleaseObject(
        object_key=key,
        digest=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        media_type="application/json",
        mutable_pointer=pointer,
    )


def _fixture(tmp_path: Path):
    payloads = {
        "records/v1/record.json": b'{"record":1}',
        "releases/v1/release-0.56.0.0.json": b'{"release":1}',
        "latest/v1.json": b'{"latest":1}',
    }
    for key, payload in payloads.items():
        path = tmp_path / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    inventory = StarterReleaseInventory(
        release_version="0.56.0.0",
        published_at=datetime(2026, 8, 27, 2, tzinfo=UTC),
        source_commit="a" * 40,
        source_inventory_digest="b" * 64,
        manifest_key_id="manifest-production",
        manifest_verifying_key="c" * 43,
        receipt_key_id="receipt-production",
        receipt_verifying_key="d" * 43,
        food_count=1,
        pack_count=1,
        total_bytes=sum(len(payload) for payload in payloads.values()),
        objects=(
            _object("records/v1/record.json", payloads["records/v1/record.json"]),
            _object(
                "releases/v1/release-0.56.0.0.json",
                payloads["releases/v1/release-0.56.0.0.json"],
            ),
            _object("latest/v1.json", payloads["latest/v1.json"], pointer=True),
        ),
    )
    return payloads, inventory


def _client(remote: dict[str, bytes]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        prefix = "/commons/"
        assert request.url.path.startswith(prefix)
        key = request.url.path.removeprefix(prefix)
        payload = remote.get(key)
        return httpx.Response(404) if payload is None else httpx.Response(200, content=payload)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_r2_publication_is_immutable_first_idempotent_and_pointer_last(
    tmp_path: Path,
) -> None:
    _, inventory = _fixture(tmp_path)
    remote: dict[str, bytes] = {}
    writer = FakeWriter(remote)
    client = _client(remote)
    try:
        first = await publish_starter_release_to_r2(
            directory=tmp_path,
            inventory=inventory,
            bucket="opennosh-public-commons",
            origin_url="https://artifacts.example.test/commons",
            writer=writer,
            client=client,
        )
        second = await publish_starter_release_to_r2(
            directory=tmp_path,
            inventory=inventory,
            bucket="opennosh-public-commons",
            origin_url="https://artifacts.example.test/commons",
            writer=writer,
            client=client,
        )
    finally:
        await client.aclose()

    assert first.uploaded_immutable == 2
    assert first.reused_immutable == 0
    assert second.uploaded_immutable == 0
    assert second.reused_immutable == 2
    assert [key for _, key, _ in writer.calls] == [
        "records/v1/record.json",
        "releases/v1/release-0.56.0.0.json",
        "latest/v1.json",
        "latest/v1.json",
    ]
    assert writer.calls[-1][2] == "public, max-age=0, must-revalidate"
    assert all("immutable" in call[2] for call in writer.calls[:2])


@pytest.mark.asyncio
async def test_r2_publication_refuses_an_immutable_conflict(tmp_path: Path) -> None:
    _, inventory = _fixture(tmp_path)
    remote = {"records/v1/record.json": b"tampered"}
    writer = FakeWriter(remote)
    client = _client(remote)
    try:
        with pytest.raises(R2ImmutableConflictError, match="conflicts"):
            await publish_starter_release_to_r2(
                directory=tmp_path,
                inventory=inventory,
                bucket="opennosh-public-commons",
                origin_url="https://artifacts.example.test/commons",
                writer=writer,
                client=client,
            )
    finally:
        await client.aclose()
    assert writer.calls == []


@pytest.mark.asyncio
async def test_r2_publication_verifies_every_upload_before_advancing(tmp_path: Path) -> None:
    _, inventory = _fixture(tmp_path)
    remote: dict[str, bytes] = {}
    writer = FakeWriter(remote, persist=False)
    client = _client(remote)
    try:
        with pytest.raises(R2PublicationError, match="did not verify"):
            await publish_starter_release_to_r2(
                directory=tmp_path,
                inventory=inventory,
                bucket="opennosh-public-commons",
                origin_url="https://artifacts.example.test/commons",
                writer=writer,
                client=client,
            )
    finally:
        await client.aclose()
    assert [key for _, key, _ in writer.calls] == ["records/v1/record.json"]


@pytest.mark.asyncio
async def test_r2_publication_requires_a_safe_https_origin(tmp_path: Path) -> None:
    _, inventory = _fixture(tmp_path)
    with pytest.raises(ValueError, match="safe HTTPS"):
        await publish_starter_release_to_r2(
            directory=tmp_path,
            inventory=inventory,
            bucket="opennosh-public-commons",
            origin_url="http://artifacts.example.test",
            writer=FakeWriter({}),
        )


@pytest.mark.asyncio
async def test_r2_publication_reports_origin_transport_failures(tmp_path: Path) -> None:
    _, inventory = _fixture(tmp_path)

    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(fail))
    try:
        with pytest.raises(R2PublicationError, match="request failed"):
            await publish_starter_release_to_r2(
                directory=tmp_path,
                inventory=inventory,
                bucket="opennosh-public-commons",
                origin_url="https://artifacts.example.test/commons",
                writer=FakeWriter({}),
                client=client,
            )
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_wrangler_writer_uses_the_reviewed_executable_and_safe_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "wrangler"
    executable.write_text("reviewed", encoding="utf-8")
    source = tmp_path / "object.json"
    source.write_bytes(b"{}")
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(arguments: list[str], **options: object) -> SimpleNamespace:
        calls.append((arguments, options))
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    writer = WranglerR2ObjectWriter(executable)

    await writer.put(
        bucket="opennosh-public-commons",
        object_key="records/v1/object.json",
        source=source,
        media_type="application/json",
        cache_control="public, max-age=31536000, immutable",
    )

    assert calls == [
        (
            [
                str(executable),
                "r2",
                "object",
                "put",
                "opennosh-public-commons/records/v1/object.json",
                "--file",
                str(source),
                "--content-type",
                "application/json",
                "--cache-control",
                "public, max-age=31536000, immutable",
                "--remote",
            ],
            {"check": False, "capture_output": True, "text": True},
        )
    ]


@pytest.mark.asyncio
async def test_wrangler_writer_reports_upload_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "wrangler"
    executable.write_text("reviewed", encoding="utf-8")
    source = tmp_path / "object.json"
    source.write_bytes(b"{}")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_options: SimpleNamespace(returncode=1, stderr="R2 denied\n"),
    )

    with pytest.raises(R2PublicationError, match="R2 denied"):
        await WranglerR2ObjectWriter(executable).put(
            bucket="opennosh-public-commons",
            object_key="records/v1/object.json",
            source=source,
            media_type="application/json",
            cache_control="immutable",
        )


@pytest.mark.asyncio
async def test_r2_publication_rejects_invalid_bucket_and_local_tamper(tmp_path: Path) -> None:
    payloads, inventory = _fixture(tmp_path)

    with pytest.raises(ValueError, match="bucket name"):
        await publish_starter_release_to_r2(
            directory=tmp_path,
            inventory=inventory,
            bucket="invalid/bucket",
            origin_url="https://artifacts.example.test/commons",
            writer=FakeWriter({}),
        )

    (tmp_path / "records/v1/record.json").write_bytes(payloads["records/v1/record.json"] + b"x")
    client = _client({})
    try:
        with pytest.raises(ValueError, match="size mismatch"):
            await publish_starter_release_to_r2(
                directory=tmp_path,
                inventory=inventory,
                bucket="opennosh-public-commons",
                origin_url="https://artifacts.example.test/commons",
                writer=FakeWriter({}),
                client=client,
            )
    finally:
        await client.aclose()


def test_wrangler_writer_requires_an_existing_executable(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="does not exist"):
        WranglerR2ObjectWriter(tmp_path / "missing-wrangler")


@pytest.mark.asyncio
async def test_r2_publication_requires_exactly_one_latest_pointer(tmp_path: Path) -> None:
    _, inventory = _fixture(tmp_path)
    without_pointer = inventory.model_copy(
        update={"objects": tuple(item for item in inventory.objects if not item.mutable_pointer)}
    )
    client = _client({})
    try:
        with pytest.raises(ValueError, match="exactly one latest pointer"):
            await publish_starter_release_to_r2(
                directory=tmp_path,
                inventory=without_pointer,
                bucket="opennosh-public-commons",
                origin_url="https://artifacts.example.test/commons",
                writer=FakeWriter({}),
                client=client,
            )
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_r2_publication_rejects_same_size_local_digest_tamper(tmp_path: Path) -> None:
    payloads, inventory = _fixture(tmp_path)
    original = payloads["records/v1/record.json"]
    (tmp_path / "records/v1/record.json").write_bytes(b"X" + original[1:])
    client = _client({})
    try:
        with pytest.raises(ValueError, match="digest mismatch"):
            await publish_starter_release_to_r2(
                directory=tmp_path,
                inventory=inventory,
                bucket="opennosh-public-commons",
                origin_url="https://artifacts.example.test/commons",
                writer=FakeWriter({}),
                client=client,
            )
    finally:
        await client.aclose()


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.put_calls: list[dict[str, object]] = []

    def put_object(self, **arguments: object) -> None:
        self.put_calls.append(arguments)
        bucket = str(arguments["Bucket"])
        key = str(arguments["Key"])
        body = arguments["Body"]
        assert isinstance(body, bytes)
        self.objects[(bucket, key)] = body

    def get_object(self, **arguments: object) -> dict[str, object]:
        payload = self.objects[(str(arguments["Bucket"]), str(arguments["Key"]))]
        return {
            "ContentLength": len(payload),
            "ETag": f'"{hashlib.md5(payload, usedforsecurity=False).hexdigest()}"',
            "Body": BytesIO(payload),
        }


@pytest.mark.asyncio
async def test_s3_r2_writer_uses_bounded_bytes_and_reviewed_object_metadata() -> None:
    client = FakeS3Client()
    writer = S3R2ObjectWriter(
        account_id="a" * 32,
        access_key_id="access-key",
        secret_access_key="secret-key",
        client=client,
    )
    payload = b'{"latest":1}'

    await writer.put_bytes(
        bucket="opennosh-public-commons",
        object_key="latest/v1.json",
        payload=payload,
        media_type="application/vnd.opennosh.latest+json",
        cache_control="public, max-age=0, must-revalidate",
    )
    readback, etag = await writer.read_revision(
        bucket="opennosh-public-commons",
        object_key="latest/v1.json",
        max_bytes=1024,
    )

    assert readback == payload
    assert etag == f'"{hashlib.md5(payload, usedforsecurity=False).hexdigest()}"'
    assert client.put_calls == [
        {
            "Bucket": "opennosh-public-commons",
            "Key": "latest/v1.json",
            "Body": payload,
            "ContentType": "application/vnd.opennosh.latest+json",
            "CacheControl": "public, max-age=0, must-revalidate",
        }
    ]


def test_s3_r2_writer_bounds_network_time_within_render_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def capture_client(*_arguments: object, **arguments: object) -> FakeS3Client:
        captured.update(arguments)
        return FakeS3Client()

    monkeypatch.setattr("opennosh_api.public.r2.boto3.client", capture_client)
    S3R2ObjectWriter(
        account_id="a" * 32,
        access_key_id="access-key",
        secret_access_key="secret-key",
    )

    config = cast(Any, captured["config"])
    assert config.connect_timeout == 1
    assert config.read_timeout == 1.5
    assert config.retries["total_max_attempts"] == 1
    worst_case_network_seconds = (
        MAX_R2_OPERATIONS_PER_REFRESH
        * (config.connect_timeout + config.read_timeout)
        + 2
    )
    assert worst_case_network_seconds == 19.5
    assert worst_case_network_seconds < 30


@pytest.mark.asyncio
async def test_s3_r2_writer_enforces_absolute_put_deadline() -> None:
    release = threading.Event()

    class StalledPutClient(FakeS3Client):
        def put_object(self, **arguments: object) -> None:
            del arguments
            release.wait()

    writer = S3R2ObjectWriter(
        account_id="a" * 32,
        access_key_id="access-key",
        secret_access_key="secret-key",
        client=StalledPutClient(),
        operation_timeout_seconds=0.01,
    )
    started = time.monotonic()
    try:
        with pytest.raises(R2PublicationError, match="absolute deadline"):
            await writer.put_bytes(
                bucket="opennosh-public-commons",
                object_key="latest/v1.json",
                payload=b"pointer",
                media_type="application/json",
                cache_control="must-revalidate",
            )
    finally:
        release.set()
    assert time.monotonic() - started < 0.5


@pytest.mark.asyncio
async def test_s3_r2_writer_enforces_one_deadline_across_headers_and_body() -> None:
    release = threading.Event()

    class StalledBody:
        def read(self, _size: int) -> bytes:
            release.wait()
            return b"pointer"

        def close(self) -> None:
            return None

    class StalledBodyClient(FakeS3Client):
        def get_object(self, **arguments: object) -> dict[str, object]:
            del arguments
            return {"ContentLength": 7, "ETag": '"revision"', "Body": StalledBody()}

    writer = S3R2ObjectWriter(
        account_id="a" * 32,
        access_key_id="access-key",
        secret_access_key="secret-key",
        client=StalledBodyClient(),
        operation_timeout_seconds=0.01,
    )
    started = time.monotonic()
    try:
        with pytest.raises(R2PublicationError, match="absolute deadline"):
            await writer.read_revision(
                bucket="opennosh-public-commons",
                object_key="latest/v1.json",
                max_bytes=1024,
            )
    finally:
        release.set()
    assert time.monotonic() - started < 0.5


def test_s3_r2_writer_rejects_invalid_identity_without_constructing_a_client() -> None:
    with pytest.raises(ValueError, match="account ID"):
        S3R2ObjectWriter(
            account_id="not-an-account",
            access_key_id="access-key",
            secret_access_key="secret-key",
        )


@pytest.mark.asyncio
async def test_s3_r2_writer_sends_the_current_revision_as_if_match() -> None:
    client = FakeS3Client()
    current = b"current"
    client.objects[("opennosh-public-commons", "latest/v1.json")] = current
    writer = S3R2ObjectWriter(
        account_id="a" * 32,
        access_key_id="access-key",
        secret_access_key="secret-key",
        client=client,
    )
    _, etag = await writer.read_revision(
        bucket="opennosh-public-commons",
        object_key="latest/v1.json",
        max_bytes=1024,
    )

    await writer.put_bytes(
        bucket="opennosh-public-commons",
        object_key="latest/v1.json",
        payload=b"replacement",
        media_type="application/vnd.opennosh.latest+json",
        cache_control="public, max-age=0, must-revalidate",
        if_match=etag,
    )

    assert client.put_calls[-1]["IfMatch"] == etag


@pytest.mark.asyncio
async def test_s3_r2_writer_enforces_read_bounds() -> None:
    client = FakeS3Client()
    client.objects[("opennosh-public-commons", "latest/v1.json")] = b"oversized"
    writer = S3R2ObjectWriter(
        account_id="a" * 32,
        access_key_id="access-key",
        secret_access_key="secret-key",
        client=client,
    )

    with pytest.raises(R2PublicationError, match="bounded read size"):
        await writer.read_bytes(
            bucket="opennosh-public-commons",
            object_key="latest/v1.json",
            max_bytes=4,
        )
