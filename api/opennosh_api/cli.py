"""opennosh command-line interface."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from datetime import datetime
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
from opennosh_api.public.artifacts import ArtifactReadError
from opennosh_api.public.bootstrap import (
    build_starter_release,
    inventory_sha256,
    load_verified_inventory,
    verify_starter_release,
)
from opennosh_api.public.live import (
    LiveReleaseVerificationError,
    warm_and_verify_public_api,
)
from opennosh_api.public.r2 import (
    R2PublicationError,
    WranglerR2ObjectWriter,
    publish_starter_release_to_r2,
)
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
    commons = commands.add_parser("commons", help="Manage signed public Commons releases")
    commons_commands = commons.add_subparsers(dest="commons_command", required=True)
    build_release = commons_commands.add_parser(
        "build-starter-release",
        help="Build and verify an offline-signed starter release",
    )
    build_release.add_argument("--packs-root", type=Path, required=True)
    build_release.add_argument("--output", type=Path, required=True)
    build_release.add_argument("--release-version", required=True)
    build_release.add_argument("--published-at", type=datetime.fromisoformat, required=True)
    build_release.add_argument("--source-commit", required=True)
    build_release.add_argument("--manifest-key-id", required=True)
    build_release.add_argument("--manifest-private-key", type=Path, required=True)
    build_release.add_argument("--receipt-key-id", required=True)
    build_release.add_argument("--receipt-private-key", type=Path, required=True)
    build_release.add_argument("--decision-reference", required=True)
    build_release.add_argument("--approving-actor", required=True)
    build_release.add_argument("--json", action="store_true")
    verify_release = commons_commands.add_parser(
        "verify-starter-release",
        help="Verify a complete starter release directory",
    )
    verify_release.add_argument("directory", type=Path)
    verify_release.add_argument("--inventory-sha256", required=True)
    verify_release.add_argument("--json", action="store_true")
    publish_release = commons_commands.add_parser(
        "publish-starter-release",
        help="Verify and publish a starter release to Cloudflare R2",
    )
    publish_release.add_argument("directory", type=Path)
    publish_release.add_argument("--inventory-sha256", required=True)
    publish_release.add_argument("--bucket", required=True)
    publish_release.add_argument("--origin-url", required=True)
    publish_release.add_argument("--wrangler", type=Path, required=True)
    publish_release.add_argument("--json", action="store_true")
    warm_release = commons_commands.add_parser(
        "warm-live-release",
        help="Warm and verify every release object through the deployed API",
    )
    warm_release.add_argument("directory", type=Path)
    warm_release.add_argument("--inventory-sha256", required=True)
    warm_release.add_argument("--api-origin", required=True)
    warm_release.add_argument("--concurrency", type=int, default=8)
    warm_release.add_argument("--json", action="store_true")
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


def run_commons_command(arguments: argparse.Namespace) -> int:
    try:
        if arguments.commons_command == "build-starter-release":
            inventory = build_starter_release(
                packs_root=arguments.packs_root,
                output_directory=arguments.output,
                release_version=arguments.release_version,
                published_at=arguments.published_at,
                source_commit=arguments.source_commit,
                manifest_key_id=arguments.manifest_key_id,
                manifest_private_key_path=arguments.manifest_private_key,
                receipt_key_id=arguments.receipt_key_id,
                receipt_private_key_path=arguments.receipt_private_key,
                decision_reference=arguments.decision_reference,
                approving_actor=arguments.approving_actor,
            )
            asyncio.run(verify_starter_release(arguments.output, inventory))
            summary = inventory.model_dump(mode="json")
            summary["inventory_sha256"] = inventory_sha256(
                arguments.output / "inventory.json"
            )
        elif arguments.commons_command == "verify-starter-release":
            inventory = load_verified_inventory(
                arguments.directory / "inventory.json",
                expected_sha256=arguments.inventory_sha256,
            )
            asyncio.run(verify_starter_release(arguments.directory, inventory))
            summary = {
                "schema_version": "1",
                "verified": True,
                "release_version": inventory.release_version,
                "food_count": inventory.food_count,
                "pack_count": inventory.pack_count,
                "object_count": len(inventory.objects),
            }
        elif arguments.commons_command == "publish-starter-release":
            inventory = load_verified_inventory(
                arguments.directory / "inventory.json",
                expected_sha256=arguments.inventory_sha256,
            )
            asyncio.run(verify_starter_release(arguments.directory, inventory))
            publication_result = asyncio.run(
                publish_starter_release_to_r2(
                    directory=arguments.directory,
                    inventory=inventory,
                    bucket=arguments.bucket,
                    origin_url=arguments.origin_url,
                    writer=WranglerR2ObjectWriter(arguments.wrangler),
                )
            )
            summary = {
                "schema_version": "1",
                "release_version": publication_result.release_version,
                "food_count": inventory.food_count,
                "pack_count": inventory.pack_count,
                "uploaded_immutable": publication_result.uploaded_immutable,
                "reused_immutable": publication_result.reused_immutable,
                "pointer_replaced": publication_result.pointer_replaced,
            }
        elif arguments.commons_command == "warm-live-release":
            inventory = load_verified_inventory(
                arguments.directory / "inventory.json",
                expected_sha256=arguments.inventory_sha256,
            )
            asyncio.run(verify_starter_release(arguments.directory, inventory))
            live_result = asyncio.run(
                warm_and_verify_public_api(
                    directory=arguments.directory,
                    inventory=inventory,
                    api_origin=arguments.api_origin,
                    concurrency=arguments.concurrency,
                )
            )
            summary = {
                "schema_version": "1",
                "release_version": live_result.release_version,
                "food_count": live_result.foods_warmed,
                "pack_count": live_result.packs_warmed,
                "provenance_warmed": live_result.provenance_warmed,
                "latest_checkpoint_advanced": live_result.latest_checkpoint_advanced,
            }
        else:
            raise AssertionError(f"unsupported Commons command: {arguments.commons_command}")
    except (
        ArtifactReadError,
        FileExistsError,
        LiveReleaseVerificationError,
        OSError,
        PermissionError,
        R2PublicationError,
        ValueError,
    ) as error:
        print(f"Commons release failed: {error}", file=sys.stderr)
        return 2
    if arguments.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(
            "Commons release verified: "
            f"{summary['release_version']}, "
            f"{summary['food_count']} foods, "
            f"{summary['pack_count']} packs"
        )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.command == "foods":
        return run_food_command(arguments)
    if arguments.command == "exercises":
        return run_exercise_command(arguments)
    if arguments.command == "commons":
        return run_commons_command(arguments)
    raise AssertionError(f"unsupported command: {arguments.command}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
