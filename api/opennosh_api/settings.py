from functools import lru_cache
from typing import Literal, Self

from pydantic import PositiveFloat, PositiveInt, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_environment: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+asyncpg://opennosh:opennosh@localhost:5432/opennosh"
    database_healthcheck_timeout_seconds: PositiveFloat = 2.0
    session_lifetime_seconds: PositiveInt = 43_200
    auth_rate_limit_attempts: PositiveInt = 5
    auth_rate_limit_window_seconds: PositiveInt = 300
    auth_rate_limit_retention_seconds: PositiveInt = 86_400
    food_search_rate_limit_attempts: PositiveInt = 120
    food_search_rate_limit_window_seconds: PositiveInt = 60
    food_search_statement_timeout_ms: PositiveInt = 500

    @model_validator(mode="after")
    def validate_rate_limit_retention(self) -> Self:
        longest_window = max(
            self.auth_rate_limit_window_seconds,
            self.food_search_rate_limit_window_seconds,
        )
        if self.auth_rate_limit_retention_seconds < longest_window:
            raise ValueError("Rate-limit retention must cover every configured window")
        return self

    @property
    def session_cookie_name(self) -> str:
        if self.app_environment == "production":
            return "__Host-opennosh-session"
        return "opennosh_session"

    @property
    def session_cookie_secure(self) -> bool:
        return self.app_environment == "production"

    @property
    def csrf_cookie_name(self) -> str:
        if self.app_environment == "production":
            return "__Host-opennosh-csrf"
        return "opennosh_csrf"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
