#!/usr/bin/env python3
"""Fail CI when the deployment connection-capacity manifest is invalid."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from opennosh_api.capacity import (
    CapacityManifest,
    ProcessRole,
    load_capacity_manifest,
    preflight_report,
)


def validate_benchmark_alignment(root: Path, manifest: CapacityManifest) -> None:
    contract_path = root / "benchmarks/performance/contract.v1.json"
    contract = cast(dict[str, Any], json.loads(contract_path.read_text(encoding="utf-8")))
    launch = next(
        profile for profile in contract["profiles"] if profile["id"] == "launch-reference"
    )
    active_roles = {role for role, budget in manifest.roles.items() if budget.replicas > 0}
    if active_roles != {ProcessRole.WEB, ProcessRole.PUBLICATION}:
        raise ValueError("Production must activate exactly the web and bounded publication roles")
    web = manifest.roles[ProcessRole.WEB]
    if int(launch["concurrency"]) > web.worker_concurrency:
        raise ValueError("Launch benchmark concurrency exceeds the web role concurrency budget")
    capacity_gate = float(contract["gates"]["capacity"]["max_connection_utilization"])
    benchmark_connection_limit = int(manifest.postgresql_connection_ceiling * capacity_gate)
    application_capacity = manifest.postgresql_connection_ceiling - manifest.reserved_headroom.total
    if benchmark_connection_limit != application_capacity:
        raise ValueError("T15 connection-utilization gate must preserve T16 reserved headroom")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    manifest = load_capacity_manifest(root / "config/database-capacity.v1.json")
    validate_benchmark_alignment(root, manifest)
    report = preflight_report(manifest)
    print(
        "Database capacity: "
        f"{report['total_committed_connections']}/"
        f"{report['postgresql_connection_ceiling']} committed; "
        f"{report['uncommitted_connections']} uncommitted; "
        "T15 launch gate aligned"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
