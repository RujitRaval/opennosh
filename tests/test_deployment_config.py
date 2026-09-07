from __future__ import annotations

import ast
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

PUBLIC_ROOT_ENABLED_DEFAULT = "true"


def test_alembic_setup_preserves_application_loggers() -> None:
    module = ast.parse((ROOT / "api/alembic/env.py").read_text(encoding="utf-8"))
    file_config_calls = [
        node
        for node in ast.walk(module)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "fileConfig"
    ]

    assert len(file_config_calls) == 1
    disable_keyword = next(
        (
            keyword
            for keyword in file_config_calls[0].keywords
            if keyword.arg == "disable_existing_loggers"
        ),
        None,
    )
    assert disable_keyword is not None
    assert isinstance(disable_keyword.value, ast.Constant)
    assert disable_keyword.value.value is False


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


def test_public_root_rollback_is_wired_from_template_through_compose() -> None:
    environment = {
        key: value
        for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "=" in line
        for key, value in [line.split("=", 1)]
    }
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert environment["OPENNOSH_PUBLIC_ROOT_ENABLED"] == PUBLIC_ROOT_ENABLED_DEFAULT
    assert (
        "OPENNOSH_PUBLIC_ROOT_ENABLED: "
        f"${{OPENNOSH_PUBLIC_ROOT_ENABLED:-{PUBLIC_ROOT_ENABLED_DEFAULT}}}"
    ) in compose


def test_compose_ci_asserts_the_current_alembic_head() -> None:
    workflow = (ROOT / ".github/workflows/quality.yml").read_text(encoding="utf-8")

    assert "ScriptDirectory.from_config" in workflow
    assert "get_current_head()" in workflow
    assert "SELECT version_num FROM alembic_version" in workflow
    assert 'test "$actual_head" = "$expected_head"' in workflow
