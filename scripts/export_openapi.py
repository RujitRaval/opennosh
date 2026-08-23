from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from opennosh_api.main import create_app


def export_openapi(destination: Path) -> dict[str, Any]:
    repository_version = (Path(__file__).resolve().parents[1] / "VERSION").read_text().strip()
    schema = create_app(app_version=repository_version).openapi()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(schema, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return schema


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the canonical opennosh OpenAPI contract.")
    parser.add_argument(
        "destination",
        nargs="?",
        type=Path,
        default=Path("web/lib/generated/openapi.json"),
    )
    args = parser.parse_args()
    schema = export_openapi(args.destination)
    print(
        f"Exported contract {schema['info']['x-opennosh-contract-version']} to {args.destination}"
    )


if __name__ == "__main__":
    main()
