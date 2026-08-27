"""Warm and verify a deployed public artifact API before feature activation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from opennosh_api.public.artifacts import PublicReadReleaseManifest
from opennosh_api.public.bootstrap import StarterReleaseInventory
from opennosh_api.public_commons.manifests import SignedEnvelope


class LiveReleaseVerificationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LiveReleaseVerificationResult:
    release_version: str
    foods_warmed: int
    provenance_warmed: int
    packs_warmed: int
    latest_checkpoint_advanced: bool


async def warm_and_verify_public_api(
    *,
    directory: Path,
    inventory: StarterReleaseInventory,
    api_origin: str,
    client: httpx.AsyncClient | None = None,
    concurrency: int = 8,
) -> LiveReleaseVerificationResult:
    """Request every signed artifact through the deployed API and advance its checkpoint."""

    if not 1 <= concurrency <= 16:
        raise ValueError("Live release verification concurrency must be between 1 and 16")
    origin = _safe_api_origin(api_origin)
    manifest_path = (directory / f"releases/v1/release-{inventory.release_version}.json").resolve(
        strict=True
    )
    envelope = SignedEnvelope.model_validate_json(await asyncio.to_thread(manifest_path.read_bytes))
    manifest = PublicReadReleaseManifest.model_validate(envelope.payload)
    if manifest.release_version != inventory.release_version:
        raise ValueError("Live verification manifest does not match the inventory")
    if not manifest.foods:
        raise LiveReleaseVerificationError("Live verification manifest contains no foods")

    owned_client = client is None
    http_client = client or httpx.AsyncClient(timeout=15.0, follow_redirects=False)
    semaphore = asyncio.Semaphore(concurrency)

    async def require(path: str, *, expected_media_type: str | None = None) -> None:
        async with semaphore:
            try:
                response = await http_client.get(f"{origin}{path}")
            except httpx.HTTPError as error:
                raise LiveReleaseVerificationError(
                    f"Live artifact route request failed: {path}"
                ) from error
        if response.status_code != 200:
            raise LiveReleaseVerificationError(
                f"Live artifact route returned {response.status_code}: {path}"
            )
        if response.headers.get("x-opennosh-release-version") != inventory.release_version:
            raise LiveReleaseVerificationError(
                f"Live artifact route returned the wrong release: {path}"
            )
        if response.headers.get("x-opennosh-release-state") != "verified":
            raise LiveReleaseVerificationError(
                f"Live artifact route is not freshly verified: {path}"
            )
        if expected_media_type is not None:
            media_type = response.headers.get("content-type", "").partition(";")[0]
            if media_type != expected_media_type:
                raise LiveReleaseVerificationError(
                    f"Live artifact route returned {media_type or 'no content type'}: {path}"
                )

    try:
        await require(
            f"/api/v1/public/releases/{inventory.release_version}/manifest",
            expected_media_type="application/vnd.opennosh.release+json",
        )
        food_tasks = []
        for food in manifest.foods:
            base = (
                f"/api/v1/public/releases/{inventory.release_version}/foods/"
                f"{quote(food.source.value, safe='')}/{quote(food.source_id, safe='')}"
            )
            food_tasks.append(require(base, expected_media_type="application/json"))
            food_tasks.append(require(f"{base}/provenance", expected_media_type="text/html"))
        await asyncio.gather(*food_tasks)
        pack_tasks = [
            require(
                (
                    f"/api/v1/public/releases/{inventory.release_version}/packs/"
                    f"{quote(pack.pack_id, safe='')}/{quote(pack.pack_version, safe='')}/download"
                ),
                expected_media_type=pack.download.media_type,
            )
            for pack in manifest.packs
        ]
        await asyncio.gather(*pack_tasks)
        first_food = manifest.foods[0]
        await require(
            (
                "/api/v1/public/foods/"
                f"{quote(first_food.source.value, safe='')}/"
                f"{quote(first_food.source_id, safe='')}"
            ),
            expected_media_type="application/json",
        )
    finally:
        if owned_client:
            await http_client.aclose()

    return LiveReleaseVerificationResult(
        release_version=inventory.release_version,
        foods_warmed=len(manifest.foods),
        provenance_warmed=len(manifest.foods),
        packs_warmed=len(manifest.packs),
        latest_checkpoint_advanced=True,
    )


def _safe_api_origin(value: str) -> str:
    normalized = value.rstrip("/")
    parsed = urlsplit(normalized)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("API origin must be a safe HTTPS origin without a path")
    return normalized
