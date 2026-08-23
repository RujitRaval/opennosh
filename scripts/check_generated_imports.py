from __future__ import annotations

import re
from pathlib import Path

GENERATED_IMPORT = re.compile(
    r"""(?:from\s+|import\s*\()(?P<quote>["'])(?P<path>[^"']*generated[^"']*)(?P=quote)"""
)
WEB_ROOT = Path("web")
ALLOWED_ROOT = WEB_ROOT / "lib" / "api"


def violations() -> list[str]:
    results: list[str] = []
    for path in sorted(WEB_ROOT.rglob("*")):
        if (
            path.suffix not in {".ts", ".tsx"}
            or "generated" in path.parts
            or "node_modules" in path.parts
            or ".next" in path.parts
        ):
            continue
        source = path.read_text(encoding="utf-8")
        allowed = path.is_relative_to(ALLOWED_ROOT) or path == WEB_ROOT / "lib" / "api.ts"
        if GENERATED_IMPORT.search(source) and not allowed:
            results.append(str(path))
    return results


def main() -> None:
    found = violations()
    if found:
        raise SystemExit(
            "Generated contract imports must stay behind web/lib/api adapters:\n"
            + "\n".join(f"  - {path}" for path in found)
        )
    print("Generated contract imports are isolated behind web/lib/api.")


if __name__ == "__main__":
    main()
