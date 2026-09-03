from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

from opennosh_api.sdk import OpenNoshClient

RELEASE_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$")


def run_public_read(
    *,
    target: str | None = None,
    query: str | None = None,
    client: Any | None = None,
) -> dict[str, object]:
    selected_target = target or os.environ.get("OPENNOSH_TARGET", "hosted")
    selected_query = query or os.environ.get("OPENNOSH_QUERY", "rajma")
    reader = client or OpenNoshClient(selected_target)
    search = reader.search_foods(selected_query, limit=1)
    if not search.data.items:
        raise RuntimeError("No public food matched the starter query.")
    match = search.data.items[0]
    detail = reader.get_public_food(match.source.value, match.source_id)
    food = detail.data
    attribution = food.record.attribution
    release_version = food.release.release_version
    expected_path = (
        f"/api/v1/public/releases/{release_version}/foods/"
        f"{match.source.value}/{match.source_id}"
    )
    if (
        food.release.state not in {"verified", "stale"}
        or RELEASE_PATTERN.fullmatch(release_version) is None
        or food.record.source != match.source
        or food.record.source_id != match.source_id
        or food.immutable_url != expected_path
        or food.provenance_url != f"{expected_path}/provenance"
        or not attribution.license.strip()
    ):
        raise RuntimeError("The public detail did not contain bound publication proof.")
    return {
        "schema_version": "1.0",
        "state": "stale_verified" if food.release.state == "stale" else "verified",
        "food": {
            "attribution": (
                attribution.contributed_by or attribution.pack_id or attribution.source.value
            ),
            "license": attribution.license,
            "name": food.record.name,
            "provenance_url": food.provenance_url,
            "release_version": release_version,
            "source": f"{food.record.source.value}:{food.record.source_id}",
        },
    }


def main() -> int:
    try:
        print(json.dumps(run_public_read(), sort_keys=True, separators=(",", ":")))
    except Exception as error:
        code = getattr(error, "code", "unavailable")
        print(f"opennosh starter failed: {code}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
