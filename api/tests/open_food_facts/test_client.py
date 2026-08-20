from __future__ import annotations

import asyncio
import gzip
import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from opennosh_api.integrations.open_food_facts import (
    OPEN_FOOD_FACTS_FIELDS,
    OpenFoodFactsClient,
    OpenFoodFactsNotFoundError,
    OpenFoodFactsRateLimitedError,
    OpenFoodFactsTimeoutError,
    OpenFoodFactsUpstreamError,
    normalize_barcode,
    parse_product,
)

FIXTURES = Path(__file__).with_name("fixtures")
BARCODE = "3017620422003"


def _fixture(name: str) -> object:
    return json.loads((FIXTURES / name).read_text())


def _client(transport: httpx.AsyncBaseTransport) -> OpenFoodFactsClient:
    return OpenFoodFactsClient(
        base_url="https://off.example.test",
        app_version="0.15.0.0",
        contact="https://example.test/opennosh",
        timeout_seconds=0.1,
        transport=transport,
    )


async def _fetch(
    transport: httpx.AsyncBaseTransport, barcode: str = BARCODE
) -> object:
    client = _client(transport)
    try:
        return await client.fetch(barcode)
    finally:
        await client.aclose()


class _ChunkedStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes], *, delay_seconds: float = 0) -> None:
        self._chunks = chunks
        self._delay_seconds = delay_seconds

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            if self._delay_seconds:
                await asyncio.sleep(self._delay_seconds)
            yield chunk


def test_lookup_uses_v3_allowlisted_fields_and_identifying_user_agent() -> None:
    payload = _fixture("product.json")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/api/v3/product/{BARCODE}"
        assert request.url.params["fields"].split(",") == list(OPEN_FOOD_FACTS_FIELDS)
        assert all("image" not in field for field in OPEN_FOOD_FACTS_FIELDS)
        assert request.headers["User-Agent"] == (
            "opennosh/0.15.0.0 (https://example.test/opennosh)"
        )
        assert request.headers["Accept-Encoding"] == "identity"
        return httpx.Response(
            200,
            stream=_ChunkedStream([json.dumps(payload).encode()]),
            headers={"Content-Type": "application/json"},
        )

    product = asyncio.run(_fetch(httpx.MockTransport(handler)))

    assert product.barcode == BARCODE
    assert product.product_name == "Hazelnut cocoa spread"
    assert product.nutrients_json["nutrients"]["sodium_mg"] == "42.8000"
    assert "image" not in json.dumps(product.nutrients_json).casefold()


@pytest.mark.parametrize(
    "barcode",
    ["", "1234567", "3017620422004", "301762042200x", " 3017620422003"],
)
def test_invalid_barcodes_are_rejected_before_network(barcode: str) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    with pytest.raises(ValueError, match="barcode|GTIN"):
        asyncio.run(_fetch(httpx.MockTransport(handler), barcode))
    assert calls == 0


def test_timeout_rate_limit_not_found_and_upstream_errors_are_controlled() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("fixture timeout", request=request)

    with pytest.raises(OpenFoodFactsTimeoutError):
        asyncio.run(_fetch(httpx.MockTransport(timeout)))

    cases = [
        (httpx.Response(429, headers={"Retry-After": "17"}), OpenFoodFactsRateLimitedError),
        (httpx.Response(404), OpenFoodFactsNotFoundError),
        (httpx.Response(503), OpenFoodFactsRateLimitedError),
        (httpx.Response(500), OpenFoodFactsUpstreamError),
        (
            httpx.Response(200, stream=_ChunkedStream([b"not-json"])),
            OpenFoodFactsUpstreamError,
        ),
    ]
    for response, expected_error in cases:
        with pytest.raises(expected_error):
            asyncio.run(
                _fetch(httpx.MockTransport(lambda _request, item=response: item))
            )


def test_untrusted_invalid_nutrition_is_rejected_and_images_are_discarded() -> None:
    with pytest.raises(OpenFoodFactsUpstreamError, match="invalid"):
        parse_product(_fixture("invalid_product.json"), BARCODE)

    product = parse_product(_fixture("product.json"), BARCODE)
    serialized = json.dumps(product.nutrients_json)
    assert "image_front_url" not in serialized
    assert "images" not in serialized

    for unsafe_name in ("unsafe\x7ftext", "display\u202ereversed", "bad\ud800name"):
        payload = _fixture("product.json")
        assert isinstance(payload, dict)
        assert isinstance(payload["product"], dict)
        payload["product"]["product_name"] = unsafe_name
        with pytest.raises(OpenFoodFactsUpstreamError, match="product name is invalid"):
            parse_product(payload, BARCODE)


@pytest.mark.parametrize("nutrition_shape", ["direct", "nested"])
def test_extreme_sodium_values_are_controlled(nutrition_shape: str) -> None:
    payload = _fixture("product.json")
    assert isinstance(payload, dict)
    assert isinstance(payload["product"], dict)
    product = payload["product"]
    if nutrition_shape == "direct":
        assert isinstance(product["nutriments"], dict)
        product["nutriments"]["sodium_100g"] = "1e999999"
    else:
        product["nutriments"] = None
        product["nutrition"] = {
            "aggregated_set": {
                "per": "100g",
                "nutrients": {
                    "energy-kcal": {"value": 170, "unit": "kcal"},
                    "proteins": {"value": 10, "unit": "g"},
                    "fat": {"value": 10, "unit": "g"},
                    "carbohydrates": {"value": 10, "unit": "g"},
                    "sodium": {"value": "1e999999", "unit": "g"},
                },
            }
        }

    with pytest.raises(OpenFoodFactsUpstreamError, match="incomplete or invalid"):
        parse_product(payload, BARCODE)


def test_deeply_nested_json_is_a_controlled_upstream_error() -> None:
    nested_json = ("[" * 10_000 + "0" + "]" * 10_000).encode()
    response = httpx.Response(200, stream=_ChunkedStream([nested_json]))

    with pytest.raises(OpenFoodFactsUpstreamError, match="invalid JSON"):
        asyncio.run(_fetch(httpx.MockTransport(lambda _request: response)))


def test_valid_v3_nutrition_is_used_when_legacy_nutriments_are_empty() -> None:
    payload = _fixture("product.json")
    assert isinstance(payload, dict)
    assert isinstance(payload["product"], dict)
    payload["product"]["nutriments"] = {}
    payload["product"]["nutrition"] = {
        "aggregated_set": {
            "per": "100g",
            "nutrients": {
                "energy-kcal": {"value": 170, "unit": "kcal"},
                "proteins": {"value": 10, "unit": "g"},
                "fat": {"value": 10, "unit": "g"},
                "carbohydrates": {"value": 10, "unit": "g"},
            },
        }
    }

    product = parse_product(payload, BARCODE)

    assert product.nutrients_json["nutrients"]["energy_kcal"] == "170"


def test_streaming_cap_rejects_chunked_and_compressed_oversized_responses() -> None:
    oversized = b"x" * (1024 * 1024 + 1)
    responses = [
        httpx.Response(200, stream=_ChunkedStream([oversized[:700_000], oversized[700_000:]])),
        httpx.Response(
            200,
            content=gzip.compress(oversized),
            headers={"Content-Encoding": "gzip"},
        ),
    ]
    for index, response in enumerate(responses):
        expected = "too large" if index == 0 else "unsupported content encoding"
        with pytest.raises(OpenFoodFactsUpstreamError, match=expected):
            asyncio.run(
                _fetch(httpx.MockTransport(lambda _request, item=response: item))
            )


@pytest.mark.parametrize(
    ("content_length", "expected_error"),
    [
        ("not-a-number", "invalid Content-Length"),
        (str(1024 * 1024 + 1), "too large"),
        ("-1", "too large"),
    ],
)
def test_invalid_or_oversized_content_length_is_rejected_before_buffering(
    content_length: str, expected_error: str
) -> None:
    response = httpx.Response(
        200,
        stream=_ChunkedStream([json.dumps(_fixture("product.json")).encode()]),
        headers={"Content-Length": content_length},
    )

    with pytest.raises(OpenFoodFactsUpstreamError, match=expected_error):
        asyncio.run(_fetch(httpx.MockTransport(lambda _request: response)))


def test_generic_transport_failure_is_controlled() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("fixture connection failure", request=request)

    with pytest.raises(OpenFoodFactsUpstreamError, match="request failed"):
        asyncio.run(_fetch(httpx.MockTransport(fail)))


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"product": None},
        {"product": {"code": "036000291452"}},
        {
            "product": {
                "code": BARCODE,
                "product_name": "Missing nutrition",
            }
        },
    ],
)
def test_missing_or_malformed_product_structures_are_rejected(payload: object) -> None:
    with pytest.raises(OpenFoodFactsUpstreamError):
        parse_product(payload, BARCODE)


def test_nested_nutrition_rejects_unsupported_units() -> None:
    payload = _fixture("product.json")
    assert isinstance(payload, dict)
    assert isinstance(payload["product"], dict)
    payload["product"].pop("nutriments")
    payload["product"]["nutrition"] = {
        "aggregated_set": {
            "per": "100g",
            "nutrients": {
                "energy-kcal": {"value": 170, "unit": "kJ"},
            },
        }
    }

    with pytest.raises(OpenFoodFactsUpstreamError, match="incomplete or invalid"):
        parse_product(payload, BARCODE)


def test_total_deadline_stops_a_slow_stream() -> None:
    client = OpenFoodFactsClient(
        base_url="https://off.example.test",
        app_version="0.15.0.0",
        contact="https://example.test/opennosh",
        timeout_seconds=0.01,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                stream=_ChunkedStream([b"{}"], delay_seconds=0.02),
            )
        ),
    )

    async def run() -> None:
        try:
            with pytest.raises(OpenFoodFactsTimeoutError):
                await client.fetch(BARCODE)
        finally:
            await client.aclose()

    asyncio.run(run())


def test_supported_gtin_lengths_and_checksum_are_enforced() -> None:
    assert normalize_barcode("96385074") == "96385074"
    assert normalize_barcode("036000291452") == "036000291452"
    assert normalize_barcode(BARCODE) == BARCODE
    assert normalize_barcode("10012345000017") == "10012345000017"
