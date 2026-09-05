from __future__ import annotations

import httpx
import pytest

import scripts.check_food_search_readiness as readiness_check_module
from scripts.check_food_search_readiness import check_food_search_readiness


def _payload() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "status": "ready",
        "query": "thepla",
        "expected_id": "community:gujarati-plain-thepla",
        "latency_ms": 12.5,
        "result": {
            "id": "community:gujarati-plain-thepla",
            "source": "community",
            "source_id": "gujarati-plain-thepla",
            "name": "Plain thepla",
            "name_local": None,
            "category": "bread",
            "attribution": {
                "source": "community",
                "license": "CC0-1.0",
                "source_license": "contributor-original",
                "pack_id": "gujarati-home-cooking",
                "pack_version": "1.0.0",
                "provenance": "published_recipe_calculation",
            },
        },
    }


def _payload_with_provenance(value: str) -> dict[str, object]:
    payload = _payload()
    result = payload["result"]
    assert isinstance(result, dict)
    attribution = result["attribution"]
    assert isinstance(attribution, dict)
    attribution["provenance"] = value
    return payload


def test_search_readiness_check_validates_the_approved_record() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=_payload()))

    report = check_food_search_readiness("https://opennosh.org", transport=transport)

    assert report["status"] == "ready"
    assert report["result_id"] == "community:gujarati-plain-thepla"
    assert report["license"] == "CC0-1.0"


@pytest.mark.parametrize(
    "response",
    (
        httpx.Response(503, json={"detail": "unavailable"}),
        httpx.Response(200, json={"status": "ready"}),
        httpx.Response(200, json={**_payload(), "expected_id": "community:other"}),
        httpx.Response(200, json={**_payload(), "query": "rice"}),
        httpx.Response(200, json=_payload_with_provenance("own_measurement")),
    ),
)
def test_search_readiness_check_fails_closed(response: httpx.Response) -> None:
    transport = httpx.MockTransport(lambda request: response)

    with pytest.raises(RuntimeError):
        check_food_search_readiness("https://opennosh.org", transport=transport)


def test_search_readiness_check_rejects_an_oversized_response() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"x" * 16_385)
    )

    with pytest.raises(RuntimeError, match="bounded payload size"):
        check_food_search_readiness("https://opennosh.org", transport=transport)


def test_search_readiness_check_enforces_external_latency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readings = iter((0.0, 1.501))
    monkeypatch.setattr(readiness_check_module, "perf_counter", lambda: next(readings))
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=_payload()))

    with pytest.raises(RuntimeError, match="1500 ms latency budget"):
        check_food_search_readiness("https://opennosh.org", transport=transport)
