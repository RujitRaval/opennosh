from decimal import Decimal
from functools import lru_cache
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, PositiveFloat, PositiveInt, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from opennosh_api.targets.constants import (
    DEFAULT_TARGET_KCAL_FLOOR,
    MAX_KCAL,
    TARGET_QUANTUM,
)


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
    open_food_facts_enabled: bool = False
    open_food_facts_base_url: str = "https://world.openfoodfacts.org"
    open_food_facts_timeout_seconds: PositiveFloat = 3.0
    open_food_facts_user_agent_contact: str = "https://github.com/RujitRaval/opennosh"
    open_food_facts_lookup_rate_limit_attempts: PositiveInt = Field(default=10, le=15)
    open_food_facts_lookup_rate_limit_window_seconds: PositiveInt = 60
    open_food_facts_upstream_rate_limit_attempts: PositiveInt = Field(default=10, le=15)
    open_food_facts_upstream_rate_limit_window_seconds: PositiveInt = Field(default=60, ge=60)
    open_food_facts_export_rate_limit_attempts: PositiveInt = 10
    open_food_facts_export_rate_limit_window_seconds: PositiveInt = 60
    open_food_facts_export_statement_timeout_ms: PositiveInt = 2_000
    exercise_search_rate_limit_attempts: PositiveInt = 120
    exercise_search_rate_limit_window_seconds: PositiveInt = 60
    exercise_search_statement_timeout_ms: PositiveInt = 500
    exercise_export_rate_limit_attempts: PositiveInt = 10
    exercise_export_rate_limit_window_seconds: PositiveInt = 60
    exercise_export_statement_timeout_ms: PositiveInt = 2_000
    target_kcal_floor: Decimal = Field(default=DEFAULT_TARGET_KCAL_FLOOR, gt=0, le=MAX_KCAL)

    @field_validator("target_kcal_floor")
    @classmethod
    def validate_target_kcal_floor_scale(cls, value: Decimal) -> Decimal:
        if value != value.quantize(TARGET_QUANTUM):
            raise ValueError("Target calorie floor must have at most two decimal places")
        return value

    @field_validator("open_food_facts_base_url")
    @classmethod
    def validate_open_food_facts_base_url(cls, value: str) -> str:
        normalized = value.rstrip("/")
        parsed = urlsplit(normalized)
        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError("Open Food Facts base URL must be a safe HTTPS URL") from error
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or (port is not None and not 1 <= port <= 65_535)
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or any(character.isspace() or character in '<>"\'\\' for character in normalized)
        ):
            raise ValueError("Open Food Facts base URL must be a safe HTTPS URL")
        return normalized

    @field_validator("open_food_facts_user_agent_contact")
    @classmethod
    def validate_open_food_facts_contact(cls, value: str) -> str:
        if not value.isascii() or any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise ValueError("Open Food Facts User-Agent contact must be printable")
        normalized = " ".join(value.split())
        if not 1 <= len(normalized) <= 255:
            raise ValueError("Open Food Facts User-Agent contact must be printable")
        return normalized

    @model_validator(mode="after")
    def validate_rate_limit_retention(self) -> Self:
        longest_window = max(
            self.auth_rate_limit_window_seconds,
            self.food_search_rate_limit_window_seconds,
            self.open_food_facts_lookup_rate_limit_window_seconds,
            self.open_food_facts_upstream_rate_limit_window_seconds,
            self.open_food_facts_export_rate_limit_window_seconds,
            self.exercise_search_rate_limit_window_seconds,
            self.exercise_export_rate_limit_window_seconds,
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
