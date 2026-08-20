from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import pytest
from opennosh_api.exports.streaming import (
    ExportByteLimitError,
    ExportCapacityError,
    ExportStreamingResponse,
    JsonSection,
    acquire_export_capacity,
    spool_json_stream,
    stream_json_sections,
)
from pydantic import BaseModel
from starlette.requests import ClientDisconnect


class Envelope(BaseModel):
    schema_version: str = "1.0.0"
    entries: list[dict[str, int]]


class Entry(BaseModel):
    sequence: int


async def _collect_chunks() -> list[bytes]:
    async def rows() -> AsyncIterator[BaseModel]:
        for sequence in range(3):
            yield Entry(sequence=sequence)

    return [
        chunk
        async for chunk in stream_json_sections(
            Envelope(entries=[]), [JsonSection("entries", rows)]
        )
    ]


def test_json_export_streams_rows_as_independent_chunks() -> None:
    chunks = asyncio.run(_collect_chunks())

    assert len(chunks) > 3
    assert json.loads(b"".join(chunks)) == {
        "schema_version": "1.0.0",
        "entries": [{"sequence": 0}, {"sequence": 1}, {"sequence": 2}],
    }
    assert all(len(chunk) < 100 for chunk in chunks)


def test_spooled_export_enforces_exact_bytes_before_returning_a_body() -> None:
    closed = False

    async def oversized() -> AsyncIterator[bytes]:
        nonlocal closed
        try:
            yield b"1234"
            yield b"5678"
        finally:
            closed = True

    async def exercise() -> None:
        with pytest.raises(ExportByteLimitError):
            await spool_json_stream(oversized(), max_bytes=7)

    asyncio.run(exercise())
    assert closed is True


def test_spooled_export_rolls_to_bounded_read_chunks() -> None:
    payload = b"x" * (1024 * 1024 + 1)

    async def source() -> AsyncIterator[bytes]:
        yield payload

    async def exercise() -> tuple[bytes, int]:
        body = await spool_json_stream(source())
        chunks = [chunk async for chunk in body]
        return b"".join(chunks), max(map(len, chunks))

    result, largest_chunk = asyncio.run(exercise())
    assert result == payload
    assert largest_chunk <= 64 * 1024


def test_export_response_closes_body_and_releases_capacity_on_disconnect() -> None:
    closed = False

    async def body() -> AsyncIterator[bytes]:
        nonlocal closed
        try:
            yield b"payload"
        finally:
            closed = True

    async def receive() -> dict[str, str]:
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        if message["type"] == "http.response.body":
            raise OSError("client disconnected")

    async def exercise() -> None:
        lease = asyncio.Semaphore(1)
        await lease.acquire()
        response = ExportStreamingResponse(
            body(),
            lease=lease,
            timeout_seconds=1,
            media_type="application/json",
        )
        with pytest.raises(ClientDisconnect):
            await response(
                {"type": "http", "asgi": {"spec_version": "2.4"}},
                receive,
                send,
            )
        await asyncio.wait_for(lease.acquire(), timeout=0.1)

    asyncio.run(exercise())
    assert closed is True


def test_export_response_deadline_closes_body_and_releases_capacity() -> None:
    closed = False

    async def body() -> AsyncIterator[bytes]:
        nonlocal closed
        try:
            await asyncio.Event().wait()
            yield b"unreachable"
        finally:
            closed = True

    async def send(_message: dict[str, object]) -> None:
        return None

    async def exercise() -> None:
        lease = asyncio.Semaphore(1)
        await lease.acquire()
        response = ExportStreamingResponse(
            body(),
            lease=lease,
            timeout_seconds=0.01,
            media_type="application/json",
        )
        with pytest.raises(TimeoutError):
            await response.stream_response(send)  # type: ignore[arg-type]
        await asyncio.wait_for(lease.acquire(), timeout=0.1)

    asyncio.run(exercise())
    assert closed is True


def test_export_capacity_wait_is_bounded() -> None:
    async def exercise() -> None:
        with pytest.raises(ExportCapacityError):
            await acquire_export_capacity(
                asyncio.Semaphore(0), wait_seconds=0.01
            )

    asyncio.run(exercise())


def test_saturated_public_capacity_does_not_consume_private_capacity() -> None:
    async def exercise() -> None:
        public = asyncio.Semaphore(2)
        private = asyncio.Semaphore(1)
        await acquire_export_capacity(public, wait_seconds=0.01)
        await acquire_export_capacity(public, wait_seconds=0.01)

        await acquire_export_capacity(private, wait_seconds=0.01)
        with pytest.raises(ExportCapacityError):
            await acquire_export_capacity(public, wait_seconds=0.01)

        private.release()
        public.release()
        public.release()

    asyncio.run(exercise())
