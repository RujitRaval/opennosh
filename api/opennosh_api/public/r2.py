"""Conflict-safe, immutable-first publication of a verified release to Cloudflare R2."""

from __future__ import annotations

import asyncio
import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import quote, urlsplit

import httpx

from opennosh_api.public.bootstrap import StarterReleaseInventory, StarterReleaseObject


class R2PublicationError(RuntimeError):
    pass


class R2ImmutableConflictError(R2PublicationError):
    pass


class R2ObjectWriter(Protocol):
    async def put(
        self,
        *,
        bucket: str,
        object_key: str,
        source: Path,
        media_type: str,
        cache_control: str,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class R2PublicationResult:
    release_version: str
    uploaded_immutable: int
    reused_immutable: int
    pointer_replaced: bool


class WranglerR2ObjectWriter:
    """Use an already-installed, operator-approved Wrangler executable."""

    def __init__(self, executable: Path) -> None:
        if not executable.is_file():
            raise FileNotFoundError(f"Wrangler executable does not exist: {executable}")
        self._executable = executable

    async def put(
        self,
        *,
        bucket: str,
        object_key: str,
        source: Path,
        media_type: str,
        cache_control: str,
    ) -> None:
        completed = await asyncio.to_thread(
            subprocess.run,
            [
                str(self._executable),
                "r2",
                "object",
                "put",
                f"{bucket}/{object_key}",
                "--file",
                str(source),
                "--content-type",
                media_type,
                "--cache-control",
                cache_control,
                "--remote",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            message = completed.stderr.strip() or "Wrangler R2 upload failed"
            raise R2PublicationError(message)


async def publish_starter_release_to_r2(
    *,
    directory: Path,
    inventory: StarterReleaseInventory,
    bucket: str,
    origin_url: str,
    writer: R2ObjectWriter,
    client: httpx.AsyncClient | None = None,
) -> R2PublicationResult:
    """Upload immutable objects idempotently and replace latest only after verification."""

    normalized_origin = _safe_origin(origin_url)
    if not bucket or "/" in bucket or any(character.isspace() for character in bucket):
        raise ValueError("R2 bucket name is invalid")
    root = await asyncio.to_thread(directory.resolve, strict=True)
    owned_client = client is None
    http_client = client or httpx.AsyncClient(timeout=10.0, follow_redirects=False)
    uploaded = 0
    reused = 0
    try:
        immutable = tuple(item for item in inventory.objects if not item.mutable_pointer)
        pointer = tuple(item for item in inventory.objects if item.mutable_pointer)
        if len(pointer) != 1 or pointer[0].object_key != "latest/v1.json":
            raise ValueError("Release inventory must contain exactly one latest pointer")
        for item in immutable:
            source = await asyncio.to_thread(_verified_local_object, root, item)
            state = await _remote_state(http_client, normalized_origin, item)
            if state == "match":
                reused += 1
                continue
            if state == "conflict":
                raise R2ImmutableConflictError(
                    f"Immutable R2 object conflicts with release inventory: {item.object_key}"
                )
            await writer.put(
                bucket=bucket,
                object_key=item.object_key,
                source=source,
                media_type=item.media_type,
                cache_control="public, max-age=31536000, immutable",
            )
            await _require_remote_match(http_client, normalized_origin, item)
            uploaded += 1

        pointer_item = pointer[0]
        pointer_source = await asyncio.to_thread(_verified_local_object, root, pointer_item)
        await writer.put(
            bucket=bucket,
            object_key=pointer_item.object_key,
            source=pointer_source,
            media_type=pointer_item.media_type,
            cache_control="public, max-age=0, must-revalidate",
        )
        await _require_remote_match(http_client, normalized_origin, pointer_item)
    finally:
        if owned_client:
            await http_client.aclose()
    return R2PublicationResult(
        release_version=inventory.release_version,
        uploaded_immutable=uploaded,
        reused_immutable=reused,
        pointer_replaced=True,
    )


def _verified_local_object(root: Path, item: StarterReleaseObject) -> Path:
    path = (root / item.object_key).resolve(strict=True)
    if not path.is_relative_to(root):
        raise ValueError("Release inventory object escapes its directory")
    if path.stat().st_size != item.size_bytes:
        raise ValueError(f"Local release object size mismatch: {item.object_key}")
    payload = path.read_bytes()
    if len(payload) != item.size_bytes:
        raise ValueError(f"Local release object size mismatch: {item.object_key}")
    if hashlib.sha256(payload).hexdigest() != item.digest:
        raise ValueError(f"Local release object digest mismatch: {item.object_key}")
    return path


async def _remote_state(
    client: httpx.AsyncClient,
    origin: str,
    item: StarterReleaseObject,
) -> str:
    try:
        async with client.stream(
            "GET",
            _object_url(origin, item.object_key),
            headers={"Accept-Encoding": "identity"},
        ) as response:
            if response.status_code == 404:
                return "missing"
            if response.status_code != 200:
                raise R2PublicationError(
                    f"R2 origin returned {response.status_code} for {item.object_key}"
                )
            declared = response.headers.get("content-length")
            if declared is not None and int(declared) != item.size_bytes:
                return "conflict"
            digest = hashlib.sha256()
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > item.size_bytes:
                    return "conflict"
                digest.update(chunk)
            return (
                "match"
                if size == item.size_bytes and digest.hexdigest() == item.digest
                else "conflict"
            )
    except (httpx.HTTPError, ValueError) as error:
        raise R2PublicationError(
            f"R2 origin request failed for {item.object_key}"
        ) from error


async def _require_remote_match(
    client: httpx.AsyncClient,
    origin: str,
    item: StarterReleaseObject,
) -> None:
    if await _remote_state(client, origin, item) != "match":
        raise R2PublicationError(f"R2 upload did not verify: {item.object_key}")


def _safe_origin(value: str) -> str:
    normalized = value.rstrip("/")
    parsed = urlsplit(normalized)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("R2 origin must be a safe HTTPS URL")
    return normalized


def _object_url(origin: str, object_key: str) -> str:
    return f"{origin}/{'/'.join(quote(part, safe='') for part in object_key.split('/'))}"
