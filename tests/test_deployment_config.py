from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FOOD_SEARCH_ENV_DEFAULTS = {
    "FOOD_SEARCH_RATE_LIMIT_ATTEMPTS": "120",
    "FOOD_SEARCH_RATE_LIMIT_WINDOW_SECONDS": "60",
    "FOOD_SEARCH_STATEMENT_TIMEOUT_MS": "500",
    "FOOD_SEARCH_CURSOR_SIGNING_KEYS": ("v1:opennosh-development-search-cursor-key-2026"),
    "FOOD_SEARCH_CURSOR_LIFETIME_SECONDS": "900",
    "FOOD_SEARCH_SNAPSHOT_REFRESH_SECONDS": "300",
    "FOOD_SEARCH_SNAPSHOT_RETENTION_SECONDS": "1200",
    "FOOD_SEARCH_SNAPSHOT_BUILD_TIMEOUT_MS": "30000",
}


def test_food_search_environment_is_wired_from_template_through_compose() -> None:
    environment = {
        key: value
        for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "=" in line
        for key, value in [line.split("=", 1)]
    }
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    for key, default in FOOD_SEARCH_ENV_DEFAULTS.items():
        assert environment[key] == default
        assert f"{key}: ${{{key}:-{default}}}" in compose
