#!/usr/bin/env python3
"""Generate the Python SDK operation policy from canonical contracts."""

from __future__ import annotations

import json
import pprint
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OPENAPI_PATH = ROOT / "web/lib/generated/openapi.json"
COMPATIBILITY_PATH = ROOT / "config/developer-compatibility.v1.json"
OUTPUT_PATH = ROOT / "api/opennosh_api/sdk/_generated.py"


def generated_source(openapi: dict[str, Any], compatibility: dict[str, Any]) -> str:
    policies: dict[str, dict[str, Any]] = {}
    for declared in compatibility["public_operations"]:
        operation = openapi["paths"][declared["path"]]["get"]
        policies[declared["path"]] = {
            "accepted_media_types": sorted(operation["responses"]["200"]["content"]),
            "media_type": declared["media_type"],
            "max_response_bytes": declared["max_response_bytes"],
            "path_parameters": {
                parameter["name"]: parameter["schema"]
                for parameter in operation.get("parameters", [])
                if parameter["in"] == "path"
            },
        }
    encoded = pprint.pformat(policies, sort_dicts=True, width=100)
    return (
        '"""Generated from the developer compatibility manifest and OpenAPI. Do not edit."""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "from typing import Any, Final\n"
        "\n"
        f"PUBLIC_OPERATION_POLICIES: Final[dict[str, dict[str, Any]]] = {encoded}\n"
    )


def main() -> int:
    openapi = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    compatibility = json.loads(COMPATIBILITY_PATH.read_text(encoding="utf-8"))
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(generated_source(openapi, compatibility), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
