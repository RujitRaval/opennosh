from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

from sqlalchemy import Text, case, func, literal_column, select, text, union
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from opennosh_api.exercises.schemas import (
    ExerciseAttribution,
    ExerciseDetail,
    ExerciseExport,
    ExerciseSearchResponse,
    ExerciseTranslation,
    ExerciseTranslationAttribution,
)
from opennosh_api.exports.streaming import (
    ExportByteLimitError,
    JsonSection,
    spool_json_stream,
    stream_json_sections,
)
from opennosh_api.models import Exercise

SEARCH_QUERY_MIN_LENGTH = 2
SEARCH_QUERY_MAX_LENGTH = 100
SEARCH_FILTER_MAX_LENGTH = 100
SEARCH_LIMIT_DEFAULT = 20
SEARCH_LIMIT_MAX = 50
SEARCH_OFFSET_MAX = 10_000
EXPORT_ROW_LIMIT = 10_000
EXPORT_MAX_DATABASE_BYTES = 64 * 1024 * 1024
SEARCH_PLAN_MAX_EXECUTION_MS = 100.0


class ExerciseSearchTimeoutError(RuntimeError):
    """The database stopped an exercise search after its configured time budget."""


class ExerciseExportLimitError(RuntimeError):
    """The catalogue exceeded the bounded JSON export size."""


class ExerciseExportTimeoutError(RuntimeError):
    """The database stopped an exercise export after its configured time budget."""


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


def normalize_filter(value: str | None, *, label: str) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split()).casefold()
    if not normalized or len(normalized) > SEARCH_FILTER_MAX_LENGTH:
        raise ValueError(
            f"{label} must contain between 1 and {SEARCH_FILTER_MAX_LENGTH} characters"
        )
    if "\x00" in normalized or any(character in "<>" for character in normalized):
        raise ValueError(f"{label} contains unsafe characters")
    return normalized


def _like_pattern(value: str) -> str:
    escaped = value.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    return f"%{escaped}%"


def exercise_detail(row: Exercise) -> ExerciseDetail:
    return ExerciseDetail(
        id=row.id,
        slug=row.slug,
        name=row.name,
        muscle_groups=row.muscle_groups,
        equipment=row.equipment,
        translations=[ExerciseTranslation.model_validate(item) for item in row.translations_json],
        attribution=ExerciseAttribution(
            source=row.source,
            source_id=row.source_id,
            source_url=row.source_url,
            derivative_source_url=row.derivative_source_url,
            license_spdx=row.license_spdx,
            license_url=row.license_url,
            author=row.author,
            author_url=row.author_url,
            attribution_text=row.attribution_text,
            translations=[
                ExerciseTranslationAttribution.model_validate(item)
                for item in row.translation_attribution_json
            ],
        ),
        source_updated_at=row.source_updated_at,
    )


def _search_statement(
    *, query: str, muscle: str | None, equipment: str | None, limit: int, offset: int
) -> Select[tuple[Exercise]]:
    document = func.to_tsvector(
        literal_column("'simple'::regconfig"),
        Exercise.name.cast(Text) + literal_column("' '::text") + Exercise.search_text,
    )
    query_expression = func.plainto_tsquery(literal_column("'simple'::regconfig"), query)
    source_filter = Exercise.source == "wger"
    candidate_ids = union(
        select(Exercise.id).where(
            source_filter,
            func.lower(Exercise.name) == query.casefold(),
        ),
        select(Exercise.id).where(
            source_filter,
            Exercise.name.ilike(_like_pattern(query), escape="!"),
        ),
        select(Exercise.id).where(source_filter, document.op("@@")(query_expression)),
    ).subquery()
    statement = select(Exercise).join(candidate_ids, candidate_ids.c.id == Exercise.id)
    if muscle is not None:
        statement = statement.where(Exercise.muscle_groups.contains([muscle]))
    if equipment is not None:
        statement = statement.where(Exercise.equipment.contains([equipment]))
    return (
        statement.order_by(
            case((func.lower(Exercise.name) == query.casefold(), 0), else_=1),
            func.ts_rank_cd(document, query_expression).desc(),
            func.lower(Exercise.name),
            Exercise.source_id,
        )
        .limit(limit + 1)
        .offset(offset)
    )


async def search_exercises(
    database: AsyncSession,
    *,
    query: str,
    muscle: str | None,
    equipment: str | None,
    limit: int,
    offset: int,
    statement_timeout_ms: int,
) -> ExerciseSearchResponse:
    await database.execute(
        text("SELECT set_config('statement_timeout', :timeout, true)"),
        {"timeout": f"{statement_timeout_ms}ms"},
    )
    statement = _search_statement(
        query=query,
        muscle=muscle,
        equipment=equipment,
        limit=limit,
        offset=offset,
    )
    try:
        rows = list((await database.scalars(statement)).all())
    except DBAPIError as error:
        if getattr(error.orig, "sqlstate", None) != "57014":
            raise
        await database.rollback()
        raise ExerciseSearchTimeoutError from error
    return ExerciseSearchResponse(
        items=[exercise_detail(row) for row in rows[:limit]],
        limit=limit,
        offset=offset,
        has_more=len(rows) > limit,
    )


async def get_exercise(database: AsyncSession, exercise_id: UUID) -> ExerciseDetail | None:
    row = await database.scalar(
        select(Exercise).where(Exercise.id == exercise_id, Exercise.source == "wger")
    )
    return exercise_detail(row) if row is not None else None


async def export_exercises(
    database: AsyncSession, *, statement_timeout_ms: int
) -> ExerciseExport:
    await database.execute(
        text("SELECT set_config('statement_timeout', :timeout, true)"),
        {"timeout": f"{statement_timeout_ms}ms"},
    )
    try:
        database_bytes = int(
            await database.scalar(
                text(
                    "SELECT COALESCE(sum(pg_column_size(exercises)), 0) FROM exercises "
                    "WHERE source = 'wger' AND license_spdx = 'CC-BY-SA-3.0'"
                )
            )
            or 0
        )
        if database_bytes > EXPORT_MAX_DATABASE_BYTES:
            raise ExerciseExportLimitError
        rows = list(
            (
                await database.scalars(
                select(Exercise)
                .where(
                    Exercise.source == "wger",
                    Exercise.license_spdx == "CC-BY-SA-3.0",
                )
                .order_by(func.lower(Exercise.name), Exercise.source_id)
                .limit(EXPORT_ROW_LIMIT + 1)
                )
            ).all()
        )
    except DBAPIError as error:
        if getattr(error.orig, "sqlstate", None) != "57014":
            raise
        await database.rollback()
        raise ExerciseExportTimeoutError from error
    if len(rows) > EXPORT_ROW_LIMIT:
        raise ExerciseExportLimitError
    return ExerciseExport(entries=[exercise_detail(row) for row in rows])


async def prepare_exercise_export(
    database: AsyncSession, *, statement_timeout_ms: int
) -> AsyncIterator[bytes]:
    """Prepare a bounded CC BY-SA export whose response body streams row by row."""
    await database.execute(
        text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
    )
    await database.execute(
        text("SELECT set_config('statement_timeout', :timeout, true)"),
        {"timeout": f"{statement_timeout_ms}ms"},
    )

    async def rows() -> AsyncIterator[ExerciseDetail]:
        result = await database.stream_scalars(
            select(Exercise)
            .where(
                Exercise.source == "wger",
                Exercise.license_spdx == "CC-BY-SA-3.0",
            )
            .order_by(func.lower(Exercise.name), Exercise.source_id)
            .execution_options(yield_per=100)
        )
        count = 0
        try:
            async for row in result:
                count += 1
                if count > EXPORT_ROW_LIMIT:
                    raise ExerciseExportLimitError
                yield exercise_detail(row)
        finally:
            await result.close()

    try:
        body = await spool_json_stream(
            stream_json_sections(
                ExerciseExport(entries=[]), [JsonSection("entries", rows)]
            ),
            max_bytes=EXPORT_MAX_DATABASE_BYTES,
        )
    except (ExerciseExportLimitError, ExportByteLimitError) as error:
        await database.rollback()
        raise ExerciseExportLimitError from error
    except DBAPIError as error:
        if getattr(error.orig, "sqlstate", None) != "57014":
            await database.rollback()
            raise
        await database.rollback()
        raise ExerciseExportTimeoutError from error
    except BaseException:
        await database.rollback()
        raise
    await database.rollback()
    return body
