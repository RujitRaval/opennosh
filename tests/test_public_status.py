from __future__ import annotations

import json
from pathlib import Path

from opennosh_api.public_operations.manifest import (
    PUBLIC_COMPONENT_IDS,
    load_public_status_manifest,
)

from scripts.check_public_status import validate_repository

ROOT = Path(__file__).resolve().parents[1]


def test_public_status_manifest_is_fixed_ordered_and_digest_stable() -> None:
    first = load_public_status_manifest(ROOT / "config/public-status.v1.json")
    second = load_public_status_manifest(ROOT / "config/public-status.v1.json")
    assert tuple(component.component_id for component in first.components) == PUBLIC_COMPONENT_IDS
    assert first.digest == second.digest
    assert len(first.digest) == 64
    assert validate_repository(ROOT) == []


def test_public_status_checker_rejects_inventory_and_freshness_drift(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "schemas").mkdir()
    document = json.loads((ROOT / "config/public-status.v1.json").read_text())
    document["components"][0]["freshness_window_seconds"] = 0
    (tmp_path / "config/public-status.v1.json").write_text(json.dumps(document))
    (tmp_path / "schemas/public-status.schema.json").write_text(
        (ROOT / "schemas/public-status.schema.json").read_text()
    )
    issues = validate_repository(tmp_path)
    assert any("minimum" in issue for issue in issues)
    assert any("manifest contract" in issue for issue in issues)
