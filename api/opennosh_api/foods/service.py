from __future__ import annotations

import re
from enum import IntEnum

from sqlalchemy import select, text
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.auth.dependencies import CurrentSession
from opennosh_api.foods.schemas import (
    CustomFoodCreate,
    CustomFoodResponse,
    FoodAttribution,
    FoodDetail,
    FoodSearchItem,
    FoodSearchResponse,
    FoodSource,
)
from opennosh_api.models import FoodCommunity, FoodCustom, FoodReference
from opennosh_api.nutrition import HouseholdPortion

SEARCH_QUERY_MIN_LENGTH = 2
SEARCH_QUERY_MAX_LENGTH = 100
SEARCH_LIMIT_DEFAULT = 20
SEARCH_LIMIT_MAX = 50
SEARCH_OFFSET_MAX = 10_000
SEARCH_PLAN_MAX_EXECUTION_MS = 100.0

_LOCALE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")


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


_COMMUNITY_SEARCH_VECTOR = """
to_tsvector(
    'simple'::regconfig,
    (((((coalesce(food.slug, ''::character varying)::text || ' '::text) ||
    coalesce(food.name, ''::character varying)::text) || ' '::text) ||
    coalesce(food.name_local, ''::character varying)::text) || ' '::text) ||
    coalesce(food.category, ''::character varying)::text
)
""".strip()

_REFERENCE_SEARCH_VECTOR = """
to_tsvector('simple'::regconfig, coalesce(food.description, ''))
""".strip()

FOOD_SEARCH_SQL = f"""
WITH community_candidate_ids AS (
    SELECT food.id
    FROM foods_community AS food
    WHERE food.slug = CAST(:slug_query AS text)
    UNION
    SELECT food.id
    FROM foods_community AS food
    WHERE {_COMMUNITY_SEARCH_VECTOR} @@
          plainto_tsquery('simple'::regconfig, CAST(:query AS text))
    UNION
    SELECT food.id
    FROM foods_community AS food
    WHERE food.slug % CAST(:query AS text)
    UNION
    SELECT food.id
    FROM foods_community AS food
    WHERE food.name % CAST(:query AS text)
    UNION
    SELECT food.id
    FROM foods_community AS food
    WHERE food.name_local % CAST(:query AS text)
),
reference_candidate_ids AS (
    SELECT food.id
    FROM foods_reference AS food
    WHERE {_REFERENCE_SEARCH_VECTOR} @@
          plainto_tsquery('simple'::regconfig, CAST(:query AS text))
    UNION
    SELECT food.id
    FROM foods_reference AS food
    WHERE food.description % CAST(:query AS text)
),
community_matches AS (
    SELECT
        'community'::text AS source,
        food.slug::text AS source_id,
        food.name::text AS name,
        food.name_local::text AS name_local,
        food.category::text AS category,
        food.pack_license::text AS license,
        food.source_uri::text AS source_uri,
        food.source_license::text AS source_license,
        food.contributed_by::text AS contributed_by,
        food.pack_id::text AS pack_id,
        food.pack_version::text AS pack_version,
        food.provenance::text AS provenance,
        CASE
            WHEN food.slug = CAST(:slug_query AS text)
                THEN {int(RankingTier.EXACT_COMMUNITY_SLUG)}
            WHEN CAST(:locale AS text) IS NOT NULL
                 AND lower(food.locale) = CAST(:locale AS text)
                THEN {int(RankingTier.LOCALE_COMMUNITY)}
            ELSE {int(RankingTier.OTHER_COMMUNITY)}
        END AS ranking_tier,
        greatest(
            similarity(food.slug, CAST(:query AS text)),
            similarity(food.name, CAST(:query AS text)),
            similarity(coalesce(food.name_local, ''), CAST(:query AS text)),
            ts_rank_cd(
                {_COMMUNITY_SEARCH_VECTOR},
                plainto_tsquery('simple'::regconfig, CAST(:query AS text))
            )
        ) AS match_score
    FROM foods_community AS food
    JOIN community_candidate_ids AS candidate ON candidate.id = food.id
    WHERE CAST(:source_filter AS text) IS NULL
       OR CAST(:source_filter AS text) = 'community'
),
reference_matches AS (
    SELECT
        'usda'::text AS source,
        food.fdc_id::text AS source_id,
        food.description::text AS name,
        NULL::text AS name_local,
        food.food_category::text AS category,
        food.license::text AS license,
        NULL::text AS source_uri,
        food.license::text AS source_license,
        NULL::text AS contributed_by,
        NULL::text AS pack_id,
        NULL::text AS pack_version,
        'government_database'::text AS provenance,
        {int(RankingTier.USDA_GENERIC)} AS ranking_tier,
        greatest(
            similarity(food.description, CAST(:query AS text)),
            ts_rank_cd(
                {_REFERENCE_SEARCH_VECTOR},
                plainto_tsquery('simple'::regconfig, CAST(:query AS text))
            )
        ) AS match_score
    FROM foods_reference AS food
    JOIN reference_candidate_ids AS candidate ON candidate.id = food.id
    WHERE CAST(:source_filter AS text) IS NULL
       OR CAST(:source_filter AS text) = 'usda'
),
ranked_matches AS (
    SELECT * FROM community_matches
    UNION ALL
    SELECT * FROM reference_matches
)
SELECT *
FROM ranked_matches
ORDER BY ranking_tier, match_score DESC, lower(name), source, source_id
LIMIT :fetch_limit OFFSET :offset
"""


class FoodSearchTimeoutError(RuntimeError):
    """The database stopped a food search after its configured time budget."""


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
    )


async def search_foods(
    database: AsyncSession,
    *,
    query: str,
    locale: str | None,
    source: FoodSource | None,
    limit: int,
    offset: int,
    statement_timeout_ms: int,
) -> FoodSearchResponse:
    await database.execute(
        text("SELECT set_config('statement_timeout', :timeout, true)"),
        {"timeout": f"{statement_timeout_ms}ms"},
    )
    try:
        rows = list(
            (
                await database.execute(
                    text(FOOD_SEARCH_SQL),
                    {
                        "query": query,
                        "slug_query": query.casefold(),
                        "locale": locale,
                        "source_filter": source.value if source is not None else None,
                        "fetch_limit": limit + 1,
                        "offset": offset,
                    },
                )
            ).mappings()
        )
    except DBAPIError as error:
        if getattr(error.orig, "sqlstate", None) != "57014":
            raise
        await database.rollback()
        raise FoodSearchTimeoutError from error
    return FoodSearchResponse(
        items=[_search_item(row) for row in rows[:limit]],
        limit=limit,
        offset=offset,
        has_more=len(rows) > limit,
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
        row = await database.scalar(select(FoodReference).where(FoodReference.fdc_id == source_id))
        return _reference_detail(row) if row is not None else None
    row = await database.scalar(select(FoodCommunity).where(FoodCommunity.slug == source_id))
    return _community_detail(row) if row is not None else None


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
