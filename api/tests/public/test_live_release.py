from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from opennosh_api.foods.schemas import FoodSource
from opennosh_api.public.artifacts import (
    PublicFoodArtifact,
    PublicPackArtifact,
    PublicReadReleaseManifest,
    artifact_descriptor,
)
from opennosh_api.public.bootstrap import StarterReleaseInventory, StarterReleaseObject
from opennosh_api.public.live import (
    LiveReleaseVerificationError,
    warm_and_verify_public_api,
)
from opennosh_api.public_commons.manifests import SignedEnvelope, canonical_json

RELEASE = "0.56.0.0"
PUBLISHED_AT = datetime(2026, 8, 27, 2, tzinfo=UTC)


def _fixture(tmp_path: Path):
    record_bytes = b'{"record":1}'
    provenance_bytes = b"<p>proof</p>"
    pack_bytes = b"PK\x03\x04pack"
    record = artifact_descriptor(
        f"records/v1/{hashlib.sha256(record_bytes).hexdigest()}.json",
        record_bytes,
        "application/json",
    )
    provenance = artifact_descriptor(
        f"provenance/v1/{hashlib.sha256(provenance_bytes).hexdigest()}.html",
        provenance_bytes,
        "text/html",
    )
    pack = artifact_descriptor(
        f"packs/v1/{hashlib.sha256(pack_bytes).hexdigest()}.zip",
        pack_bytes,
        "application/zip",
    )
    manifest = PublicReadReleaseManifest(
        release_version=RELEASE,
        published_at=PUBLISHED_AT,
        publication_receipt_key="receipts/v1/11111111-1111-4111-8111-111111111111.json",
        foods=(
            PublicFoodArtifact(
                source=FoodSource.COMMUNITY,
                source_id="rajma-masala",
                record=record,
                provenance=provenance,
            ),
        ),
        packs=(
            PublicPackArtifact(
                pack_id="north-india-home-foods",
                pack_version="2.4.0",
                download=pack,
            ),
        ),
    )
    envelope = SignedEnvelope(
        key_id="production",
        payload=manifest.model_dump(mode="json"),
        signature="A" * 86,
    )
    manifest_bytes = canonical_json(envelope.model_dump(mode="json"))
    manifest_path = tmp_path / f"releases/v1/release-{RELEASE}.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_bytes(manifest_bytes)
    inventory = StarterReleaseInventory(
        release_version=RELEASE,
        published_at=PUBLISHED_AT,
        source_commit="a" * 40,
        source_inventory_digest="b" * 64,
        manifest_key_id="production",
        manifest_verifying_key="c" * 43,
        receipt_key_id="production-receipt",
        receipt_verifying_key="d" * 43,
        food_count=1,
        pack_count=1,
        total_bytes=len(manifest_bytes),
        objects=(
            StarterReleaseObject(
                object_key=f"releases/v1/release-{RELEASE}.json",
                digest="e" * 64,
                size_bytes=len(manifest_bytes),
                media_type="application/vnd.opennosh.release+json",
            ),
            StarterReleaseObject(
                object_key="latest/v1.json",
                digest="f" * 64,
                size_bytes=1,
                media_type="application/vnd.opennosh.latest+json",
                mutable_pointer=True,
            ),
        ),
    )
    return inventory


@pytest.mark.asyncio
async def test_live_verifier_warms_every_route_and_advances_latest(tmp_path: Path) -> None:
    inventory = _fixture(tmp_path)
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        media_type = (
            "application/vnd.opennosh.release+json"
            if request.url.path.endswith("/manifest")
            else "text/html"
            if request.url.path.endswith("/provenance")
            else "application/zip"
            if request.url.path.endswith("/download")
            else "application/json"
        )
        return httpx.Response(
            200,
            headers={
                "content-type": media_type,
                "x-opennosh-release-version": RELEASE,
                "x-opennosh-release-state": "verified",
            },
            content=b"verified",
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await warm_and_verify_public_api(
            directory=tmp_path,
            inventory=inventory,
            api_origin="https://opennosh.org",
            client=client,
        )
    finally:
        await client.aclose()

    assert result.foods_warmed == 1
    assert result.provenance_warmed == 1
    assert result.packs_warmed == 1
    assert result.latest_checkpoint_advanced is True
    assert len(paths) == 5
    assert paths[-1] == "/api/v1/public/foods/community/rajma-masala"


@pytest.mark.asyncio
async def test_live_verifier_rejects_wrong_release_and_stale_state(tmp_path: Path) -> None:
    inventory = _fixture(tmp_path)
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                headers={
                    "content-type": "application/vnd.opennosh.release+json",
                    "x-opennosh-release-version": "0.55.0.0",
                    "x-opennosh-release-state": "verified",
                },
            )
        )
    )
    try:
        with pytest.raises(LiveReleaseVerificationError, match="wrong release"):
            await warm_and_verify_public_api(
                directory=tmp_path,
                inventory=inventory,
                api_origin="https://opennosh.org",
                client=client,
            )
    finally:
        await client.aclose()

    stale_client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                headers={
                    "content-type": "application/vnd.opennosh.release+json",
                    "x-opennosh-release-version": RELEASE,
                    "x-opennosh-release-state": "stale",
                },
            )
        )
    )
    try:
        with pytest.raises(LiveReleaseVerificationError, match="freshly verified"):
            await warm_and_verify_public_api(
                directory=tmp_path,
                inventory=inventory,
                api_origin="https://opennosh.org",
                client=stale_client,
            )
    finally:
        await stale_client.aclose()


@pytest.mark.asyncio
async def test_live_verifier_requires_a_bare_https_origin(tmp_path: Path) -> None:
    inventory = _fixture(tmp_path)
    with pytest.raises(ValueError, match="safe HTTPS"):
        await warm_and_verify_public_api(
            directory=tmp_path,
            inventory=inventory,
            api_origin="https://opennosh.org/private/path",
        )


@pytest.mark.asyncio
async def test_live_verifier_reports_transport_failures(tmp_path: Path) -> None:
    inventory = _fixture(tmp_path)

    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(fail))
    try:
        with pytest.raises(LiveReleaseVerificationError, match="request failed"):
            await warm_and_verify_public_api(
                directory=tmp_path,
                inventory=inventory,
                api_origin="https://opennosh.org",
                client=client,
            )
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_live_verifier_rejects_invalid_concurrency(tmp_path: Path) -> None:
    inventory = _fixture(tmp_path)

    with pytest.raises(ValueError, match="between 1 and 16"):
        await warm_and_verify_public_api(
            directory=tmp_path,
            inventory=inventory,
            api_origin="https://opennosh.org",
            concurrency=17,
        )


@pytest.mark.asyncio
async def test_live_verifier_rejects_non_200_and_wrong_media_type(tmp_path: Path) -> None:
    inventory = _fixture(tmp_path)

    for response, message in (
        (httpx.Response(503), "returned 503"),
        (
            httpx.Response(
                200,
                headers={
                    "content-type": "text/plain",
                    "x-opennosh-release-version": RELEASE,
                    "x-opennosh-release-state": "verified",
                },
            ),
            "returned text/plain",
        ),
    ):
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _request, response=response: response)
        )
        try:
            with pytest.raises(LiveReleaseVerificationError, match=message):
                await warm_and_verify_public_api(
                    directory=tmp_path,
                    inventory=inventory,
                    api_origin="https://opennosh.org",
                    client=client,
                )
        finally:
            await client.aclose()


@pytest.mark.asyncio
async def test_live_verifier_rejects_an_empty_food_manifest(tmp_path: Path) -> None:
    inventory = _fixture(tmp_path)
    manifest_path = tmp_path / f"releases/v1/release-{RELEASE}.json"
    envelope = SignedEnvelope.model_validate_json(manifest_path.read_bytes())
    payload = dict(envelope.payload)
    payload["foods"] = []
    manifest_path.write_bytes(
        canonical_json(
            SignedEnvelope(
                key_id=envelope.key_id,
                payload=payload,
                signature=envelope.signature,
            ).model_dump(mode="json")
        )
    )

    with pytest.raises(LiveReleaseVerificationError, match="contains no foods"):
        await warm_and_verify_public_api(
            directory=tmp_path,
            inventory=inventory,
            api_origin="https://opennosh.org",
        )
