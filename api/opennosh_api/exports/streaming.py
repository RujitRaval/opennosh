from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from tempfile import SpooledTemporaryFile
from typing import Any

from pydantic import BaseModel
from starlette.responses import StreamingResponse
from starlette.types import Send


@dataclass(frozen=True, slots=True)
class JsonSection:
    name: str
    rows: Callable[[], AsyncIterator[BaseModel]]


class ExportByteLimitError(RuntimeError):
    """A serialized export exceeded its exact response-byte ceiling."""


class ExportCapacityError(RuntimeError):
    """All bounded export spool slots are currently occupied."""


async def acquire_export_capacity(
    semaphore: asyncio.Semaphore, *, wait_seconds: float
) -> None:
    try:
        async with asyncio.timeout(wait_seconds):
            await semaphore.acquire()
    except TimeoutError as error:
        raise ExportCapacityError from error


class ExportStreamingResponse(StreamingResponse):
    """Streaming response that always closes its spool and releases capacity."""

    def __init__(
        self,
        content: AsyncIterator[bytes],
        *,
        lease: asyncio.Semaphore,
        timeout_seconds: float,
        **kwargs: Any,
    ) -> None:
        self._export_lease = lease
        self._export_timeout_seconds = timeout_seconds
        super().__init__(content, **kwargs)

    async def stream_response(self, send: Send) -> None:
        try:
            async with asyncio.timeout(self._export_timeout_seconds):
                await super().stream_response(send)
        finally:
            try:
                close = getattr(self.body_iterator, "aclose", None)
                if close is not None:
                    await close()
            finally:
                self._export_lease.release()


async def stream_json_sections(
    envelope: BaseModel, sections: Sequence[JsonSection]
) -> AsyncIterator[bytes]:
    """Stream one valid JSON object while keeping only one exported row in memory."""
    section_names = {section.name for section in sections}
    prefix = envelope.model_dump_json(exclude=section_names).encode("utf-8")
    if not prefix.endswith(b"}"):  # pragma: no cover - Pydantic guarantees an object
        raise RuntimeError("Export envelope did not serialize as a JSON object")
    yield prefix[:-1]
    for section in sections:
        yield b',"' + section.name.encode("ascii") + b'":['
        first = True
        rows = section.rows()
        try:
            async for row in rows:
                if not first:
                    yield b","
                yield row.model_dump_json().encode("utf-8")
                first = False
        finally:
            close = getattr(rows, "aclose", None)
            if close is not None:
                await close()
        yield b"]"
    yield b"}"


async def spool_json_stream(
    body: AsyncIterator[bytes], *, max_bytes: int | None = None
) -> AsyncIterator[bytes]:
    """Validate a JSON stream before headers, then serve it without retaining DB state."""
    spool = SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b")
    pending = bytearray()
    total = 0
    try:
        try:
            async for chunk in body:
                total += len(chunk)
                if max_bytes is not None and total > max_bytes:
                    raise ExportByteLimitError
                pending.extend(chunk)
                if len(pending) >= 64 * 1024:
                    await asyncio.to_thread(spool.write, bytes(pending))
                    pending.clear()
        finally:
            close = getattr(body, "aclose", None)
            if close is not None:
                await close()
        if pending:
            await asyncio.to_thread(spool.write, bytes(pending))
        await asyncio.to_thread(spool.seek, 0)
    except BaseException:
        await asyncio.to_thread(spool.close)
        raise

    async def read_chunks() -> AsyncIterator[bytes]:
        try:
            while chunk := await asyncio.to_thread(spool.read, 64 * 1024):
                yield chunk
        finally:
            await asyncio.to_thread(spool.close)

    return read_chunks()
