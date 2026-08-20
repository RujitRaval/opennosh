from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from sqlalchemy import literal_column, or_
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from opennosh_api.database import build_engine
from opennosh_api.models import Exercise
from opennosh_api.settings import get_settings

WGER_SOURCE = "wger"
WGER_LICENSE_SPDX = "CC-BY-SA-3.0"
WGER_LICENSE_SHORT_NAME = "CC-BY-SA 3"
WGER_LICENSE_FULL_NAME = "Creative Commons Attribution Share Alike 3"
WGER_LICENSE_PATH_PREFIX = "/licenses/by-sa/3.0"
WGER_LICENSE_URL = "https://creativecommons.org/licenses/by-sa/3.0/"
_MAX_INPUT_BYTES = 64 * 1024 * 1024
_MAX_EXERCISES = 10_000
_MAX_TRANSLATIONS = 100
_MAX_SEARCH_TEXT = 50_000
_MAX_RETAINED_ISSUES = 1_000
_MAX_BATCH_SIZE = 1_000
_FORBIDDEN_URL_CHARACTERS = frozenset("<>\"'\\")
_MARKUP = re.compile(r"[<>]")
_MIN_SOURCE_TIMESTAMP = datetime.min.replace(tzinfo=UTC) + timedelta(microseconds=1)
_MAX_SOURCE_TIMESTAMP = datetime.max.replace(tzinfo=UTC) - timedelta(microseconds=1)


class WgerFormatError(ValueError):
    """The offline file is not a supported wger exerciseinfo JSON export."""


class _PlainTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag.casefold() in {"script", "style", "iframe", "object"}:
            self.hidden_depth += 1
        elif self.hidden_depth == 0 and tag.casefold() in {"br", "p", "li", "div"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in {"script", "style", "iframe", "object"}:
            self.hidden_depth = max(0, self.hidden_depth - 1)
        elif self.hidden_depth == 0 and tag.casefold() in {"p", "li", "div"}:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self.hidden_depth == 0:
            self.parts.append(data)


def _mapping(value: object, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _sequence(value: object, *, label: str) -> Sequence[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return value


def _plain_text(
    value: object,
    *,
    label: str,
    maximum: int,
    required: bool = True,
    reject_markup: bool = True,
) -> str | None:
    if value is None:
        if required:
            raise ValueError(f"{label} is required")
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    normalized = " ".join(value.split())
    if not normalized:
        if required:
            raise ValueError(f"{label} is required")
        return None
    if len(normalized) > maximum:
        raise ValueError(f"{label} must be at most {maximum} characters")
    if any(ord(character) < 32 for character in normalized):
        raise ValueError(f"{label} contains control characters")
    if reject_markup and _MARKUP.search(normalized):
        raise ValueError(f"{label} must not contain markup")
    return normalized


def _description_text(value: object, *, label: str) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    parser = _PlainTextParser()
    try:
        parser.feed(value)
        parser.close()
    except Exception as error:  # HTMLParser can surface malformed entity errors
        raise ValueError(f"{label} contains malformed HTML") from error
    return _plain_text(
        "".join(parser.parts),
        label=label,
        maximum=10_000,
        required=False,
    )


def _safe_url(
    value: object, *, label: str, required: bool = True, maximum: int = 2048
) -> str | None:
    text_value = _plain_text(
        value,
        label=label,
        maximum=maximum,
        required=required,
        reject_markup=False,
    )
    if text_value is None:
        return None
    if any(
        character.isspace() or character in _FORBIDDEN_URL_CHARACTERS for character in text_value
    ):
        raise ValueError(f"{label} must be a safe HTTP(S) URL")
    parsed = urlsplit(text_value)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError(f"{label} must be a safe HTTP(S) URL") from error
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        and not 1 <= port <= 65535
    ):
        raise ValueError(f"{label} must be a safe HTTP(S) URL")
    return parsed._replace(scheme=parsed.scheme.casefold()).geturl()


def _source_identifier(value: object, *, label: str) -> str:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a positive integer")
    result = str(value).strip() if value is not None else ""
    if not result.isdigit() or int(result) <= 0 or len(result) > 160:
        raise ValueError(f"{label} must be a positive integer")
    return result


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("last_update_global is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("last_update_global must be an ISO 8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("last_update_global must include a timezone")
    try:
        normalized = parsed.astimezone(UTC)
    except (OverflowError, ValueError) as error:
        raise ValueError("last_update_global is outside the supported timestamp range") from error
    if not _MIN_SOURCE_TIMESTAMP <= normalized <= _MAX_SOURCE_TIMESTAMP:
        raise ValueError("last_update_global is outside the supported timestamp range")
    return parsed


@dataclass(frozen=True)
class _License:
    source_id: str
    url: str


def _allowed_license(value: object, *, label: str) -> _License:
    license_row = _mapping(value, label=label)
    source_id = _source_identifier(license_row.get("id"), label=f"{label}.id")
    short_name = _plain_text(license_row.get("short_name"), label=f"{label}.short_name", maximum=64)
    if short_name != WGER_LICENSE_SHORT_NAME:
        raise ValueError(f"{label} is not allowlisted as {WGER_LICENSE_SPDX}")
    full_name = _plain_text(
        license_row.get("full_name"), label=f"{label}.full_name", maximum=128
    )
    if full_name != WGER_LICENSE_FULL_NAME:
        raise ValueError(f"{label} metadata is ambiguous or unsupported")
    url = _safe_url(license_row.get("url"), label=f"{label}.url")
    assert url is not None
    parsed = urlsplit(url)
    if parsed.scheme.casefold() != "https" or parsed.hostname != "creativecommons.org":
        raise ValueError(f"{label}.url is not the CC BY-SA 3.0 license URL")
    license_path = parsed.path.rstrip("/")
    if license_path != WGER_LICENSE_PATH_PREFIX and not license_path.startswith(
        f"{WGER_LICENSE_PATH_PREFIX}/"
    ):
        raise ValueError(f"{label}.url is not the CC BY-SA 3.0 license URL")
    return _License(source_id=source_id, url=WGER_LICENSE_URL)


def _translation_license(value: object, parent: _License, *, label: str) -> _License:
    if isinstance(value, Mapping):
        nested = _allowed_license(value, label=label)
        if nested.source_id != parent.source_id or nested.url != parent.url:
            raise ValueError(f"{label} does not match the exercise license")
        return nested
    source_id = _source_identifier(value, label=label)
    if source_id != parent.source_id:
        raise ValueError(f"{label} is not allowlisted as {WGER_LICENSE_SPDX}")
    return parent


def _text_list(value: object, *, label: str, maximum_items: int = 100) -> list[str]:
    items = _sequence(value, label=label)
    if len(items) > maximum_items:
        raise ValueError(f"{label} contains too many items")
    result: list[str] = []
    for index, item in enumerate(items):
        if isinstance(item, Mapping):
            candidate = item.get("alias", item.get("note", item.get("name")))
        else:
            candidate = item
        parsed = _plain_text(candidate, label=f"{label}[{index}]", maximum=500, required=False)
        if parsed is not None:
            result.append(parsed)
    return result


def _taxonomy_names(value: object, *, label: str) -> list[str]:
    rows = _sequence(value, label=label)
    if len(rows) > 100:
        raise ValueError(f"{label} contains too many items")
    names: set[str] = set()
    for index, item in enumerate(rows):
        row = _mapping(item, label=f"{label}[{index}]")
        candidate = row.get("name_en") or row.get("name")
        parsed = _plain_text(candidate, label=f"{label}[{index}].name", maximum=100)
        assert parsed is not None
        names.add(parsed.casefold())
    return sorted(names)


def _attribution(author: str, source_url: str) -> str:
    return (
        f"{author}, via wger ({source_url}), licensed under Creative Commons "
        "Attribution-ShareAlike 3.0."
    )


@dataclass(frozen=True)
class WgerExerciseRecord:
    source_id: str
    slug: str
    name: str
    muscle_groups: list[str]
    equipment: list[str]
    search_text: str
    source_url: str
    derivative_source_url: str | None
    license_url: str
    author: str
    author_url: str | None
    attribution_text: str
    translations_json: list[dict[str, object]]
    translation_attribution_json: list[dict[str, object]]
    source_updated_at: datetime
    source: str = WGER_SOURCE
    license_spdx: str = WGER_LICENSE_SPDX

    def database_values(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "slug": self.slug,
            "name": self.name,
            "muscle_groups": self.muscle_groups,
            "equipment": self.equipment,
            "search_text": self.search_text,
            "source": self.source,
            "source_url": self.source_url,
            "derivative_source_url": self.derivative_source_url,
            "license_spdx": self.license_spdx,
            "license_url": self.license_url,
            "author": self.author,
            "author_url": self.author_url,
            "attribution_text": self.attribution_text,
            "translations_json": self.translations_json,
            "translation_attribution_json": self.translation_attribution_json,
            "source_updated_at": self.source_updated_at,
        }


@dataclass(frozen=True)
class WgerImportIssue:
    source_path: str
    row_number: int | None
    source_id: str | None
    message: str

    def to_dict(self) -> dict[str, object]:
        return {
            "source_path": self.source_path,
            "row_number": self.row_number,
            "source_id": self.source_id,
            "message": self.message,
        }


@dataclass(frozen=True)
class WgerParseOutcome:
    record: WgerExerciseRecord | None = None
    issue: WgerImportIssue | None = None

    def __post_init__(self) -> None:
        if (self.record is None) == (self.issue is None):
            raise ValueError("A parse outcome must contain exactly one record or issue")


@dataclass
class WgerImportReport:
    rows_seen: int = 0
    rows_written: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    rows_skipped_stale: int = 0
    rows_rejected: int = 0
    issues: list[WgerImportIssue] = field(default_factory=list)

    def add_issue(self, issue: WgerImportIssue) -> None:
        self.rows_rejected += 1
        if len(self.issues) < _MAX_RETAINED_ISSUES:
            self.issues.append(issue)

    def to_dict(self) -> dict[str, object]:
        return {
            "rows_seen": self.rows_seen,
            "rows_written": self.rows_written,
            "rows_inserted": self.rows_inserted,
            "rows_updated": self.rows_updated,
            "rows_skipped_stale": self.rows_skipped_stale,
            "rows_rejected": self.rows_rejected,
            "issues_omitted": self.rows_rejected - len(self.issues),
            "issues": [issue.to_dict() for issue in self.issues],
        }


def _parse_translation(
    value: object, parent_license: _License, *, source_url: str, index: int
) -> dict[str, object]:
    row = _mapping(value, label=f"translations[{index}]")
    _translation_license(row.get("license"), parent_license, label=f"translations[{index}].license")
    source_id = _source_identifier(row.get("id"), label=f"translations[{index}].id")
    language_id = _source_identifier(row.get("language"), label=f"translations[{index}].language")
    name = _plain_text(row.get("name"), label=f"translations[{index}].name", maximum=255)
    author = _plain_text(
        row.get("license_author"),
        label=f"translations[{index}].license_author",
        maximum=255,
    )
    assert name is not None and author is not None
    translation_source_url = _safe_url(
        row.get("license_object_url"),
        label=f"translations[{index}].license_object_url",
        required=False,
    )
    derivative_url = _safe_url(
        row.get("license_derivative_source_url"),
        label=f"translations[{index}].license_derivative_source_url",
        required=False,
    )
    author_url = _safe_url(
        row.get("license_author_url"),
        label=f"translations[{index}].license_author_url",
        required=False,
    )
    effective_source_url = translation_source_url or source_url
    license_title = _plain_text(
        row.get("license_title"),
        label=f"translations[{index}].license_title",
        maximum=255,
        required=False,
    )
    source_uuid = _plain_text(
        row.get("uuid"),
        label=f"translations[{index}].uuid",
        maximum=64,
        required=False,
    )
    return {
        "source_id": source_id,
        "source_uuid": source_uuid,
        "language_id": language_id,
        "name": name,
        "description": _description_text(
            row.get("description"), label=f"translations[{index}].description"
        ),
        "aliases": _text_list(row.get("aliases", []), label=f"translations[{index}].aliases"),
        "notes": _text_list(row.get("notes", []), label=f"translations[{index}].notes"),
        "source_url": translation_source_url,
        "derivative_source_url": derivative_url,
        "license_spdx": WGER_LICENSE_SPDX,
        "license_url": parent_license.url,
        "license_title": license_title,
        "author": author,
        "author_url": author_url,
        "attribution_text": _attribution(author, effective_source_url),
    }


def _parse_exercise(value: object) -> WgerExerciseRecord:
    row = _mapping(value, label="exercise")
    source_id = _source_identifier(row.get("id"), label="exercise.id")
    parent_license = _allowed_license(row.get("license"), label="exercise.license")
    source_url = f"https://wger.de/api/v2/exerciseinfo/{source_id}/"
    author = _plain_text(row.get("license_author"), label="exercise.license_author", maximum=255)
    assert author is not None
    translation_rows = _sequence(row.get("translations"), label="exercise.translations")
    if not translation_rows:
        raise ValueError("exercise.translations must contain at least one attributed translation")
    if len(translation_rows) > _MAX_TRANSLATIONS:
        raise ValueError(f"exercise.translations exceeds the {_MAX_TRANSLATIONS}-item limit")
    translations = [
        _parse_translation(item, parent_license, source_url=source_url, index=index)
        for index, item in enumerate(translation_rows)
    ]
    primary = next(
        (translation for translation in translations if translation["language_id"] == "2"),
        translations[0],
    )
    muscles = sorted(
        set(_taxonomy_names(row.get("muscles", []), label="exercise.muscles"))
        | set(_taxonomy_names(row.get("muscles_secondary", []), label="exercise.muscles_secondary"))
    )
    equipment = _taxonomy_names(row.get("equipment", []), label="exercise.equipment")
    category = _mapping(row.get("category"), label="exercise.category")
    category_name = _plain_text(category.get("name"), label="exercise.category.name", maximum=100)
    assert category_name is not None
    search_parts: list[str] = [str(primary["name"]), category_name, *muscles, *equipment]
    for translation in translations:
        search_parts.append(str(translation["name"]))
        if translation["description"] is not None:
            search_parts.append(str(translation["description"]))
        search_parts.extend(str(item) for item in cast(list[str], translation["aliases"]))
        search_parts.extend(str(item) for item in cast(list[str], translation["notes"]))
    search_text = " ".join(search_parts)
    if len(search_text) > _MAX_SEARCH_TEXT:
        raise ValueError(f"exercise searchable text exceeds {_MAX_SEARCH_TEXT} characters")
    top_author_url = primary["author_url"] if primary["author"] == author else None
    attribution_rows = [
        {
            key: translation[key]
            for key in (
                "source_id",
                "language_id",
                "source_url",
                "derivative_source_url",
                "license_spdx",
                "license_url",
                "license_title",
                "author",
                "author_url",
                "attribution_text",
            )
        }
        for translation in translations
    ]
    return WgerExerciseRecord(
        source_id=source_id,
        slug=f"wger-{source_id}",
        name=str(primary["name"]),
        muscle_groups=muscles,
        equipment=equipment,
        search_text=search_text,
        source_url=source_url,
        derivative_source_url=(
            str(primary["derivative_source_url"])
            if primary["derivative_source_url"] is not None
            else None
        ),
        license_url=parent_license.url,
        author=author,
        author_url=str(top_author_url) if top_author_url is not None else None,
        attribution_text=_attribution(author, source_url),
        translations_json=translations,
        translation_attribution_json=attribution_rows,
        source_updated_at=_timestamp(row.get("last_update_global")),
    )


def iter_wger(path: str | Path) -> Iterator[WgerParseOutcome]:
    resolved = Path(path)
    try:
        size = resolved.stat().st_size
    except OSError:
        raise
    if size > _MAX_INPUT_BYTES:
        raise WgerFormatError(f"{resolved} exceeds the {_MAX_INPUT_BYTES}-byte input limit")
    try:
        payload = json.loads(resolved.read_bytes())
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WgerFormatError(f"{resolved} contains malformed JSON: {error}") from error
    if isinstance(payload, Mapping):
        if payload.get("next") not in (None, "") or payload.get("previous") not in (None, ""):
            raise WgerFormatError(f"{resolved} is a partial paginated export")
        rows = payload.get("results")
        count = payload.get("count")
        if isinstance(rows, list) and (
            isinstance(count, bool) or not isinstance(count, int) or count != len(rows)
        ):
            raise WgerFormatError(f"{resolved} has inconsistent pagination metadata")
    else:
        rows = payload
    if not isinstance(rows, list):
        raise WgerFormatError(f"{resolved} must contain an exercise array or results envelope")
    if not rows:
        raise WgerFormatError(f"{resolved} contains no exercise records")
    if len(rows) > _MAX_EXERCISES:
        raise WgerFormatError(f"{resolved} exceeds the {_MAX_EXERCISES}-exercise limit")
    seen: set[str] = set()
    for row_number, item in enumerate(rows, start=1):
        raw_source_id: str | None = None
        if isinstance(item, Mapping) and item.get("id") is not None:
            raw_source_id = str(item["id"])
        try:
            record = _parse_exercise(item)
            if record.source_id in seen:
                raise ValueError(f"duplicate wger exercise ID {record.source_id}")
            seen.add(record.source_id)
        except (TypeError, ValueError) as error:
            yield WgerParseOutcome(
                issue=WgerImportIssue(
                    source_path=str(resolved),
                    row_number=row_number,
                    source_id=raw_source_id,
                    message=str(error),
                )
            )
        else:
            yield WgerParseOutcome(record=record)


async def _write_batch(
    session: AsyncSession, records: Sequence[WgerExerciseRecord]
) -> tuple[int, int, int]:
    statement = postgresql_insert(Exercise).values([record.database_values() for record in records])
    upsert = statement.on_conflict_do_update(
        index_elements=[Exercise.source, Exercise.source_id],
        set_={
            "slug": statement.excluded.slug,
            "name": statement.excluded.name,
            "muscle_groups": statement.excluded.muscle_groups,
            "equipment": statement.excluded.equipment,
            "search_text": statement.excluded.search_text,
            "source_url": statement.excluded.source_url,
            "derivative_source_url": statement.excluded.derivative_source_url,
            "license_spdx": statement.excluded.license_spdx,
            "license_url": statement.excluded.license_url,
            "author": statement.excluded.author,
            "author_url": statement.excluded.author_url,
            "attribution_text": statement.excluded.attribution_text,
            "translations_json": statement.excluded.translations_json,
            "translation_attribution_json": statement.excluded.translation_attribution_json,
            "source_updated_at": statement.excluded.source_updated_at,
        },
        where=or_(
            Exercise.source_updated_at.is_(None),
            statement.excluded.source_updated_at > Exercise.source_updated_at,
        ),
    )
    returning: Any = upsert.returning(literal_column("xmax = 0").label("inserted"))
    inserted_flags = list((await session.execute(returning)).scalars())
    inserted = sum(bool(flag) for flag in inserted_flags)
    updated = len(inserted_flags) - inserted
    return inserted, updated, len(records) - len(inserted_flags)


async def import_wger(
    session: AsyncSession,
    paths: Iterable[str | Path],
    *,
    batch_size: int = 250,
) -> WgerImportReport:
    if not 1 <= batch_size <= _MAX_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {_MAX_BATCH_SIZE}")
    report = WgerImportReport()
    batch: list[WgerExerciseRecord] = []

    async def flush() -> None:
        if not batch:
            return
        inserted, updated, stale = await _write_batch(session, batch)
        report.rows_inserted += inserted
        report.rows_updated += updated
        report.rows_written += inserted + updated
        report.rows_skipped_stale += stale
        batch.clear()

    for path in paths:
        for outcome in iter_wger(path):
            report.rows_seen += 1
            if outcome.issue is not None:
                report.add_issue(outcome.issue)
                continue
            assert outcome.record is not None
            batch.append(outcome.record)
            if len(batch) >= batch_size:
                await flush()
        await flush()
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import an offline wger exerciseinfo JSON export into exercises."
    )
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--batch-size", type=int, default=250)
    return parser


async def _run_cli(arguments: argparse.Namespace) -> int:
    engine = build_engine(arguments.database_url or get_settings().database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session, session.begin():
            report = await import_wger(session, arguments.paths, batch_size=arguments.batch_size)
    finally:
        await engine.dispose()
    print(json.dumps(report.to_dict(), sort_keys=True))
    return 2 if report.rows_rejected else 0


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    try:
        return asyncio.run(_run_cli(arguments))
    except (OSError, ValueError, WgerFormatError) as error:
        print(f"wger import failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
