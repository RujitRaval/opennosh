from __future__ import annotations

import asyncio
import gzip
import json
import time
from pathlib import Path

import httpx
import pytest
from opennosh_api.foods.schemas import FoodCapabilities, FoodSearchResponse, FoodSearchResponseV1
from opennosh_api.public.artifacts import PublicFoodRecordResponse
from opennosh_api.sdk import (
    PACKAGE_VERSION,
    AsyncOpenNoshClient,
    OpenNoshClient,
    OpenNoshProblem,
    normalize_target,
)

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = json.loads(
    (ROOT / "tests/fixtures/developer-compatibility.v1.json").read_text(encoding="utf-8")
)
RESPONSES = {item["operation_id"]: item for item in FIXTURES["responses"]}


def response_for(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/api/v1/foods/capabilities":
        operation = "capabilities_api_v1_foods_capabilities_get"
    elif path == "/api/v1/foods/search":
        operation = "search_api_v1_foods_search_get"
    elif path == "/api/v1/public/commons-snapshot":
        operation = "commons_snapshot_api_v1_public_commons_snapshot_get"
    elif path == "/api/v1/public/foods/community/rajma-masala":
        operation = "latest_food_api_v1_public_foods__source___source_id__get"
    elif path == "/api/v1/public/missions":
        operation = "missions_api_v1_public_missions_get"
    elif path == "/api/v1/public/missions/activity":
        operation = "mission_activity_api_v1_public_missions_activity_get"
    elif path.endswith("/provenance"):
        operation = (
            "exact_provenance_api_v1_public_releases__release_version__foods__source___source_id__"
            "provenance_get"
        )
    elif path.endswith("/manifest"):
        operation = "exact_manifest_api_v1_public_releases__release_version__manifest_get"
    elif path.endswith("/download"):
        operation = (
            "exact_pack_download_api_v1_public_releases__release_version__packs__pack_id___"
            "pack_version__download_get"
        )
    else:
        operation = (
            "exact_food_api_v1_public_releases__release_version__foods__source___source_id__get"
        )
    fixture = RESPONSES[operation]
    body = fixture["body"]
    content = json.dumps(body).encode() if isinstance(body, dict) else str(body).encode()
    return httpx.Response(
        200,
        content=content,
        headers={
            "content-type": fixture["media_type"],
            "etag": '"fixture"',
            "cache-control": "public, immutable",
            "x-opennosh-release-version": "0.82.1.0",
            "x-opennosh-release-state": "stale",
            "x-opennosh-stale-age": "3600",
            "warning": '110 - "Response is stale but remains cryptographically verified"',
        },
        request=request,
    )


def test_normalizes_only_safe_exact_origins() -> None:
    assert normalize_target() == "https://opennosh.org"
    assert normalize_target("https://EXAMPLE.test:443/") == "https://example.test"
    assert normalize_target("http://localhost:8000") == "http://localhost:8000"
    assert normalize_target("http://127.0.0.1") == "http://127.0.0.1"
    assert normalize_target("http://[::1]:8000") == "http://[::1]:8000"
    for target in (
        "example.test",
        "ftp://example.test",
        "http://example.test",
        "http://127.1",
        "https://user@example.test",
        "https://example.test/path",
        "https://example.test/?query=1",
        "https://example.test/#fragment",
        " https://example.test",
    ):
        with pytest.raises(TypeError):
            normalize_target(target)


def test_sync_client_maps_all_operations_without_credentials_or_retries() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response_for(request)

    client = OpenNoshClient("https://nosh.example", transport=httpx.MockTransport(handler))
    results = [
        client.capabilities(),
        client.search_foods("rajma", locale="en-US", packs=["north", "home"], limit=5),
        client.get_commons_snapshot(if_none_match='"old"'),
        client.get_public_food("community", "rajma-masala", version="2"),
        client.list_missions(limit=12),
        client.get_mission_activity(),
        client.get_release_food("0.82.1.0", "community", "rajma-masala"),
        client.get_provenance("0.82.1.0", "community", "rajma-masala"),
        client.get_release_manifest("0.82.1.0"),
        client.download_pack("0.82.1.0", "north-india-home-foods", "2.4.0"),
    ]

    assert len(requests) == 10
    assert isinstance(results[0].data, FoodCapabilities)
    assert isinstance(results[1].data, FoodSearchResponse)
    assert isinstance(results[3].data, PublicFoodRecordResponse)
    assert results[3].data.record.attribution.license == "CC0-1.0"
    assert results[7].data.startswith("<!doctype html>")
    assert results[9].data.startswith(b"PK")
    assert results[0].etag == '"fixture"'
    assert results[7].release_version == "0.82.1.0"
    assert results[7].release_state == "stale"
    assert results[7].stale_age_seconds == 3600
    assert results[7].warning is not None
    assert requests[1].url.params.get_list("pack") == ["north", "home"]
    assert requests[2].headers["if-none-match"] == '"old"'
    for request in requests:
        assert request.method == "GET"
        assert request.headers["x-opennosh-client"] == f"python/{PACKAGE_VERSION}"
        assert request.headers["accept-encoding"] == "identity"
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers


@pytest.mark.asyncio
async def test_async_client_uses_the_same_models_and_policy() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response_for(request)

    client = AsyncOpenNoshClient("http://localhost:8000", transport=httpx.MockTransport(handler))
    results = [
        await client.capabilities(timeout=1),
        await client.search_foods("rajma", locale="en-US", packs=["home"]),
        await client.get_commons_snapshot(if_none_match='"old"'),
        await client.get_public_food("community", "rajma-masala"),
        await client.list_missions(limit=12),
        await client.get_mission_activity(),
        await client.get_release_food("0.82.1.0", "community", "rajma-masala"),
        await client.get_provenance("0.82.1.0", "community", "rajma-masala"),
        await client.get_release_manifest("0.82.1.0"),
        await client.download_pack("0.82.1.0", "north-india-home-foods", "2.4.0"),
    ]

    assert isinstance(results[0].data, FoodCapabilities)
    assert results[3].data.record.name == "Rajma masala"
    assert results[7].data.startswith("<!doctype html>")
    assert results[9].data.startswith(b"PK")
    assert [request.url.host for request in requests] == ["localhost"] * 10


def test_redirects_and_unsafe_path_values_fail_before_followup() -> None:
    calls = 0

    def redirect(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            302,
            headers={"location": "https://attacker.example"},
            request=request,
        )

    client = OpenNoshClient(transport=httpx.MockTransport(redirect))
    with pytest.raises(OpenNoshProblem, match="redirected") as captured:
        client.capabilities()
    assert captured.value.code == "redirect_refused"
    assert calls == 1

    with pytest.raises(TypeError):
        client.get_release_manifest("..")
    with pytest.raises(TypeError):
        client.get_public_food("federation", "rajma")
    assert calls == 1


def test_valid_problem_and_retry_after_are_typed_without_raw_body_leak() -> None:
    fixture = next(item for item in FIXTURES["problems"] if item["name"] == "rate_limited")

    def limited(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json=fixture["body"],
            headers={"content-type": "application/problem+json", "retry-after": "42"},
            request=request,
        )

    with pytest.raises(OpenNoshProblem) as captured:
        OpenNoshClient(transport=httpx.MockTransport(limited)).capabilities()
    assert captured.value.status == 429
    assert captured.value.code == "rate_limited"
    assert captured.value.retry_after_seconds == 42
    assert captured.value.request_reference == fixture["body"]["request_id"]

    secret = "raw-upstream-secret"

    def malformed(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            502,
            content=secret,
            headers={"content-type": "text/plain", "x-request-id": "safe-reference"},
            request=request,
        )

    with pytest.raises(OpenNoshProblem) as invalid:
        OpenNoshClient(transport=httpx.MockTransport(malformed)).capabilities()
    assert invalid.value.code == "unexpected_response"
    assert invalid.value.request_reference == "safe-reference"
    assert secret not in str(invalid.value)


def test_response_limits_media_types_timeouts_and_not_modified() -> None:
    def oversized(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"{}",
            headers={"content-type": "application/json", "content-length": "24577"},
            request=request,
        )

    client = OpenNoshClient(transport=httpx.MockTransport(oversized))
    with pytest.raises(OpenNoshProblem) as captured:
        client.get_commons_snapshot()
    assert captured.value.code == "response_too_large"
    with pytest.raises(ValueError):
        client.capabilities(timeout=10.01)
    with pytest.raises(ValueError):
        client.download_pack("0.82.1.0", "core", "1.0.0", timeout=30.01)

    def wrong_media(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"{}", headers={"content-type": "text/plain"}, request=request
        )

    with pytest.raises(OpenNoshProblem) as wrong:
        OpenNoshClient(transport=httpx.MockTransport(wrong_media)).capabilities()
    assert wrong.value.code == "unexpected_response"

    def unchanged(request: httpx.Request) -> httpx.Response:
        return httpx.Response(304, headers={"etag": '"same"'}, request=request)

    response = OpenNoshClient(transport=httpx.MockTransport(unchanged)).get_commons_snapshot()
    assert response.status == 304
    assert response.data is None
    assert response.etag == '"same"'


def test_transport_errors_map_to_stable_codes() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("private network detail", request=request)

    with pytest.raises(OpenNoshProblem) as captured:
        OpenNoshClient(transport=httpx.MockTransport(timeout)).capabilities(timeout=1)
    assert captured.value.code == "request_timeout"
    assert "private network detail" not in str(captured.value)

    def network(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("private host detail", request=request)

    with pytest.raises(OpenNoshProblem) as unavailable:
        OpenNoshClient(transport=httpx.MockTransport(network)).capabilities()
    assert unavailable.value.code == "network_error"
    assert "private host detail" not in str(unavailable.value)
    assert unavailable.value.__suppress_context__ is True


def test_n_minus_one_search_fixture_maps_to_the_retained_pydantic_contract() -> None:
    fixture = next(
        item
        for item in FIXTURES["n_minus_one_responses"]
        if item["operation_id"] == "search_api_v1_foods_search_get"
    )

    def legacy(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=fixture["body"],
            headers={"content-type": fixture["media_type"]},
            request=request,
        )

    response = OpenNoshClient(transport=httpx.MockTransport(legacy)).search_foods("beans")
    assert isinstance(response.data, FoodSearchResponseV1)
    assert response.data.offset == 0
    assert response.data.items[0].attribution.license == "CC0-1.0"


def test_encoded_success_response_is_refused_before_decompression() -> None:
    def compressed(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=gzip.compress(b"{}"),
            headers={"content-type": "application/json", "content-encoding": "gzip"},
            request=request,
        )

    with pytest.raises(OpenNoshProblem) as captured:
        OpenNoshClient(transport=httpx.MockTransport(compressed)).capabilities()
    assert captured.value.code == "unexpected_response"


def test_sync_timeout_is_one_wall_clock_deadline() -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.2)
        return response_for(request)

    started = time.monotonic()
    with pytest.raises(OpenNoshProblem) as captured:
        OpenNoshClient(transport=httpx.MockTransport(slow)).capabilities(timeout=0.01)
    elapsed = time.monotonic() - started

    assert captured.value.code == "request_timeout"
    assert elapsed < 0.15


@pytest.mark.asyncio
async def test_sync_client_rejects_an_active_event_loop() -> None:
    with pytest.raises(RuntimeError, match="AsyncOpenNoshClient"):
        OpenNoshClient(transport=httpx.MockTransport(response_for)).capabilities()
