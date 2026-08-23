from __future__ import annotations

import uvicorn


def main() -> None:
    uvicorn.run(
        "opennosh_api.main:app",
        host="0.0.0.0",
        port=8000,
        access_log=False,
    )
