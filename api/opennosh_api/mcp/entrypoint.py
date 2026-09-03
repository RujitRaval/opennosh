"""Console entrypoint for the preview read-only MCP stdio server."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence

from mcp.server.stdio import stdio_server

from opennosh_api.mcp.server import build_server


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="opennosh-mcp",
        description="Run the preview read-only OpenNosh MCP server over stdio.",
    )
    parser.add_argument(
        "--target",
        default=os.environ.get("OPENNOSH_MCP_TARGET", "hosted"),
        help="hosted or one validated self-hosted origin",
    )
    return parser


async def _serve(target: str) -> None:
    server = build_server(target)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        asyncio.run(_serve(args.target))
    except (TypeError, ValueError):
        sys.stderr.write("opennosh-mcp: target must be hosted or a validated HTTP(S) origin\n")
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover - console script owns direct execution
    raise SystemExit(main())
