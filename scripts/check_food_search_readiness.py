"""Fail unless a deployed OpenNosh instance passes its real-search readiness canary."""

from __future__ import annotations

import argparse
import json
from time import perf_counter
from urllib.parse import urljoin

import httpx
from opennosh_api.foods.schemas import FoodSearchReadiness, FoodSource
from pydantic import ValidationError

EXPECTED_ID = "community:gujarati-plain-thepla"
EXPECTED_PACK = "gujarati-home-cooking"
EXPECTED_LICENSE = "CC0-1.0"
EXPECTED_SOURCE_LICENSE = "contributor-original"
EXPECTED_PROVENANCE = "published_recipe_calculation"
MAX_RESPONSE_BYTES = 16_384


def check_food_search_readiness(
    origin: str,
    *,
    timeout_seconds: float = 2.0,
    max_latency_ms: float = 1_500.0,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, object]:
    url = urljoin(origin.rstrip("/") + "/", "api/v1/foods/readiness")
    started = perf_counter()
    with httpx.Client(timeout=timeout_seconds, transport=transport) as client:
        response = client.get(url, headers={"Accept": "application/json"})
    latency_ms = (perf_counter() - started) * 1_000
    if response.status_code != 200:
        raise RuntimeError(f"readiness returned HTTP {response.status_code}")
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise RuntimeError("readiness response exceeded the bounded payload size")
    try:
        readiness = FoodSearchReadiness.model_validate(response.json())
    except (ValueError, ValidationError) as error:
        raise RuntimeError("readiness response did not match the API schema") from error
    if latency_ms > max_latency_ms:
        raise RuntimeError(
            f"readiness exceeded the {max_latency_ms:.0f} ms latency budget: "
            f"{latency_ms:.1f} ms"
        )
    if (
        readiness.query != "thepla"
        or readiness.expected_id != EXPECTED_ID
        or readiness.result.id != EXPECTED_ID
        or readiness.result.source is not FoodSource.COMMUNITY
        or readiness.result.source_id != "gujarati-plain-thepla"
        or readiness.result.attribution.license != EXPECTED_LICENSE
        or readiness.result.attribution.source_license != EXPECTED_SOURCE_LICENSE
        or readiness.result.attribution.pack_id != EXPECTED_PACK
        or readiness.result.attribution.provenance != EXPECTED_PROVENANCE
    ):
        raise RuntimeError("readiness did not return the expected approved food metadata")
    return {
        "status": "ready",
        "url": url,
        "latency_ms": round(latency_ms, 1),
        "result_id": readiness.result.id,
        "source": readiness.result.source.value,
        "license": readiness.result.attribution.license,
        "pack_id": readiness.result.attribution.pack_id,
        "provenance": readiness.result.attribution.provenance,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("origin", help="Public origin, for example https://opennosh.org")
    parser.add_argument("--timeout-seconds", type=float, default=2.0)
    parser.add_argument("--max-latency-ms", type=float, default=1_500.0)
    arguments = parser.parse_args()
    try:
        report = check_food_search_readiness(
            arguments.origin,
            timeout_seconds=arguments.timeout_seconds,
            max_latency_ms=arguments.max_latency_ms,
        )
    except (httpx.HTTPError, RuntimeError) as error:
        parser.exit(1, f"food search readiness failed: {error}\n")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
