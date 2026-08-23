"""opennosh command-line interface."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker

from opennosh_api.capacity import JobRole
from opennosh_api.database import build_administration_engine
from opennosh_api.foodpacks.loader import (
    DEFAULT_MAX_ATTEMPTS,
    FoodPackBatchLoadReport,
    load_food_pack_root_with_retries,
)
from opennosh_api.foodpacks.validation import FoodPackLoadError
from opennosh_api.importers.wger import WgerFormatError, import_wger
from opennosh_api.settings import get_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="opennosh")
    commands = parser.add_subparsers(dest="command", required=True)
    foods = commands.add_parser("foods", help="Manage the community food database")
    food_commands = foods.add_subparsers(dest="food_command", required=True)
    load = food_commands.add_parser("load", help="Validate and load a CC0 food pack")
    load.add_argument("path", type=Path)
    load.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS)
    load.add_argument("--json", action="store_true")
    exercises = commands.add_parser("exercises", help="Manage the attributed exercise catalogue")
    exercise_commands = exercises.add_subparsers(dest="exercise_command", required=True)
    import_wger_command = exercise_commands.add_parser(
        "import-wger", help="Import an offline wger exerciseinfo JSON export"
    )
    import_wger_command.add_argument("paths", nargs="+", type=Path)
    import_wger_command.add_argument("--batch-size", type=int, default=250)
    import_wger_command.add_argument("--json", action="store_true")
    return parser


async def _run_load(arguments: argparse.Namespace) -> FoodPackBatchLoadReport:
    settings = get_settings()
    engine = build_administration_engine(
        settings.process_database_url(JobRole.ADMINISTRATION),
        manifest_path=settings.database_capacity_manifest_path,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        return await load_food_pack_root_with_retries(
            factory, arguments.path, max_attempts=arguments.max_attempts
        )
    finally:
        await engine.dispose()


def run_food_command(arguments: argparse.Namespace) -> int:
    if arguments.food_command != "load":
        raise AssertionError(f"unsupported foods command: {arguments.food_command}")
    try:
        report = asyncio.run(_run_load(arguments))
    except (DBAPIError, FoodPackLoadError, OSError, ValueError) as error:
        print(f"food-pack load failed: {error}", file=sys.stderr)
        return 2
    if arguments.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(
            f"food-pack load: {len(report.packs)} pack(s), {report.entries_written} written, "
            f"{report.entries_unchanged} unchanged, {report.entries_skipped_stale} stale, "
            f"{report.entries_rejected} rejected"
        )
        for pack in report.packs:
            for issue in (*pack.issues, *pack.warnings):
                print(
                    f"{issue.severity.upper()} {issue.code} "
                    f"{issue.json_pointer or '/'}: {issue.message}",
                    file=sys.stderr,
                )
    return 2 if report.failed else 0


async def _run_wger_import(arguments: argparse.Namespace) -> dict[str, object]:
    settings = get_settings()
    engine = build_administration_engine(
        settings.process_database_url(JobRole.ADMINISTRATION),
        manifest_path=settings.database_capacity_manifest_path,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session, session.begin():
            report = await import_wger(
                session,
                arguments.paths,
                batch_size=arguments.batch_size,
            )
    finally:
        await engine.dispose()
    return report.to_dict()


def run_exercise_command(arguments: argparse.Namespace) -> int:
    if arguments.exercise_command != "import-wger":
        raise AssertionError(f"unsupported exercise command: {arguments.exercise_command}")
    try:
        report = asyncio.run(_run_wger_import(arguments))
    except (DBAPIError, OSError, ValueError, WgerFormatError) as error:
        print(f"wger import failed: {error}", file=sys.stderr)
        return 2
    if arguments.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            "wger import: "
            f"{report['rows_seen']} read, {report['rows_written']} written, "
            f"{report['rows_skipped_stale']} stale, {report['rows_rejected']} rejected"
        )
        for issue in cast(list[dict[str, object]], report["issues"]):
            print(
                f"{issue['source_path']}:{issue['row_number'] or '?'}: {issue['message']}",
                file=sys.stderr,
            )
    return 2 if report["rows_rejected"] else 0


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.command == "foods":
        return run_food_command(arguments)
    if arguments.command == "exercises":
        return run_exercise_command(arguments)
    raise AssertionError(f"unsupported command: {arguments.command}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
