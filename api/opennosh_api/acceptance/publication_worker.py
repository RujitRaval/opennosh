from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from opennosh_api.acceptance.adapters import acceptance_publication_adapter_registry
from opennosh_api.jobs.worker import run_publication_worker


def main() -> None:
    environment = os.environ.get("APP_ENVIRONMENT", "").lower()
    enabled = os.environ.get("OPENNOSH_ACCEPTANCE_FIXTURES") == "1"
    if environment not in {"development", "test", "testing"} or not enabled:
        raise SystemExit(
            "Acceptance publication adapters require a development/test environment and opt-in"
        )
    state_root = Path(_required_environment("ACCEPTANCE_STATE_DIRECTORY"))
    artifact_root = Path(_required_environment("ACCEPTANCE_ARTIFACT_DIRECTORY"))
    published_at = datetime.fromisoformat(
        (state_root / "published-at.txt").read_text().strip().replace("Z", "+00:00")
    )
    if published_at.tzinfo is None or published_at.utcoffset() is None:
        raise SystemExit("Acceptance publication clock must include a timezone")
    registry = acceptance_publication_adapter_registry(
        state_root=state_root,
        artifact_root=artifact_root,
        clock=lambda: published_at,
    )
    raise SystemExit(run_publication_worker(registry))


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} is required")
    return value


if __name__ == "__main__":
    main()
