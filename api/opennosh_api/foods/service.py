from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import IntEnum
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.auth.dependencies import CurrentSession
from opennosh_api.federation.search import (
    ActiveFederationProjection,
    active_federation_projection,
    append_federation_projection,
    federation_food_detail,
)
from opennosh_api.foods.cursors import (
    SEARCH_CURSOR_SCHEMA_VERSION,
    SEARCH_RANKING_VERSION,
    SearchCursorError,
    SearchCursorFailure,
    SearchCursorKeyRing,
    SearchCursorPayload,
    search_fingerprint,
)
from opennosh_api.foods.schemas import (
    CustomFoodCreate,
    CustomFoodResponse,
    FoodAttribution,
    FoodDetail,
    FoodSearchItem,
    FoodSearchReleaseSet,
    FoodSearchResponse,
    FoodSource,
)
from opennosh_api.models import FoodCommunity, FoodCustom, FoodReference
from opennosh_api.nutrition import HouseholdPortion

SEARCH_QUERY_MIN_LENGTH = 2
SEARCH_QUERY_MAX_LENGTH = 100
SEARCH_LIMIT_DEFAULT = 20
SEARCH_LIMIT_MAX = 50
SEARCH_PLAN_MAX_EXECUTION_MS = 100.0

_LOCALE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
_PACK_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SEARCH_PACK_FILTER_MAX = 20


class RankingTier(IntEnum):
    EXACT_COMMUNITY_SLUG = 0
    LOCALE_COMMUNITY = 1
    USDA_GENERIC = 2
    OTHER_COMMUNITY = 3


def normalize_search_query(value: str) -> str:
    normalized = " ".join(value.split())
    if not SEARCH_QUERY_MIN_LENGTH <= len(normalized) <= SEARCH_QUERY_MAX_LENGTH:
        raise ValueError(
            f"q must contain between {SEARCH_QUERY_MIN_LENGTH} and "
            f"{SEARCH_QUERY_MAX_LENGTH} non-whitespace characters"
        )
    if not any(character.isalnum() for character in normalized):
        raise ValueError("q must contain at least one letter or number")
    if "\x00" in normalized:
        raise ValueError("q cannot contain NUL characters")
    return normalized


def normalize_locale(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not _LOCALE.fullmatch(normalized):
        raise ValueError("locale must be a valid BCP 47 language tag")
    return normalized.lower()


def normalize_pack_ids(values: list[str] | None) -> tuple[str, ...]:
    if not values:
        return ()
    normalized = tuple(sorted(set(value.strip() for value in values)))
    if len(normalized) > SEARCH_PACK_FILTER_MAX:
        raise ValueError(f"pack can contain at most {SEARCH_PACK_FILTER_MAX} distinct IDs")
    if any(not 1 <= len(value) <= 80 or _PACK_ID.fullmatch(value) is None for value in normalized):
        raise ValueError("pack values must be canonical food-pack IDs")
    return normalized


_SNAPSHOT_SEARCH_VECTOR = """
to_tsvector(
    'simple'::regconfig,
    (((((coalesce(food.source_id, '') || ' ') || coalesce(food.name, '')) || ' ') ||
    coalesce(food.name_local, '')) || ' ') || coalesce(food.category, '')
)
""".strip()

FOOD_SEARCH_SQL = f"""
WITH ranked_matches AS (
    SELECT
        food.source,
        food.source_id,
        food.source_record_id,
        food.name,
        food.name_local,
        food.category,
        food.license,
        food.source_uri,
        food.source_license,
        food.contributed_by,
        food.pack_id,
        food.pack_version,
        food.provenance,
        food.release_version,
        food.release_digest,
        food.equivalence_group_id,
        food.variant_id,
        food.conflict,
        food.variant_count,
        CASE
            WHEN food.source = 'community'
                 AND food.source_id = CAST(:slug_query AS text)
                THEN {int(RankingTier.EXACT_COMMUNITY_SLUG)}
            WHEN food.source IN ('community', 'federation')
                 AND CAST(:locale AS text) IS NOT NULL
                 AND lower(food.locale) = CAST(:locale AS text)
                THEN {int(RankingTier.LOCALE_COMMUNITY)}
            WHEN food.source = 'usda'
                THEN {int(RankingTier.USDA_GENERIC)}
            ELSE {int(RankingTier.OTHER_COMMUNITY)}
        END AS ranking_tier,
        greatest(
            similarity(food.source_id, CAST(:query AS text)),
            similarity(food.name, CAST(:query AS text)),
            similarity(coalesce(food.name_local, ''), CAST(:query AS text)),
            ts_rank_cd(
                {_SNAPSHOT_SEARCH_VECTOR},
                plainto_tsquery('simple'::regconfig, CAST(:query AS text))
            )
        ) AS match_score,
        lower(food.name) AS normalized_name
    FROM food_search_snapshot_items AS food
    WHERE food.snapshot_id = CAST(:snapshot_id AS uuid)
      AND (
          CAST(:source_filter AS text) IS NULL
          OR food.source = CAST(:source_filter AS text)
      )
      AND (
          food.source_id = CAST(:slug_query AS text)
          OR {_SNAPSHOT_SEARCH_VECTOR} @@
             plainto_tsquery('simple'::regconfig, CAST(:query AS text))
          OR food.source_id % CAST(:query AS text)
          OR food.name % CAST(:query AS text)
          OR food.name_local % CAST(:query AS text)
      )
)
SELECT *
FROM ranked_matches
WHERE
    CAST(:has_cursor AS boolean) IS FALSE
    OR ranking_tier > CAST(:after_rank AS integer)
    OR (
        ranking_tier = CAST(:after_rank AS integer)
        AND match_score < CAST(:after_score AS double precision)
    )
    OR (
        ranking_tier = CAST(:after_rank AS integer)
        AND match_score = CAST(:after_score AS double precision)
        AND normalized_name > CAST(:after_name AS text)
    )
    OR (
        ranking_tier = CAST(:after_rank AS integer)
        AND match_score = CAST(:after_score AS double precision)
        AND normalized_name = CAST(:after_name AS text)
        AND source > CAST(:after_source AS text)
    )
    OR (
        ranking_tier = CAST(:after_rank AS integer)
        AND match_score = CAST(:after_score AS double precision)
        AND normalized_name = CAST(:after_name AS text)
        AND source = CAST(:after_source AS text)
        AND source_id > CAST(:after_source_id AS text)
    )
ORDER BY ranking_tier, match_score DESC, normalized_name, source, source_id
LIMIT :fetch_limit
"""

FOOD_SEARCH_SNAPSHOT_INSERT_SQL = """
INSERT INTO food_search_snapshot_items (
    snapshot_id, source, source_id, source_record_id, name, name_local, locale, category, license,
    source_uri, source_license, contributed_by, pack_id, pack_version, provenance
)
SELECT
    CAST(:snapshot_id AS uuid), 'community', food.slug, food.slug, food.name, food.name_local,
    lower(food.locale), food.category, food.pack_license, food.source_uri,
    food.source_license, food.contributed_by, food.pack_id, food.pack_version,
    food.provenance
FROM foods_community AS food
WHERE CAST(:has_pack_filter AS boolean) IS FALSE
   OR food.pack_id = ANY(CAST(:selected_pack_ids AS text[]))
UNION ALL
SELECT
    CAST(:snapshot_id AS uuid), 'usda', food.fdc_id, food.fdc_id, food.description, NULL, NULL,
    food.food_category, food.license, NULL, food.license, NULL, NULL, NULL,
    'government_database'
FROM foods_reference AS food
WHERE CAST(:has_pack_filter AS boolean) IS FALSE
"""


class FoodSearchTimeoutError(RuntimeError):
    """The database stopped a food search after its configured time budget."""


class FoodSearchProjectionBusyError(RuntimeError):
    """Another request is preparing the first retained search projection."""


@dataclass(frozen=True)
class SearchSnapshot:
    snapshot_id: UUID
    expires_at: datetime
    federation_checkpoint_id: UUID | None = None
    release_set_digest: str | None = None
    selected_pack_ids: tuple[str, ...] = ()
    stale: bool = False


def _attribution(row: RowMapping) -> FoodAttribution:
    source = FoodSource(row["source"])
    return FoodAttribution(
        source=source,
        license=row["license"],
        source_uri=row["source_uri"],
        source_license=row["source_license"],
        contributed_by=row["contributed_by"],
        pack_id=row["pack_id"],
        pack_version=row["pack_version"],
        provenance=row["provenance"],
        release_version=row["release_version"],
        release_digest=row["release_digest"],
    )


def _search_item(row: RowMapping) -> FoodSearchItem:
    source = FoodSource(row["source"])
    source_id = str(row["source_id"])
    return FoodSearchItem(
        id=f"{source.value}:{source_id}",
        source=source,
        source_id=source_id,
        name=row["name"],
        name_local=row["name_local"],
        category=row["category"],
        attribution=_attribution(row),
        equivalence_group_id=row["equivalence_group_id"],
        variant_id=row["variant_id"],
        conflict=bool(row["conflict"]),
        variant_count=int(row["variant_count"]),
    )


def _cursor_position(row: RowMapping) -> tuple[int, str, str, str, str]:
    return (
        int(row["ranking_tier"]),
        format(float(row["match_score"]), ".17g"),
        str(row["normalized_name"]),
        str(row["source"]),
        str(row["source_id"]),
    )


async def _existing_snapshot(
    database: AsyncSession,
    *,
    snapshot_id: UUID,
    now: datetime,
    active_projection: ActiveFederationProjection | None = None,
) -> SearchSnapshot:
    row = (
        (
            await database.execute(
                text(
                    """
                SELECT id, expires_at, federation_checkpoint_id,
                       release_set_digest, selected_pack_ids
                FROM food_search_snapshots
                WHERE id = CAST(:snapshot_id AS uuid)
                  AND ranking_version = :ranking_version
                FOR KEY SHARE
                """
                ),
                {
                    "snapshot_id": snapshot_id,
                    "ranking_version": SEARCH_RANKING_VERSION,
                },
            )
        )
        .mappings()
        .first()
    )
    if row is None or row["expires_at"] <= now:
        raise SearchCursorError(
            SearchCursorFailure.RESTART,
            "The retained search snapshot is no longer available.",
        )
    checkpoint_id = row["federation_checkpoint_id"]
    release_set_digest = row["release_set_digest"]
    stale = (
        (active_projection is None and checkpoint_id is not None)
        or (active_projection is not None and active_projection.stale)
        or (
            active_projection is not None
            and (
                checkpoint_id != active_projection.checkpoint_id
                or release_set_digest != active_projection.release_set_digest
            )
        )
    )
    return SearchSnapshot(
        snapshot_id=row["id"],
        expires_at=row["expires_at"],
        federation_checkpoint_id=checkpoint_id,
        release_set_digest=release_set_digest,
        selected_pack_ids=tuple(row["selected_pack_ids"]),
        stale=stale,
    )


async def _latest_snapshot(
    database: AsyncSession,
    *,
    now: datetime,
    fresh_after: datetime | None,
    active_projection: ActiveFederationProjection | None = None,
    selected_pack_ids: tuple[str, ...] = (),
) -> SearchSnapshot | None:
    freshness = "AND created_at >= :fresh_after" if fresh_after is not None else ""
    row = (
        (
            await database.execute(
                text(
                    f"""
                SELECT id, expires_at, federation_checkpoint_id,
                       release_set_digest, selected_pack_ids
                FROM food_search_snapshots
                WHERE ranking_version = :ranking_version
                  AND expires_at > :now
                  AND federation_checkpoint_id IS NOT DISTINCT FROM
                      CAST(:federation_checkpoint_id AS uuid)
                  AND release_set_digest IS NOT DISTINCT FROM :release_set_digest
                  AND selected_pack_ids = CAST(:selected_pack_ids AS jsonb)
                  AND (
                      CAST(:quarantine_cutoff AS timestamptz) IS NULL
                      OR created_at >= CAST(:quarantine_cutoff AS timestamptz)
                  )
                  {freshness}
                ORDER BY created_at DESC
                LIMIT 1
                FOR KEY SHARE
                """
                ),
                {
                    "ranking_version": SEARCH_RANKING_VERSION,
                    "fresh_after": fresh_after,
                    "now": now,
                    "federation_checkpoint_id": (
                        active_projection.checkpoint_id
                        if active_projection is not None
                        else None
                    ),
                    "release_set_digest": (
                        active_projection.release_set_digest
                        if active_projection is not None
                        else None
                    ),
                    "selected_pack_ids": json.dumps(selected_pack_ids),
                    "quarantine_cutoff": (
                        active_projection.quarantine_cutoff
                        if active_projection is not None
                        else None
                    ),
                },
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    return SearchSnapshot(
        snapshot_id=row["id"],
        expires_at=row["expires_at"],
        federation_checkpoint_id=row["federation_checkpoint_id"],
        release_set_digest=row["release_set_digest"],
        selected_pack_ids=tuple(row["selected_pack_ids"]),
        stale=active_projection.stale if active_projection is not None else False,
    )


async def _fresh_snapshot(
    database: AsyncSession,
    *,
    now: datetime,
    refresh_seconds: int,
    retention_seconds: int,
    active_projection: ActiveFederationProjection | None = None,
    selected_pack_ids: tuple[str, ...] = (),
) -> SearchSnapshot:
    fresh_after = now - timedelta(seconds=refresh_seconds)
    snapshot = await _latest_snapshot(
        database,
        now=now,
        fresh_after=fresh_after,
        active_projection=active_projection,
        selected_pack_ids=selected_pack_ids,
    )
    if snapshot is not None:
        return snapshot

    lock_acquired = bool(
        (
            await database.execute(
                text(
                    "SELECT pg_try_advisory_xact_lock(hashtext('opennosh.food-search-projection'))"
                )
            )
        ).scalar_one()
    )
    if not lock_acquired:
        retained = await _latest_snapshot(
            database,
            now=now,
            fresh_after=None,
            active_projection=active_projection,
            selected_pack_ids=selected_pack_ids,
        )
        if retained is not None:
            return retained
        raise FoodSearchProjectionBusyError

    snapshot = await _latest_snapshot(
        database,
        now=now,
        fresh_after=fresh_after,
        active_projection=active_projection,
        selected_pack_ids=selected_pack_ids,
    )
    if snapshot is not None:
        return snapshot

    snapshot_id = uuid4()
    expires_at = now + timedelta(seconds=retention_seconds)
    await database.execute(
        text("DELETE FROM food_search_snapshots WHERE expires_at <= :now"),
        {"now": now},
    )
    await database.execute(
        text(
            """
            INSERT INTO food_search_snapshots (
                id, ranking_version, created_at, expires_at,
                federation_checkpoint_id, release_set_digest, selected_pack_ids
            ) VALUES (
                CAST(:snapshot_id AS uuid), :ranking_version, :created_at, :expires_at,
                CAST(:federation_checkpoint_id AS uuid), :release_set_digest,
                CAST(:selected_pack_ids AS jsonb)
            )
            """
        ),
        {
            "snapshot_id": snapshot_id,
            "ranking_version": SEARCH_RANKING_VERSION,
            "created_at": now,
            "expires_at": expires_at,
            "federation_checkpoint_id": (
                active_projection.checkpoint_id if active_projection is not None else None
            ),
            "release_set_digest": (
                active_projection.release_set_digest if active_projection is not None else None
            ),
            "selected_pack_ids": json.dumps(selected_pack_ids),
        },
    )
    await database.execute(
        text(FOOD_SEARCH_SNAPSHOT_INSERT_SQL),
        {
            "snapshot_id": snapshot_id,
            "has_pack_filter": bool(selected_pack_ids),
            "selected_pack_ids": list(selected_pack_ids),
        },
    )
    if active_projection is not None:
        await append_federation_projection(
            database,
            snapshot_id=snapshot_id,
            projection=active_projection,
            selected_pack_ids=selected_pack_ids,
        )
    await database.commit()
    return SearchSnapshot(
        snapshot_id=snapshot_id,
        expires_at=expires_at,
        federation_checkpoint_id=(
            active_projection.checkpoint_id if active_projection is not None else None
        ),
        release_set_digest=(
            active_projection.release_set_digest if active_projection is not None else None
        ),
        selected_pack_ids=selected_pack_ids,
        stale=active_projection.stale if active_projection is not None else False,
    )


async def search_foods(
    database: AsyncSession,
    *,
    query: str,
    locale: str | None,
    source: FoodSource | None,
    limit: int,
    cursor: str | None,
    key_ring: SearchCursorKeyRing,
    cursor_lifetime_seconds: int,
    snapshot_refresh_seconds: int,
    snapshot_retention_seconds: int,
    snapshot_build_timeout_ms: int,
    statement_timeout_ms: int,
    federation_enabled: bool = False,
    selected_pack_ids: tuple[str, ...] = (),
    now: datetime | None = None,
) -> FoodSearchResponse:
    current_time = now or datetime.now(UTC)
    federation_requested = federation_enabled and (
        source is FoodSource.FEDERATION or bool(selected_pack_ids)
    )
    active_projection = (
        await active_federation_projection(database) if federation_requested else None
    )
    fingerprint = search_fingerprint(
        query=query,
        locale=locale,
        source=source.value if source is not None else None,
        pack_ids=selected_pack_ids,
        federation_enabled=federation_requested,
    )
    payload: SearchCursorPayload | None = None
    if cursor is not None:
        payload = key_ring.decode(cursor, now=int(current_time.timestamp()))
        if payload.fp != fingerprint:
            raise SearchCursorError(
                SearchCursorFailure.RESTART,
                "This search cursor belongs to different search terms or filters.",
            )
        if payload.rv != SEARCH_RANKING_VERSION or payload.size != limit:
            raise SearchCursorError(
                SearchCursorFailure.RESTART,
                "This search cursor no longer matches the ranking or page-size policy.",
            )
        snapshot = await _existing_snapshot(
            database,
            snapshot_id=payload.sid,
            now=current_time,
            active_projection=active_projection,
        )
        if (
            snapshot.selected_pack_ids != selected_pack_ids
            or payload.rs != snapshot.release_set_digest
        ):
            raise SearchCursorError(
                SearchCursorFailure.RESTART,
                "This search cursor belongs to a different pack release set.",
            )
    else:
        try:
            async with asyncio.timeout(snapshot_build_timeout_ms / 1_000):
                await database.execute(
                    text("SELECT set_config('statement_timeout', :timeout, true)"),
                    {"timeout": f"{snapshot_build_timeout_ms}ms"},
                )
                snapshot = await _fresh_snapshot(
                    database,
                    now=current_time,
                    refresh_seconds=snapshot_refresh_seconds,
                    retention_seconds=snapshot_retention_seconds,
                    active_projection=active_projection,
                    selected_pack_ids=selected_pack_ids,
                )
        except TimeoutError as error:
            await database.rollback()
            raise FoodSearchProjectionBusyError from error
        except DBAPIError as error:
            if getattr(error.orig, "sqlstate", None) != "57014":
                raise
            await database.rollback()
            raise FoodSearchProjectionBusyError from error

    after = payload.pos if payload is not None else (0, "0", "", "", "")
    try:
        await database.execute(
            text("SELECT set_config('statement_timeout', :timeout, true)"),
            {"timeout": f"{statement_timeout_ms}ms"},
        )
        rows = list(
            (
                await database.execute(
                    text(FOOD_SEARCH_SQL),
                    {
                        "query": query,
                        "slug_query": query.casefold(),
                        "locale": locale,
                        "source_filter": source.value if source is not None else None,
                        "snapshot_id": snapshot.snapshot_id,
                        "has_cursor": payload is not None,
                        "after_rank": after[0],
                        "after_score": float(after[1]),
                        "after_name": after[2],
                        "after_source": after[3],
                        "after_source_id": after[4],
                        "fetch_limit": limit + 1,
                    },
                )
            ).mappings()
        )
    except DBAPIError as error:
        if getattr(error.orig, "sqlstate", None) != "57014":
            raise
        await database.rollback()
        raise FoodSearchTimeoutError from error

    has_more = len(rows) > limit
    visible_rows = rows[:limit]
    next_cursor: str | None = None
    if has_more and visible_rows:
        cursor_expires_at = min(
            snapshot.expires_at,
            current_time + timedelta(seconds=cursor_lifetime_seconds),
        )
        next_cursor = key_ring.encode(
            SearchCursorPayload(
                v=SEARCH_CURSOR_SCHEMA_VERSION,
                sid=snapshot.snapshot_id,
                fp=fingerprint,
                rv=SEARCH_RANKING_VERSION,
                rs=snapshot.release_set_digest,
                pos=_cursor_position(visible_rows[-1]),
                size=limit,
                exp=int(cursor_expires_at.timestamp()),
            )
        )
    return FoodSearchResponse(
        items=[_search_item(row) for row in visible_rows],
        limit=limit,
        has_more=has_more,
        next_cursor=next_cursor,
        snapshot_id=snapshot.snapshot_id,
        snapshot_expires_at=snapshot.expires_at,
        release_set=FoodSearchReleaseSet(
            enabled=federation_requested,
            checkpoint_id=snapshot.federation_checkpoint_id,
            digest=snapshot.release_set_digest,
            selected_pack_ids=list(snapshot.selected_pack_ids),
            stale=snapshot.stale,
        ),
    )


def _reference_detail(row: FoodReference) -> FoodDetail:
    source_id = row.fdc_id
    return FoodDetail(
        id=f"usda:{source_id}",
        source=FoodSource.USDA,
        source_id=source_id,
        name=row.description,
        category=row.food_category,
        attribution=FoodAttribution(
            source=FoodSource.USDA,
            license=row.license,
            source_license=row.license,
            provenance="government_database",
        ),
        nutrients=row.nutrients_json,
        portions=[HouseholdPortion.model_validate(value) for value in row.portions_json],
    )


def _community_detail(row: FoodCommunity) -> FoodDetail:
    source_id = row.slug
    return FoodDetail(
        id=f"community:{source_id}",
        source=FoodSource.COMMUNITY,
        source_id=source_id,
        name=row.name,
        name_local=row.name_local,
        category=row.category,
        attribution=FoodAttribution(
            source=FoodSource.COMMUNITY,
            license=row.pack_license,
            source_uri=row.source_uri,
            source_license=row.source_license,
            contributed_by=row.contributed_by,
            pack_id=row.pack_id,
            pack_version=row.pack_version,
            provenance=row.provenance,
        ),
        nutrients=row.nutrients_json,
        portions=[HouseholdPortion.model_validate(value) for value in row.portions_json],
    )


async def get_food_detail(
    database: AsyncSession, source: FoodSource, source_id: str
) -> FoodDetail | None:
    if source is FoodSource.USDA:
        reference = await database.scalar(
            select(FoodReference).where(FoodReference.fdc_id == source_id)
        )
        return _reference_detail(reference) if reference is not None else None
    if source is FoodSource.COMMUNITY:
        community = await database.scalar(
            select(FoodCommunity).where(FoodCommunity.slug == source_id)
        )
        return _community_detail(community) if community is not None else None
    federation_row = await federation_food_detail(database, source_id)
    if federation_row is None:
        return None
    item = _search_item(federation_row)
    return FoodDetail(
        **item.model_dump(),
        nutrients=federation_row["nutrients_json"],
        portions=[
            HouseholdPortion.model_validate(value)
            for value in federation_row["portions_json"]
        ],
    )


async def create_custom_food(
    database: AsyncSession,
    payload: CustomFoodCreate,
    current: CurrentSession,
) -> CustomFoodResponse:
    row = FoodCustom(
        user_id=current.user_id,
        name=payload.name,
        nutrients_json=payload.nutrients.model_dump(mode="json"),
        portions_json=[portion.model_dump(mode="json") for portion in payload.portions],
    )
    database.add(row)
    await database.commit()
    await database.refresh(row)
    return CustomFoodResponse(
        id=row.id,
        source_id=str(row.id),
        name=row.name,
        nutrients=row.nutrients_json,
        portions=[HouseholdPortion.model_validate(value) for value in row.portions_json],
    )
