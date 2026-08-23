from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from benchmarks.performance.contract import load_contract
from benchmarks.performance.harness import (
    _artifact_manifest,
    _exact_weighted_schedule,
    _load_resource_evidence,
    _parse_boundaries,
    _query_interaction,
    _relevant,
    _response_object,
    _sample_from_browser,
    _wait_for_cell_ready,
)
from benchmarks.performance.metrics import percentile, summarize_samples


def _response(status: int, payload: object) -> httpx.Response:
    return httpx.Response(
        status,
        json=payload,
        request=httpx.Request("GET", "https://example.test/foods"),
    )


class _SequenceClient:
    def __init__(self, values: list[httpx.Response | Exception]) -> None:
        self.values = iter(values)
        self.calls: list[dict[str, Any]] = []

    async def get(self, path: str, **kwargs: Any) -> httpx.Response:
        self.calls.append({"path": path, **kwargs})
        value = next(self.values)
        if isinstance(value, Exception):
            raise value
        return value


def _query(*, pages: int = 1, mode: str = "contains", expected: object = "apple") -> dict[str, Any]:
    return {
        "path": "/v1/foods",
        "query": "apple",
        "params": {"pages": pages, "limit": 20},
        "relevance": {"mode": mode, "expected": expected},
    }


def test_exact_weighted_schedule_is_complete_deterministic_and_rejects_zero() -> None:
    items = [{"id": "a", "weight_bps": 6_000}, {"id": "b", "weight_bps": 4_000}]
    first, counts = _exact_weighted_schedule(items, 7, seed="fixed")
    second, second_counts = _exact_weighted_schedule(items, 7, seed="fixed")

    assert first == second
    assert counts == second_counts == {"a": 4, "b": 3}
    assert len(first) == sum(counts.values()) == 7
    with pytest.raises(ValueError, match="at least one interaction"):
        _exact_weighted_schedule(items, 0, seed="fixed")


def test_response_and_relevance_cover_status_empty_contains_and_invalid_payload() -> None:
    success = _response(200, {"items": [{"name": "Apple"}]})
    assert _response_object(success) == {"items": [{"name": "Apple"}]}
    assert _relevant(_query(), success, success.json())
    assert _relevant(
        _query(mode="empty", expected=True), _response(200, {"items": []}), {"items": []}
    )
    assert _relevant(_query(mode="status", expected=404), _response(404, {}), None)
    assert not _relevant(_query(), _response(500, {}), None)
    with pytest.raises(ValueError, match="JSON object"):
        _response_object(_response(200, []))


@pytest.mark.asyncio
async def test_query_interaction_follows_cursor_and_marks_later_http_failure() -> None:
    client = _SequenceClient(
        [
            _response(200, {"items": [{"name": "Apple"}], "next_cursor": "next"}),
            _response(503, {"detail": "busy"}),
        ]
    )

    sample = await _query_interaction(client, _query(pages=2), cold=True)  # type: ignore[arg-type]

    assert sample.error is True
    assert sample.relevant is False
    assert sample.error_code == "http_503"
    assert client.calls[0]["headers"] == {"Cache-Control": "no-cache"}
    assert client.calls[1]["params"]["cursor"] == "next"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected_code", "expected_timeout"),
    [
        (httpx.TimeoutException("slow"), "timeout", True),
        (httpx.ConnectError("offline"), "ConnectError", False),
    ],
)
async def test_query_interaction_records_transport_failures(
    failure: Exception,
    expected_code: str,
    expected_timeout: bool,
) -> None:
    sample = await _query_interaction(
        _SequenceClient([failure]),  # type: ignore[arg-type]
        _query(),
        cold=False,
    )

    assert sample.error is True
    assert sample.timeout is expected_timeout
    assert sample.relevant is False
    assert sample.error_code == expected_code


@pytest.mark.asyncio
async def test_query_interaction_records_malformed_success_response() -> None:
    sample = await _query_interaction(
        _SequenceClient([_response(200, [])]),  # type: ignore[arg-type]
        _query(),
        cold=False,
    )

    assert sample.error is True
    assert sample.error_code == "malformed_response"


def test_browser_sample_validation_accepts_valid_and_rejects_bad_fields() -> None:
    sample = _sample_from_browser(
        {
            "latency_ms": 12.5,
            "error": False,
            "timeout": False,
            "relevant": True,
            "error_code": None,
        }
    )
    assert sample.latency_ms == 12.5

    invalid_values = [
        None,
        {"latency_ms": -1, "error": False, "timeout": False, "relevant": True},
        {"latency_ms": 1, "error": "no", "timeout": False, "relevant": True},
        {
            "latency_ms": 1,
            "error": False,
            "timeout": False,
            "relevant": True,
            "error_code": 500,
        },
    ]
    for value in invalid_values:
        with pytest.raises(ValueError, match="edge browser runner"):
            _sample_from_browser(value)


def test_parse_boundaries_requires_every_known_http_boundary() -> None:
    parsed = _parse_boundaries(
        [
            "fastapi=http://api.test",
            "same_origin_proxy=https://web.test",
            "edge_browser=https://edge.test",
        ]
    )
    assert parsed == {
        "fastapi": "http://api.test",
        "same_origin_proxy": "https://web.test",
        "edge_browser": "https://edge.test",
    }
    with pytest.raises(ValueError, match="full contract runs require"):
        _parse_boundaries(["fastapi=http://api.test"])
    with pytest.raises(ValueError, match="--boundary must be"):
        _parse_boundaries(["fastapi=file:///tmp/api"])


def test_resource_evidence_requires_exact_roles_metrics_and_typed_values(tmp_path: Path) -> None:
    evidence = {
        "memory_high_water_bytes": {
            role: {"value": 1024, "source": "observer", "observed_at": "2026-08-23T00:00:00Z"}
            for role in ("postgresql", "fastapi", "same_origin_proxy", "edge_browser")
        },
        **{
            metric: {"value": 1.5, "source": "observer", "observed_at": "2026-08-23T00:00:00Z"}
            for metric in ("index_build_ms", "job_age_p95_ms", "projection_lag_p95_ms")
        },
    }
    path = tmp_path / "resources.json"
    path.write_text(json.dumps(evidence))
    assert _load_resource_evidence(path) == evidence

    evidence["memory_high_water_bytes"].pop("edge_browser")
    path.write_text(json.dumps(evidence))
    with pytest.raises(ValueError, match="memory evidence must contain exactly"):
        _load_resource_evidence(path)

    path.write_text("not-json")
    with pytest.raises(ValueError, match="not valid JSON"):
        _load_resource_evidence(path)


@pytest.mark.asyncio
async def test_wait_for_cell_ready_accepts_both_ready_events() -> None:
    ready = (asyncio.Event(), asyncio.Event())
    start = asyncio.Event()

    async def workload(event: asyncio.Event) -> None:
        event.set()
        await start.wait()

    tasks = tuple(asyncio.create_task(workload(event)) for event in ready)
    await _wait_for_cell_ready(ready, tasks)
    start.set()
    await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_wait_for_cell_ready_rejects_workload_that_ends_early() -> None:
    ready = (asyncio.Event(), asyncio.Event())

    async def ended() -> None:
        return None

    tasks = (asyncio.create_task(ended()), asyncio.create_task(asyncio.sleep(60)))
    try:
        with pytest.raises(RuntimeError, match="ended before"):
            await _wait_for_cell_ready(ready, tasks)
    finally:
        tasks[1].cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def test_artifact_manifest_records_relative_paths_digests_and_sizes(tmp_path: Path) -> None:
    first = tmp_path / "a.json"
    second = tmp_path / "nested" / "b.json"
    second.parent.mkdir()
    first.write_bytes(b"a\n")
    second.write_bytes(b"bb\n")

    manifest_path, digest = _artifact_manifest(tmp_path, [second, first])
    document = json.loads(manifest_path.read_text())

    assert [entry["path"] for entry in document["files"]] == ["a.json", "nested/b.json"]
    assert document["files"][0]["bytes"] == 2
    assert digest == hashlib.sha256(manifest_path.read_bytes()).hexdigest()


def test_percentile_and_summary_reject_empty_or_out_of_range_samples() -> None:
    with pytest.raises(ValueError, match="empty sample"):
        percentile([], 50)
    with pytest.raises(ValueError, match="between 0 and 100"):
        percentile([1], 101)
    with pytest.raises(ValueError, match="at least one sample"):
        summarize_samples(
            boundary="fastapi",
            cache_state="warm",
            workload="anonymous_read",
            samples=[],
        )


def test_contract_profile_rejects_unknown_profile() -> None:
    with pytest.raises(ValueError, match="unknown benchmark profile"):
        load_contract().profile("not-a-profile")
