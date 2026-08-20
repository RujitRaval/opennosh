"""opennosh command-line interface."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker

from opennosh_api.database import build_engine
from opennosh_api.foodpacks.loader import (
    DEFAULT_MAX_ATTEMPTS,
    FoodPackBatchLoadReport,
    load_food_pack_root_with_retries,
)
from opennosh_api.foodpacks.validation import FoodPackLoadError
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
    return parser


async def _run_load(arguments: argparse.Namespace) -> FoodPackBatchLoadReport:
    engine = build_engine(get_settings().database_url)
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


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.command == "foods":
        return run_food_command(arguments)
    raise AssertionError(f"unsupported command: {arguments.command}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
