"""Run the pinned benchmark and write a self-describing artifact bundle."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import platform
import random
import subprocess
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from opennosh_api.foods.service import (  # type: ignore[import-untyped]
    FOOD_SEARCH_SNAPSHOT_INSERT_SQL,
    FOOD_SEARCH_SQL,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from benchmarks.performance.contract import (
    DEFAULT_CONTRACT_PATH,
    BenchmarkContract,
    canonical_json_bytes,
    load_contract,
)
from benchmarks.performance.database import (
    capture_query_plans,
    current_snapshot,
    database_environment,
    resource_snapshot,
    seed_database,
)
from benchmarks.performance.metrics import Sample, evaluate_gates, summarize_samples
from benchmarks.performance.reproducibility import sanitized_command
from benchmarks.performance.result_validation import validate_result_bundle

BOUNDARY_IDS = ("fastapi", "same_origin_proxy", "edge_browser")
MEMORY_EVIDENCE_ROLES = {"postgresql", "fastapi", "same_origin_proxy", "edge_browser"}
EXTERNAL_EVIDENCE_METRICS = {"index_build_ms", "job_age_p95_ms", "projection_lag_p95_ms"}
EDGE_BROWSER_RUNNER = (
    Path(__file__).resolve().parents[2] / "web/scripts/performance_edge_browser.mjs"
)


def _exact_weighted_schedule(
    items: list[dict[str, Any]], count: int, *, seed: str
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Apportion a fixed run deterministically with largest remainders."""
    if count < 1:
        raise ValueError("a weighted schedule requires at least one interaction")
    allocations = [count * int(item["weight_bps"]) // 10_000 for item in items]
    remainders = [count * int(item["weight_bps"]) % 10_000 for item in items]
    remaining = count - sum(allocations)
    ranked = sorted(
        range(len(items)),
        key=lambda index: (-remainders[index], str(items[index]["id"])),
    )
    for index in ranked[:remaining]:
        allocations[index] += 1
    schedule = [
        item for item, allocation in zip(items, allocations, strict=True) for _ in range(allocation)
    ]
    random.Random(seed).shuffle(schedule)
    return schedule, {
        str(item["id"]): allocation for item, allocation in zip(items, allocations, strict=True)
    }


def _response_object(response: httpx.Response) -> dict[str, Any]:
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("successful benchmark response must be a JSON object")
    return payload


def _relevant(
    query: dict[str, Any], response: httpx.Response, payload: dict[str, Any] | None
) -> bool:
    rule = query["relevance"]
    if rule["mode"] == "status":
        return response.status_code == int(rule["expected"])
    if response.status_code != 200 or payload is None:
        return False
    if rule["mode"] == "empty":
        return payload.get("items") == []
    expected = str(rule["expected"]).casefold()
    return expected in json.dumps(payload, ensure_ascii=False).casefold()


async def _query_interaction(
    client: httpx.AsyncClient, query: dict[str, Any], *, cold: bool
) -> Sample:
    params = dict(query["params"])
    pages = int(params.pop("pages", 1))
    if query["query"]:
        params["q"] = query["query"]
    headers = {"Cache-Control": "no-cache"} if cold else {}
    started = time.perf_counter()
    try:
        response = await client.get(query["path"], params=params, headers=headers)
        payload = _response_object(response) if response.status_code == 200 else None
        relevant = _relevant(query, response, payload)
        error = not 200 <= response.status_code < 300
        cursor = payload.get("next_cursor") if payload is not None else None
        for _page in range(1, pages):
            if not cursor:
                break
            params["cursor"] = cursor
            response = await client.get(query["path"], params=params, headers=headers)
            if response.status_code != 200:
                error = True
                relevant = False
                break
            payload = _response_object(response)
            cursor = payload.get("next_cursor")
        return Sample(
            latency_ms=(time.perf_counter() - started) * 1_000,
            error=error,
            relevant=relevant,
            error_code=f"http_{response.status_code}" if error else None,
        )
    except httpx.TimeoutException:
        return Sample(
            latency_ms=(time.perf_counter() - started) * 1_000,
            error=True,
            timeout=True,
            relevant=False,
            error_code="timeout",
        )
    except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
        return Sample(
            latency_ms=(time.perf_counter() - started) * 1_000,
            error=True,
            relevant=False,
            error_code="malformed_response",
        )
    except httpx.HTTPError as error:
        return Sample(
            latency_ms=(time.perf_counter() - started) * 1_000,
            error=True,
            relevant=False,
            error_code=type(error).__name__,
        )


async def run_http_boundary(
    boundary: str,
    base_url: str,
    cache_state: str,
    contract: BenchmarkContract,
    *,
    requests: int,
    concurrency: int,
    ready: asyncio.Event,
    start: asyncio.Event,
) -> tuple[dict[str, object], dict[str, int], float]:
    query_mix = contract.document["query_mix"]
    schedule, query_counts = _exact_weighted_schedule(
        query_mix, requests, seed=f"{contract.seed}:{boundary}:{cache_state}"
    )
    limits = httpx.Limits(
        max_connections=concurrency,
        max_keepalive_connections=concurrency if cache_state == "warm" else 0,
    )
    samples: list[Sample] = []
    async with httpx.AsyncClient(
        base_url=base_url.rstrip("/") + "/",
        timeout=httpx.Timeout(15.0),
        limits=limits,
        follow_redirects=False,
        headers={"User-Agent": "opennosh-benchmark/1.0"},
    ) as client:
        if cache_state == "warm":
            for query in query_mix:
                await _query_interaction(client, query, cold=False)
        ready.set()
        await start.wait()
        measured_started = time.perf_counter()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        for query in schedule:
            queue.put_nowait(query)

        async def worker() -> None:
            while True:
                try:
                    query = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                samples.append(await _query_interaction(client, query, cold=cache_state == "cold"))
                queue.task_done()

        await asyncio.gather(*(worker() for _ in range(min(concurrency, requests))))
        elapsed_ms = (time.perf_counter() - measured_started) * 1_000
    return (
        summarize_samples(
            boundary=boundary,
            cache_state=cache_state,
            workload="anonymous_read",
            samples=samples,
        ),
        query_counts,
        elapsed_ms,
    )


def _sample_from_browser(value: object) -> Sample:
    if not isinstance(value, dict):
        raise ValueError("edge browser runner returned a non-object sample")
    latency = value.get("latency_ms")
    if not isinstance(latency, (int, float)) or isinstance(latency, bool) or latency < 0:
        raise ValueError("edge browser runner returned an invalid sample latency")
    error = value.get("error")
    timeout = value.get("timeout")
    relevant = value.get("relevant")
    error_code = value.get("error_code")
    if (
        not isinstance(error, bool)
        or not isinstance(timeout, bool)
        or not isinstance(relevant, bool)
    ):
        raise ValueError("edge browser runner returned invalid sample flags")
    if error_code is not None and not isinstance(error_code, str):
        raise ValueError("edge browser runner returned an invalid error code")
    return Sample(float(latency), error, timeout, relevant, error_code)


async def run_edge_browser_boundary(
    base_url: str,
    cache_state: str,
    contract: BenchmarkContract,
    *,
    requests: int,
    concurrency: int,
    ready: asyncio.Event,
    start: asyncio.Event,
) -> tuple[dict[str, object], dict[str, int], float]:
    if not EDGE_BROWSER_RUNNER.is_file():
        raise ValueError(f"edge browser runner is missing: {EDGE_BROWSER_RUNNER}")
    schedule, query_counts = _exact_weighted_schedule(
        contract.document["query_mix"],
        requests,
        seed=f"{contract.seed}:edge_browser:{cache_state}",
    )
    process = await asyncio.create_subprocess_exec(
        "node",
        str(EDGE_BROWSER_RUNNER),
        cwd=str(EDGE_BROWSER_RUNNER.parents[1]),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    if process.stdin is None or process.stdout is None or process.stderr is None:
        process.kill()
        await process.wait()
        raise RuntimeError("failed to create edge browser runner pipes")
    configuration = {
        "base_url": base_url,
        "cache_state": cache_state,
        "concurrency": concurrency,
        "query_mix": contract.document["query_mix"],
        "schedule": [str(item["id"]) for item in schedule],
    }
    try:
        process.stdin.write(canonical_json_bytes(configuration))
        await process.stdin.drain()
        runner_ready = await asyncio.wait_for(process.stderr.readline(), timeout=120)
        if runner_ready != b"READY\n":
            error_output = runner_ready + await process.stderr.read()
            process.kill()
            await process.wait()
            raise RuntimeError(
                "edge browser runner failed before readiness: "
                + error_output.decode(errors="replace").strip()
            )
        ready.set()
        await start.wait()
        process.stdin.write(b"START\n")
        await process.stdin.drain()
        process.stdin.close()
        output, error_output = await asyncio.gather(process.stdout.read(), process.stderr.read())
        return_code = await process.wait()
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    if return_code != 0:
        raise RuntimeError(
            "edge browser runner failed: " + error_output.decode(errors="replace").strip()
        )
    try:
        document = json.loads(output)
    except json.JSONDecodeError as error:
        raise ValueError("edge browser runner returned malformed JSON") from error
    if not isinstance(document, dict) or not isinstance(document.get("samples"), list):
        raise ValueError("edge browser runner returned an invalid result object")
    elapsed_ms = document.get("elapsed_ms")
    if not isinstance(elapsed_ms, (int, float)) or isinstance(elapsed_ms, bool) or elapsed_ms < 0:
        raise ValueError("edge browser runner returned an invalid elapsed time")
    samples = [_sample_from_browser(sample) for sample in document["samples"]]
    if len(samples) != requests:
        raise ValueError(
            f"edge browser runner returned {len(samples)} samples for {requests} interactions"
        )
    return (
        summarize_samples(
            boundary="edge_browser",
            cache_state=cache_state,
            workload="anonymous_read",
            samples=samples,
        ),
        query_counts,
        float(elapsed_ms),
    )


async def _mixed_database_operation(
    engine: AsyncEngine, operation: str, ordinal: int, snapshot_id: object
) -> Sample:
    started = time.perf_counter()
    unique = f"load-{ordinal}-{uuid4().hex}"
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                if operation == "anonymous_read":
                    await connection.execute(
                        text(FOOD_SEARCH_SQL),
                        {
                            "query": "benchmark food",
                            "slug_query": "benchmark food",
                            "locale": "en-us",
                            "source_filter": None,
                            "snapshot_id": snapshot_id,
                            "has_cursor": False,
                            "after_rank": 0,
                            "after_score": 0.0,
                            "after_name": "",
                            "after_source": "",
                            "after_source_id": "",
                            "fetch_limit": 21,
                        },
                    )
                elif operation == "tracker":
                    await connection.scalar(text("SELECT count(*) FROM log_entries"))
                elif operation == "publication":
                    await connection.execute(
                        text(
                            """
                            INSERT INTO foods_community (
                                pack_id, pack_version, slug, name, locale, category,
                                provenance, source_license, nutrients_json, portions_json,
                                contributed_by
                            ) VALUES (
                                'benchmark-live', '1.0.0', :slug, :name, 'en-US', 'benchmark',
                                'own_measurement', 'contributor-original', '{}'::jsonb,
                                '[]'::jsonb, 'opennosh-benchmark'
                            )
                            """
                        ),
                        {"slug": unique, "name": f"Benchmark publication {unique}"},
                    )
                elif operation == "pack_ingestion":
                    await connection.execute(
                        text(
                            """
                            INSERT INTO foods_community (
                                pack_id, pack_version, slug, name, locale, category,
                                provenance, source_license, nutrients_json, portions_json,
                                contributed_by
                            )
                            SELECT
                                CAST(:pack AS text), '1.0.0', CAST(:pack AS text) || '-' || value,
                                'Benchmark pack food ' || value, 'en-US', 'benchmark',
                                'own_measurement', 'contributor-original', '{}'::jsonb,
                                '[]'::jsonb, 'opennosh-benchmark'
                            FROM generate_series(1, 10) AS value
                            """
                        ),
                        {"pack": unique},
                    )
                elif operation == "projection_rebuild":
                    candidate = uuid4()
                    await connection.execute(
                        text(
                            """
                            INSERT INTO food_search_snapshots (
                                id, ranking_version, created_at, expires_at
                            ) VALUES (
                                CAST(:snapshot_id AS uuid), 1, now(), now() + INTERVAL '30 minutes'
                            )
                            """
                        ),
                        {"snapshot_id": candidate},
                    )
                    await connection.execute(
                        text(FOOD_SEARCH_SNAPSHOT_INSERT_SQL), {"snapshot_id": candidate}
                    )
                else:
                    raise ValueError(f"unsupported traffic operation: {operation}")
            finally:
                await transaction.rollback()
        return Sample(latency_ms=(time.perf_counter() - started) * 1_000)
    except Exception as error:
        return Sample(
            latency_ms=(time.perf_counter() - started) * 1_000,
            error=True,
            relevant=False,
            error_code=type(error).__name__,
        )


async def run_mixed_database_load(
    engine: AsyncEngine,
    contract: BenchmarkContract,
    *,
    requests: int,
    concurrency: int,
    schedule_seed: str,
    ready: asyncio.Event,
    start: asyncio.Event,
) -> tuple[dict[str, list[Sample]], dict[str, int], float]:
    weighted_schedule, traffic_counts = _exact_weighted_schedule(
        contract.document["traffic_mix"],
        requests,
        seed=f"{contract.seed}:{schedule_seed}:mixed-database",
    )
    schedule = [str(item["id"]) for item in weighted_schedule]
    snapshot_id = await current_snapshot(engine)
    samples: defaultdict[str, list[Sample]] = defaultdict(list)
    queue: asyncio.Queue[tuple[int, str]] = asyncio.Queue()
    for ordinal, operation in enumerate(schedule):
        queue.put_nowait((ordinal, operation))
    ready.set()
    await start.wait()
    measured_started = time.perf_counter()

    async def worker() -> None:
        while True:
            try:
                ordinal, operation = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            samples[operation].append(
                await _mixed_database_operation(engine, operation, ordinal, snapshot_id)
            )
            queue.task_done()

    await asyncio.gather(*(worker() for _ in range(min(concurrency, requests))))
    elapsed_ms = (time.perf_counter() - measured_started) * 1_000
    return dict(samples), traffic_counts, elapsed_ms


async def _sample_connection_peak(engine: AsyncEngine, stop: asyncio.Event) -> tuple[int, int]:
    peak = 0
    samples = 0
    while True:
        async with engine.connect() as connection:
            connections = int(
                await connection.scalar(
                    text("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()")
                )
            )
        peak = max(peak, connections)
        samples += 1
        if stop.is_set():
            return peak, samples
        try:
            await asyncio.wait_for(stop.wait(), timeout=0.05)
        except TimeoutError:
            pass


async def _wait_for_cell_ready(
    ready_events: tuple[asyncio.Event, asyncio.Event],
    workload_tasks: tuple[asyncio.Task[Any], asyncio.Task[Any]],
) -> None:
    ready_waiter = asyncio.gather(*(event.wait() for event in ready_events))
    done, _pending = await asyncio.wait(
        {ready_waiter, *workload_tasks}, return_when=asyncio.FIRST_COMPLETED
    )
    if ready_waiter in done:
        await ready_waiter
        return
    ready_waiter.cancel()
    await asyncio.gather(ready_waiter, return_exceptions=True)
    for task in done:
        await task
    raise RuntimeError("a benchmark workload ended before the synchronized start")


async def _run_measurement_cell(
    engine: AsyncEngine,
    contract: BenchmarkContract,
    *,
    boundary: str,
    base_url: str,
    cache_state: str,
    requests: int,
    mixed_requests: int,
    concurrency: int,
) -> tuple[dict[str, object], dict[str, list[Sample]], dict[str, object], int]:
    boundary_ready = asyncio.Event()
    mixed_ready = asyncio.Event()
    start = asyncio.Event()
    stop_sampler = asyncio.Event()
    if boundary == "edge_browser":
        boundary_task = asyncio.create_task(
            run_edge_browser_boundary(
                base_url,
                cache_state,
                contract,
                requests=requests,
                concurrency=concurrency,
                ready=boundary_ready,
                start=start,
            )
        )
    else:
        boundary_task = asyncio.create_task(
            run_http_boundary(
                boundary,
                base_url,
                cache_state,
                contract,
                requests=requests,
                concurrency=concurrency,
                ready=boundary_ready,
                start=start,
            )
        )
    mixed_task = asyncio.create_task(
        run_mixed_database_load(
            engine,
            contract,
            requests=mixed_requests,
            concurrency=min(concurrency, 24),
            schedule_seed=f"{boundary}:{cache_state}",
            ready=mixed_ready,
            start=start,
        )
    )
    sampler_task = asyncio.create_task(_sample_connection_peak(engine, stop_sampler))
    workload_tasks = (boundary_task, mixed_task)
    try:
        await _wait_for_cell_ready((boundary_ready, mixed_ready), workload_tasks)
        cell_started = time.perf_counter()
        start.set()
        boundary_result, mixed_result = await asyncio.gather(boundary_task, mixed_task)
        cell_elapsed_ms = (time.perf_counter() - cell_started) * 1_000
    except BaseException:
        start.set()
        for task in workload_tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*workload_tasks, return_exceptions=True)
        raise
    finally:
        stop_sampler.set()
    connection_peak, connection_samples = await sampler_task
    boundary_measurement, query_counts, boundary_elapsed_ms = boundary_result
    mixed_samples, traffic_counts, mixed_elapsed_ms = mixed_result
    request_total = boundary_measurement["requests"]
    if not isinstance(request_total, int):
        raise ValueError("boundary measurement returned an invalid request count")
    completed = request_total
    cell_metadata: dict[str, object] = {
        "boundary": boundary,
        "cache_state": cache_state,
        "target_interactions": requests,
        "completed_interactions": completed,
        "mixed_target_interactions": mixed_requests,
        "mixed_completed_interactions": sum(traffic_counts.values()),
        "query_counts": query_counts,
        "traffic_counts": traffic_counts,
        "measurement_elapsed_ms": round(boundary_elapsed_ms, 3),
        "mixed_elapsed_ms": round(mixed_elapsed_ms, 3),
        "cell_elapsed_ms": round(cell_elapsed_ms, 3),
        "achieved_interactions_per_second": round(
            completed / max(boundary_elapsed_ms / 1_000, 0.000_001), 3
        ),
        "connection_observation_samples": connection_samples,
    }
    return boundary_measurement, mixed_samples, cell_metadata, connection_peak


def _memory_bytes() -> int:
    return int(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE"))


def _git_sha() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


def _opennosh_version() -> str:
    return Path("VERSION").read_text().strip()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_boundaries(values: list[str]) -> dict[str, str]:
    boundaries: dict[str, str] = {}
    for value in values:
        boundary, separator, url = value.partition("=")
        if (
            not separator
            or boundary not in BOUNDARY_IDS
            or not url.startswith(("http://", "https://"))
        ):
            raise ValueError(
                "--boundary must be fastapi=URL, same_origin_proxy=URL, or edge_browser=URL"
            )
        boundaries[boundary] = url
    missing = set(BOUNDARY_IDS) - set(boundaries)
    if missing:
        raise ValueError(f"full contract runs require boundary URLs for {sorted(missing)}")
    return boundaries


def _validate_metric_evidence(value: object, *, label: str, integer: bool) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {"value", "source", "observed_at"}:
        raise ValueError(f"{label} evidence must contain value, source, and observed_at")
    measured = value["value"]
    number_is_valid = isinstance(measured, int) if integer else isinstance(measured, (int, float))
    if isinstance(measured, bool) or not number_is_valid or measured < 0:
        raise ValueError(f"{label} evidence value must be a non-negative number")
    if not isinstance(value["source"], str) or not value["source"].strip():
        raise ValueError(f"{label} evidence source must be a non-empty string")
    if not isinstance(value["observed_at"], str):
        raise ValueError(f"{label} evidence observed_at must be an ISO-8601 string")
    try:
        datetime.fromisoformat(value["observed_at"].replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{label} evidence observed_at must be an ISO-8601 string") from error
    return dict(value)


def _load_resource_evidence(path: Path) -> dict[str, object]:
    try:
        document = json.loads(path.read_text())
    except json.JSONDecodeError as error:
        raise ValueError(f"resource evidence is not valid JSON: {path}") from error
    required = {"memory_high_water_bytes", *EXTERNAL_EVIDENCE_METRICS}
    if not isinstance(document, dict) or set(document) != required:
        raise ValueError(f"resource evidence must contain exactly {sorted(required)}")
    memory = document["memory_high_water_bytes"]
    if not isinstance(memory, dict) or set(memory) != MEMORY_EVIDENCE_ROLES:
        raise ValueError(
            "memory evidence must contain exactly postgresql, fastapi, "
            "same_origin_proxy, and edge_browser"
        )
    result: dict[str, object] = {
        "memory_high_water_bytes": {
            role: _validate_metric_evidence(value, label=f"memory/{role}", integer=True)
            for role, value in sorted(memory.items())
        }
    }
    for metric in sorted(EXTERNAL_EVIDENCE_METRICS):
        result[metric] = _validate_metric_evidence(document[metric], label=metric, integer=False)
    return result


def _evidence_value(value: object) -> float:
    if not isinstance(value, dict):
        raise ValueError("resource evidence must be an object")
    measured = value.get("value")
    if not isinstance(measured, (int, float)) or isinstance(measured, bool):
        raise ValueError("resource evidence value must be numeric")
    return float(measured)


def _artifact_manifest(artifact_directory: Path, paths: list[Path]) -> tuple[Path, str]:
    entries = [
        {
            "path": str(path.relative_to(artifact_directory)),
            "sha256": _sha256_file(path),
            "bytes": path.stat().st_size,
        }
        for path in sorted(paths)
    ]
    manifest_path = artifact_directory / "artifact-manifest.json"
    manifest_path.write_bytes(canonical_json_bytes({"schema_version": "1.0.0", "files": entries}))
    return manifest_path, _sha256_file(manifest_path)


async def run(arguments: argparse.Namespace) -> dict[str, Any]:
    started_at = datetime.now(UTC)
    contract = load_contract(arguments.contract)
    profile = contract.profile(arguments.profile)
    boundaries = _parse_boundaries(arguments.boundary)
    external_resources = _load_resource_evidence(arguments.resource_evidence)
    profile_interactions = int(profile["interactions_per_boundary_cache"])
    request_count = arguments.requests or profile_interactions
    diagnostic_override = arguments.requests is not None
    mixed_requests = max(100, request_count // 5)
    artifact_directory = arguments.artifact_directory.resolve()
    artifact_directory.mkdir(parents=True, exist_ok=False)
    engine = create_async_engine(arguments.database_url, pool_pre_ping=True)
    seed_result = None
    try:
        if arguments.seed_database:
            await engine.dispose()
            seed_result = await seed_database(arguments.database_url, contract, arguments.profile)
            engine = create_async_engine(arguments.database_url, pool_pre_ping=True)
        snapshot_id = seed_result.snapshot_id if seed_result else await current_snapshot(engine)
        corpus_sha256 = seed_result.corpus_sha256 if seed_result else arguments.corpus_sha256
        if not corpus_sha256 or len(corpus_sha256) != 64:
            raise ValueError("provide --corpus-sha256 when using a pre-seeded database")

        plan_entries, plan_execution_times = await capture_query_plans(
            engine, snapshot_id, artifact_directory
        )
        measurements = [
            summarize_samples(
                boundary="postgresql",
                cache_state=state,
                workload="first_page_search",
                samples=[Sample(latency_ms=duration)],
            )
            for state, duration in zip(("cold", "warm"), plan_execution_times, strict=True)
        ]
        execution_cells: list[dict[str, object]] = []
        all_mixed_samples: defaultdict[str, list[Sample]] = defaultdict(list)
        observed_connection_peak = 0
        for boundary in BOUNDARY_IDS:
            for cache_state in ("cold", "warm"):
                (
                    boundary_measurement,
                    mixed_samples,
                    cell_metadata,
                    connection_peak,
                ) = await _run_measurement_cell(
                    engine,
                    contract,
                    boundary=boundary,
                    base_url=boundaries[boundary],
                    cache_state=cache_state,
                    requests=request_count,
                    mixed_requests=mixed_requests,
                    concurrency=int(profile["concurrency"]),
                )
                measurements.append(boundary_measurement)
                for operation, operation_samples in mixed_samples.items():
                    all_mixed_samples[operation].extend(operation_samples)
                execution_cells.append(cell_metadata)
                observed_connection_peak = max(observed_connection_peak, connection_peak)
        measurements.extend(
            summarize_samples(
                boundary="postgresql",
                cache_state="warm",
                workload=operation,
                samples=operation_samples,
            )
            for operation, operation_samples in sorted(all_mixed_samples.items())
        )

        database = await database_environment(engine)
        snapshot_resources = await resource_snapshot(engine)
        resources: dict[str, object] = {
            **external_resources,
            "connections_peak": observed_connection_peak,
            "index_size_bytes": int(snapshot_resources["index_size_bytes"]),
        }
        if seed_result is not None:
            resources["index_build_ms"] = {
                "value": seed_result.index_build_ms,
                "source": "seed_database retained-projection build timer",
                "observed_at": started_at.isoformat().replace("+00:00", "Z"),
            }
        environment = {
            "hardware": {
                "machine": platform.machine(),
                "cpu_count": os.cpu_count() or 1,
                "memory_bytes": _memory_bytes(),
                "platform": platform.platform(),
            },
            "database": database,
            "software": {
                "opennosh_version": await asyncio.to_thread(_opennosh_version),
                "git_sha": _git_sha(),
                "python_version": platform.python_version(),
                "httpx_version": httpx.__version__,
            },
            "cache_preparation": contract.document["measurement_boundaries"],
        }
        environment_path = artifact_directory / "environment.json"
        environment_path.write_bytes(canonical_json_bytes(environment))
        resource_evidence_path = artifact_directory / "resource-evidence.json"
        resource_evidence_path.write_bytes(canonical_json_bytes(external_resources))
        query_seed_path = artifact_directory / "query-seed.json"
        query_seed_path.write_bytes(
            canonical_json_bytes(
                {"seed": contract.seed, "query_mix": contract.document["query_mix"]}
            )
        )
        artifact_paths = [environment_path, query_seed_path, resource_evidence_path]
        artifact_paths.extend(artifact_directory / str(entry["path"]) for entry in plan_entries)
        _manifest_path, manifest_sha256 = _artifact_manifest(artifact_directory, artifact_paths)
        gate_evaluation = evaluate_gates(
            measurements, contract.document["gates"], postgresql_miss_streak=0
        )
        gate_evaluation.pop("postgresql_miss_streak")
        capacity_gates = contract.document["gates"]["capacity"]
        connections_peak = resources["connections_peak"]
        if not isinstance(connections_peak, int):
            raise ValueError("connection peak must be an integer")
        if connections_peak > int(
            int(database["connection_ceiling"])
            * float(capacity_gates["max_connection_utilization"])
        ):
            gate_evaluation["failures"].append("database connection utilization exceeded")
        if _evidence_value(resources["projection_lag_p95_ms"]) > float(
            capacity_gates["max_projection_lag_ms"]
        ):
            gate_evaluation["failures"].append("projection lag exceeded")
        if _evidence_value(resources["job_age_p95_ms"]) > float(capacity_gates["max_job_age_ms"]):
            gate_evaluation["failures"].append("job age exceeded")
        if diagnostic_override:
            gate_evaluation["failures"].append(
                "diagnostic --requests override cannot produce passing benchmark evidence"
            )
        gate_evaluation["passed"] = not gate_evaluation["failures"]
        run_id = f"{started_at.strftime('%Y%m%dT%H%M%SZ')}-{arguments.profile}-{_git_sha()[:12]}"
        execution = {
            "mode": "fixed_interactions",
            "profile_interactions_per_cell": profile_interactions,
            "configured_interactions_per_cell": request_count,
            "mixed_interactions_per_cell": mixed_requests,
            "diagnostic_override": diagnostic_override,
            "cells": execution_cells,
        }
        reproducibility: dict[str, object] = {
            "corpus_sha256": corpus_sha256,
            "command": sanitized_command(sys.argv),
            "artifact_manifest_sha256": manifest_sha256,
        }
        if seed_result is not None:
            reproducibility["seed_evidence"] = {
                "records": seed_result.records,
                "observed_distributions": seed_result.observed_distributions,
                "snapshot_counts": seed_result.snapshot_counts,
            }
        result: dict[str, Any] = {
            "schema_version": "1.0.0",
            "run_id": run_id,
            "contract": {
                "id": contract.document["contract_id"],
                "schema_version": contract.document["schema_version"],
                "sha256": contract.sha256,
            },
            "profile": arguments.profile,
            "started_at": started_at.isoformat().replace("+00:00", "Z"),
            "finished_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "environment": environment,
            "execution": execution,
            "query_seed": contract.seed,
            "measurements": measurements,
            "resources": resources,
            "query_plans": plan_entries,
            "gate_evaluation": gate_evaluation,
            "reproducibility": reproducibility,
        }
        validate_result_bundle(result, contract, artifact_directory)
        (artifact_directory / "result.json").write_bytes(canonical_json_bytes(result))
        return result
    finally:
        await engine.dispose()


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT_PATH)
    value.add_argument("--profile", choices=("launch-reference", "10x", "100x"), required=True)
    value.add_argument("--database-url", required=True)
    value.add_argument(
        "--boundary",
        action="append",
        default=[],
        help="repeat for fastapi=URL, same_origin_proxy=URL, or edge_browser=URL",
    )
    value.add_argument("--artifact-directory", type=Path, required=True)
    value.add_argument(
        "--resource-evidence",
        type=Path,
        required=True,
        help="JSON evidence for role memory HWM, index build, job age, and projection lag",
    )
    value.add_argument(
        "--requests",
        type=int,
        help=(
            "diagnostic interactions per boundary/cache cell; diagnostics always fail release gates"
        ),
    )
    value.add_argument("--seed-database", action="store_true")
    value.add_argument("--corpus-sha256")
    return value


def main() -> int:
    arguments = parser().parse_args()
    if arguments.requests is not None and arguments.requests < 1:
        raise SystemExit("--requests must be positive")
    result = asyncio.run(run(arguments))
    print(json.dumps(result["gate_evaluation"], sort_keys=True))
    return 0 if result["gate_evaluation"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
