from __future__ import annotations

import argparse
import asyncio
import os
from datetime import datetime
from pathlib import Path

from opennosh_api.acceptance.fixtures import (
    _resolve_published_at,
    hand_fixture_to_runtime,
)
from opennosh_api.acceptance.pipeline import run_browser_acceptance_pipeline


def _published_at(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError("published-at must be an ISO 8601 timestamp") from error


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Prepare or run one worker-driven browser-acceptance release."
    )
    result.add_argument("--mode", choices=("prepare", "run"), default="run")
    result.add_argument("--artifact-directory", required=True, type=Path)
    result.add_argument("--state-directory", required=True, type=Path)
    result.add_argument("--published-at", type=_published_at)
    result.add_argument("--database-url")
    result.add_argument(
        "--capacity-manifest-path",
        type=Path,
        default=Path("config/database-capacity.acceptance.v1.json"),
    )
    result.add_argument("--timeout-seconds", type=float, default=60.0)
    result.add_argument("--runtime-uid", type=int, default=10001)
    result.add_argument("--runtime-gid", type=int, default=10001)
    return result


def main() -> None:
    args = parser().parse_args()
    environment = os.environ.get("APP_ENVIRONMENT", "").lower()
    enabled = os.environ.get("OPENNOSH_ACCEPTANCE_FIXTURES") == "1"
    if environment not in {"development", "test", "testing"} or not enabled:
        raise SystemExit(
            "Acceptance fixtures require an explicit development/test environment and opt-in"
        )
    args.artifact_directory.mkdir(parents=True, exist_ok=True)
    args.state_directory.mkdir(parents=True, exist_ok=True)
    published_at = _resolve_published_at(
        args.state_directory,
        args.published_at,
    )
    if args.mode == "prepare":
        hand_fixture_to_runtime(
            args.artifact_directory,
            args.state_directory,
            uid=args.runtime_uid,
            gid=args.runtime_gid,
        )
        return
    database_url = args.database_url or os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("Acceptance pipeline requires --database-url or DATABASE_URL")
    metadata = asyncio.run(
        run_browser_acceptance_pipeline(
            database_url=database_url,
            capacity_manifest_path=args.capacity_manifest_path,
            artifact_directory=args.artifact_directory,
            state_directory=args.state_directory,
            published_at=published_at,
            timeout_seconds=args.timeout_seconds,
        )
    )
    print(metadata.model_dump_json())


if __name__ == "__main__":
    main()
