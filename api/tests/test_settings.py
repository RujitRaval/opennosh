import pytest
from opennosh_api.settings import Settings
from pydantic import ValidationError


def test_settings_have_safe_development_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.database_url.endswith("@localhost:5432/opennosh")
    assert settings.database_healthcheck_timeout_seconds == 2.0


def test_settings_read_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://test:test@database.example.test:5432/opennosh_test",
    )
    monkeypatch.setenv("DATABASE_HEALTHCHECK_TIMEOUT_SECONDS", "4.5")

    settings = Settings(_env_file=None)

    assert settings.database_url.endswith("@database.example.test:5432/opennosh_test")
    assert settings.database_healthcheck_timeout_seconds == 4.5


def test_settings_reject_a_non_positive_healthcheck_timeout() -> None:
    with pytest.raises(ValidationError):
        Settings(database_healthcheck_timeout_seconds=0, _env_file=None)
