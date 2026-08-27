from __future__ import annotations

import asyncio
import hashlib
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from opennosh_api.public.bootstrap import StarterReleaseInventory, StarterReleaseObject
from opennosh_api.public.r2 import (
    R2ImmutableConflictError,
    R2PublicationError,
    WranglerR2ObjectWriter,
    publish_starter_release_to_r2,
)


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
