"""Benchmark-only database seeding, mixed writes, plans, and resource probes."""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit
from uuid import UUID, uuid4

from opennosh_api.foods.service import (  # type: ignore[import-untyped]
    FOOD_SEARCH_SNAPSHOT_INSERT_SQL,
    FOOD_SEARCH_SQL,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from benchmarks.performance.contract import BenchmarkContract, canonical_json_bytes
from benchmarks.performance.corpus import CorpusRecord, generate_records


@dataclass(frozen=True)
class SeedResult:
    corpus_sha256: str
    records: int
    index_build_ms: float
    snapshot_id: UUID
    observed_distributions: dict[str, dict[str, int]]
    snapshot_counts: dict[str, int]


def ensure_benchmark_database(database_url: str) -> None:
    database_name = unquote(urlsplit(database_url).path.rsplit("/", 1)[-1])
    if "benchmark" not in database_name.casefold():
        raise ValueError(
            "refusing to replace data outside a database whose name contains 'benchmark'"
        )


def _record_parameters(record: CorpusRecord) -> dict[str, object]:
    return {
        "source": record.source,
        "source_id": record.source_id,
        "pack_id": record.pack_id,
        "release_id": record.release_id,
        "locale": record.locale,
        "name": record.name,
        "name_local": record.name_local,
        "category": record.category,
        "provenance": record.provenance,
        "nutrients": json.dumps(record.nutrients, sort_keys=True),
        "script": record.script,
        "name_length": record.name_length,
        "variant": record.variant,
        "duplicate_cluster": record.duplicate_cluster,
        "duplicate_cluster_id": record.duplicate_cluster_id,
        "missing_field": record.missing_field,
        "license": record.license,
        "evidence": record.evidence,
        "projection_state": record.projection_state,
        "release_age": record.release_age,
    }


async def _seed_batch(connection: AsyncConnection, records: list[CorpusRecord]) -> None:
    parameters = [_record_parameters(record) for record in records]
    await connection.execute(
        text(
            """
            INSERT INTO benchmark_corpus_metadata (
                source, source_id, locale_script, name_length, variant,
                duplicate_cluster, duplicate_cluster_id, missing_field, license,
                evidence, projection_state, release_age
            ) VALUES (
                :source, :source_id, :locale || '|' || :script, :name_length, :variant,
                :duplicate_cluster, :duplicate_cluster_id, :missing_field, :license,
                :evidence, :projection_state, :release_age
            )
            """
        ),
        parameters,
    )
    reference = [item for item in parameters if item["source"] == "usda"]
    community = [item for item in parameters if item["source"] == "community"]
    if reference:
        await connection.execute(
            text(
                """
                INSERT INTO foods_reference (
                    fdc_id, description, food_category, nutrients_json, portions_json
                ) VALUES (
                    :source_id, :name, :category, CAST(:nutrients AS jsonb), '[]'::jsonb
                )
                """
            ),
            reference,
        )
    if community:
        await connection.execute(
            text(
                """
                INSERT INTO foods_community (
                    pack_id, pack_version, slug, name, name_local, locale, category,
                    provenance, source_uri, source_license, nutrients_json, portions_json,
                    contributed_by
                ) VALUES (
                    :pack_id, :release_id, :source_id, :name, :name_local, :locale, :category,
                    :provenance, NULL, 'contributor-original', CAST(:nutrients AS jsonb),
                    '[]'::jsonb, 'opennosh-benchmark'
                )
                """
            ),
            community,
        )


async def seed_database(
    database_url: str,
    contract: BenchmarkContract,
    profile_id: str,
    *,
    batch_size: int = 5_000,
) -> SeedResult:
    ensure_benchmark_database(database_url)
    engine = create_async_engine(database_url, pool_pre_ping=True)
    digest = hashlib.sha256()
    count = 0
    summary: defaultdict[str, Counter[str]] = defaultdict(Counter)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("TRUNCATE food_search_snapshots, foods_reference, foods_community CASCADE")
            )
            await connection.execute(
                text(
                    """
                    CREATE TEMP TABLE benchmark_corpus_metadata (
                        source text NOT NULL,
                        source_id text NOT NULL,
                        locale_script text NOT NULL,
                        name_length text NOT NULL,
                        variant text NOT NULL,
                        duplicate_cluster text NOT NULL,
                        duplicate_cluster_id text NOT NULL,
                        missing_field text NOT NULL,
                        license text NOT NULL,
                        evidence text NOT NULL,
                        projection_state text NOT NULL,
                        release_age text NOT NULL,
                        PRIMARY KEY (source, source_id)
                    ) ON COMMIT DROP
                    """
                )
            )
            batch: list[CorpusRecord] = []
            for record in generate_records(contract, profile_id):
                digest.update(record.json_bytes())
                batch.append(record)
                count += 1
                for field in (
                    "source",
                    "name_length",
                    "variant",
                    "duplicate_cluster",
                    "missing_field",
                    "license",
                    "evidence",
                    "projection_state",
                    "release_age",
                ):
                    summary[field][str(getattr(record, field))] += 1
                summary["locale_script"][f"{record.locale}|{record.script}"] += 1
                if len(batch) >= batch_size:
                    await _seed_batch(connection, batch)
                    batch.clear()
            if batch:
                await _seed_batch(connection, batch)
            observed = {
                dimension: dict(sorted(values.items()))
                for dimension, values in sorted(summary.items())
            }
            expected = {
                dimension: {
                    label: count * int(weight) // 10_000 for label, weight in distribution.items()
                }
                for dimension, distribution in contract.document["distributions"].items()
            }
            if observed != expected:
                raise ValueError("generated corpus does not match the pinned distributions")

            started = time.perf_counter()
            snapshot_counts: dict[str, int] = {}
            snapshot_ids: dict[str, UUID] = {}
            for snapshot_state, excluded in (
                ("retained_previous", ("rebuild_pending",)),
                ("retained_active", ("retained_previous", "rebuild_pending")),
            ):
                candidate = uuid4()
                snapshot_ids[snapshot_state] = candidate
                await connection.execute(
                    text(
                        """
                        INSERT INTO food_search_snapshots (
                            id, ranking_version, created_at, expires_at
                        ) VALUES (
                            CAST(:snapshot_id AS uuid), 1,
                            now() + (:offset_minutes * INTERVAL '1 minute'),
                            now() + INTERVAL '30 minutes'
                        )
                        """
                    ),
                    {
                        "snapshot_id": candidate,
                        "offset_minutes": -1 if snapshot_state == "retained_previous" else 0,
                    },
                )
                await connection.execute(
                    text(FOOD_SEARCH_SNAPSHOT_INSERT_SQL), {"snapshot_id": candidate}
                )
                await connection.execute(
                    text(
                        """
                        DELETE FROM food_search_snapshot_items AS item
                        USING benchmark_corpus_metadata AS metadata
                        WHERE item.snapshot_id = CAST(:snapshot_id AS uuid)
                          AND item.source = metadata.source
                          AND item.source_id = metadata.source_id
                          AND metadata.projection_state = ANY(CAST(:excluded AS text[]))
                        """
                    ),
                    {"snapshot_id": candidate, "excluded": list(excluded)},
                )
                snapshot_counts[snapshot_state] = int(
                    await connection.scalar(
                        text(
                            """
                            SELECT count(*) FROM food_search_snapshot_items
                            WHERE snapshot_id = CAST(:snapshot_id AS uuid)
                            """
                        ),
                        {"snapshot_id": candidate},
                    )
                )
            snapshot_id = snapshot_ids["retained_active"]
            if snapshot_counts != {
                "retained_previous": expected["projection_state"]["retained_active"]
                + expected["projection_state"]["retained_previous"],
                "retained_active": expected["projection_state"]["retained_active"],
            }:
                raise ValueError("retained snapshot membership does not match projection states")
            await connection.execute(text("ANALYZE food_search_snapshot_items"))
            index_build_ms = (time.perf_counter() - started) * 1_000
        return SeedResult(
            corpus_sha256=digest.hexdigest(),
            records=count,
            index_build_ms=index_build_ms,
            snapshot_id=snapshot_id,
            observed_distributions=observed,
            snapshot_counts=snapshot_counts,
        )
    finally:
        await engine.dispose()


def _plan_parameters(snapshot_id: UUID) -> dict[str, object]:
    return {
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
    }


async def capture_query_plans(
    engine: AsyncEngine,
    snapshot_id: UUID,
    artifact_directory: Path,
) -> tuple[list[dict[str, object]], list[float]]:
    plans: list[dict[str, object]] = []
    execution_times: list[float] = []
    plan_directory = artifact_directory / "plans"
    plan_directory.mkdir(parents=True, exist_ok=True)
    statement = text(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {FOOD_SEARCH_SQL}")
    for cache_state in ("cold", "warm"):
        async with engine.connect() as connection:
            if cache_state == "cold":
                await connection.execute(text("DISCARD PLANS"))
                await connection.commit()
            else:
                await connection.scalar(statement, _plan_parameters(snapshot_id))
            payload = await connection.scalar(statement, _plan_parameters(snapshot_id))
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, list) or not payload:
            raise ValueError("PostgreSQL returned an invalid JSON EXPLAIN plan")
        execution_times.append(float(payload[0]["Execution Time"]))
        encoded = canonical_json_bytes(payload)
        path = plan_directory / f"first-page-{cache_state}.json"
        path.write_bytes(encoded)
        plans.append(
            {
                "query_id": "first_page",
                "cache_state": cache_state,
                "path": str(path.relative_to(artifact_directory)),
                "sha256": hashlib.sha256(encoded).hexdigest(),
            }
        )
    return plans, execution_times


async def database_environment(engine: AsyncEngine) -> dict[str, Any]:
    settings = (
        "shared_buffers",
        "effective_cache_size",
        "work_mem",
        "maintenance_work_mem",
        "max_connections",
        "random_page_cost",
        "effective_io_concurrency",
    )
    async with engine.connect() as connection:
        version = str(await connection.scalar(text("SHOW server_version")))
        configuration = {
            setting: str(await connection.scalar(text(f"SHOW {setting}"))) for setting in settings
        }
    return {
        "postgresql_version": version,
        "configuration": configuration,
        "connection_ceiling": int(configuration["max_connections"]),
    }


async def resource_snapshot(engine: AsyncEngine) -> dict[str, int | float]:
    async with engine.connect() as connection:
        connections = int(
            await connection.scalar(
                text("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()")
            )
        )
        index_size = int(
            await connection.scalar(
                text(
                    """
                    SELECT coalesce(sum(pg_relation_size(indexrelid)), 0)
                    FROM pg_index
                    WHERE indrelid IN (
                        'food_search_snapshot_items'::regclass,
                        'foods_community'::regclass,
                        'foods_reference'::regclass
                    )
                    """
                )
            )
        )
    return {"connections_peak": connections, "index_size_bytes": index_size}


async def current_snapshot(engine: AsyncEngine) -> UUID:
    async with engine.connect() as connection:
        snapshot_id = await connection.scalar(
            text("SELECT id FROM food_search_snapshots ORDER BY created_at DESC LIMIT 1")
        )
    if not isinstance(snapshot_id, UUID):
        raise ValueError("benchmark database does not contain a retained search snapshot")
    return snapshot_id
