from decimal import Decimal

import pytest
from opennosh_api.settings import Settings
from pydantic import ValidationError


def test_settings_have_safe_development_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.database_url.endswith("@localhost:5432/opennosh")
    assert settings.database_healthcheck_timeout_seconds == 2.0
    assert settings.food_search_rate_limit_attempts == 120
    assert settings.food_search_rate_limit_window_seconds == 60
    assert settings.food_search_statement_timeout_ms == 500
    assert settings.target_kcal_floor == Decimal("1200")
    assert settings.session_cookie_name == "opennosh_session"
    assert settings.session_cookie_secure is False


def test_production_settings_use_secure_host_only_cookie_names() -> None:
    settings = Settings(app_environment="production", _env_file=None)

    assert settings.session_cookie_name == "__Host-opennosh-session"
    assert settings.csrf_cookie_name == "__Host-opennosh-csrf"
    assert settings.session_cookie_secure is True


def test_settings_read_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://test:test@database.example.test:5432/opennosh_test",
    )
    monkeypatch.setenv("DATABASE_HEALTHCHECK_TIMEOUT_SECONDS", "4.5")
    monkeypatch.setenv("TARGET_KCAL_FLOOR", "1300.50")

    settings = Settings(_env_file=None)

    assert settings.database_url.endswith("@database.example.test:5432/opennosh_test")
    assert settings.database_healthcheck_timeout_seconds == 4.5
    assert settings.target_kcal_floor == Decimal("1300.50")


def test_settings_reject_a_non_positive_healthcheck_timeout() -> None:
    with pytest.raises(ValidationError):
        Settings(database_healthcheck_timeout_seconds=0, _env_file=None)


def test_settings_reject_rate_limit_retention_shorter_than_window() -> None:
    with pytest.raises(ValidationError):
        Settings(
            auth_rate_limit_window_seconds=300,
            auth_rate_limit_retention_seconds=299,
            _env_file=None,
        )
    with pytest.raises(ValidationError):
        Settings(
            food_search_rate_limit_window_seconds=300,
            auth_rate_limit_retention_seconds=299,
            _env_file=None,
        )


def test_settings_reject_an_invalid_target_floor() -> None:
    with pytest.raises(ValidationError):
        Settings(target_kcal_floor=0, _env_file=None)
    with pytest.raises(ValidationError):
        Settings(target_kcal_floor=20_001, _env_file=None)
    with pytest.raises(ValidationError):
        Settings(target_kcal_floor="1200.001", _env_file=None)
