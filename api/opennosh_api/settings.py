from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, PositiveFloat, PositiveInt, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from opennosh_api.capacity import JobRole, ProcessRole
from opennosh_api.targets.constants import (
    DEFAULT_TARGET_KCAL_FLOOR,
    MAX_KCAL,
    TARGET_QUANTUM,
)


class Settings(BaseSettings):
    app_environment: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+asyncpg://opennosh:opennosh@localhost:5432/opennosh"
    web_database_url: str | None = None
    publication_database_url: str | None = None
    evidence_database_url: str | None = None
    projection_database_url: str | None = None
    reconciler_database_url: str | None = None
    scheduler_database_url: str | None = None
    migration_database_url: str | None = None
    administration_database_url: str | None = None
    database_capacity_manifest_path: Path | None = None
    database_healthcheck_timeout_seconds: PositiveFloat = 2.0
    session_lifetime_seconds: PositiveInt = 43_200
    trusted_web_proxy_token: SecretStr | None = Field(default=None, min_length=32)
    auth_rate_limit_attempts: PositiveInt = 5
    auth_rate_limit_window_seconds: PositiveInt = 300
    auth_rate_limit_retention_seconds: PositiveInt = 86_400
    food_search_rate_limit_attempts: PositiveInt = 120
    food_search_rate_limit_window_seconds: PositiveInt = 60
    food_search_statement_timeout_ms: PositiveInt = 500
    food_search_cursor_signing_keys: SecretStr = SecretStr(
        "v1:opennosh-development-search-cursor-key-2026"
    )
    food_search_cursor_lifetime_seconds: PositiveInt = 900
    food_search_snapshot_refresh_seconds: PositiveInt = 300
    food_search_snapshot_retention_seconds: PositiveInt = 1_200
    food_search_snapshot_build_timeout_ms: PositiveInt = 30_000
    public_commons_latest_pointer_path: Path | None = None
    public_commons_release_directory: Path | None = None
    public_commons_checkpoint_path: Path | None = None
    public_commons_projection_path: Path | None = None
    public_commons_refresh_seconds: PositiveFloat = 5.0
    public_commons_revalidation_url: str | None = None
    public_commons_revalidation_token: SecretStr | None = Field(
        default=None, min_length=32
    )
    public_commons_revalidation_allowed_hosts: str = "web,localhost,127.0.0.1,::1"
    public_commons_verifying_keys: str = "development:Laz0b4AQMs1TfE090-MRSPubDqxptaEJ-HZXEsZe_lw"
    public_commons_stale_after_seconds: PositiveInt = 300
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
    community_export_rate_limit_attempts: PositiveInt = 10
    community_export_rate_limit_window_seconds: PositiveInt = 60
    community_export_statement_timeout_ms: PositiveInt = 2_000
    private_export_rate_limit_attempts: PositiveInt = 10
    private_export_rate_limit_window_seconds: PositiveInt = 60
    private_export_statement_timeout_ms: PositiveInt = 5_000
    public_export_concurrency_limit: PositiveInt = Field(default=2, le=8)
    private_export_concurrency_limit: PositiveInt = Field(default=1, le=4)
    export_capacity_wait_seconds: PositiveFloat = 1.0
    public_export_response_timeout_seconds: PositiveFloat = 300.0
    private_export_response_timeout_seconds: PositiveFloat = 1_800.0
    target_kcal_floor: Decimal = Field(default=DEFAULT_TARGET_KCAL_FLOOR, gt=0, le=MAX_KCAL)

    @field_validator("food_search_cursor_signing_keys")
    @classmethod
    def validate_food_search_cursor_signing_keys(cls, value: SecretStr) -> SecretStr:
        from opennosh_api.foods.cursors import SearchCursorKeyRing

        SearchCursorKeyRing.from_secret(value)
        return value

    @field_validator("public_commons_verifying_keys")
    @classmethod
    def validate_public_commons_verifying_keys(cls, value: str) -> str:
        from opennosh_api.public_commons.manifests import ManifestKeyRing

        ManifestKeyRing.from_config(value)
        return value

    @field_validator("public_commons_revalidation_url")
    @classmethod
    def validate_public_commons_revalidation_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError(
                "Public commons revalidation URL must be a safe HTTP URL"
            ) from error
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or (port is not None and not 1 <= port <= 65_535)
            or parsed.path != "/api/internal/public-commons/revalidate"
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Public commons revalidation URL must be a safe HTTP URL")
        return value

    @field_validator("public_commons_revalidation_token", mode="before")
    @classmethod
    def blank_public_commons_revalidation_token_is_disabled(
        cls, value: object
    ) -> object:
        return None if value == "" else value

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
            or any(character.isspace() or character in "<>\"'\\" for character in normalized)
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
        if (self.public_commons_latest_pointer_path is None) != (
            self.public_commons_release_directory is None
        ):
            raise ValueError(
                "Public commons latest pointer and release directory must be configured together"
            )
        if (
            self.app_environment == "production"
            and self.public_commons_latest_pointer_path is not None
            and self.public_commons_checkpoint_path is None
        ):
            raise ValueError("Production public commons reads require a durable checkpoint path")
        if (
            self.app_environment == "production"
            and self.public_commons_latest_pointer_path is not None
            and self.public_commons_projection_path is None
        ):
            raise ValueError("Production public commons reads require a durable projection path")
        if (
            self.app_environment == "production"
            and self.public_commons_latest_pointer_path is not None
            and self.public_commons_checkpoint_path is not None
            and self.public_commons_projection_path is not None
            and self.public_commons_release_directory is not None
        ):
            pointer_path = self.public_commons_latest_pointer_path.resolve(strict=False)
            release_directory = self.public_commons_release_directory.resolve(strict=False)
            checkpoint_path = self.public_commons_checkpoint_path.resolve(strict=False)
            projection_path = self.public_commons_projection_path.resolve(strict=False)
            checkpoint_lock_path = checkpoint_path.with_suffix(
                f"{checkpoint_path.suffix}.lock"
            )
            projection_lock_path = projection_path.with_suffix(
                f"{projection_path.suffix}.lock"
            )
            state_paths = {
                checkpoint_path,
                checkpoint_lock_path,
                projection_path,
                projection_lock_path,
            }
            if len(state_paths) != 4:
                raise ValueError(
                    "Public commons checkpoint and projection state paths must be distinct"
                )
            for state_path in state_paths:
                if state_path == pointer_path or state_path.is_relative_to(release_directory):
                    raise ValueError(
                        "Public commons durable state paths must be separate from signed artifacts"
                    )
        if (
            self.public_commons_revalidation_url is not None
            and self.public_commons_revalidation_token is None
        ):
            raise ValueError("Public commons edge revalidation requires a scoped token")
        if self.public_commons_revalidation_url is not None:
            allowed_hosts = {
                host.strip().casefold()
                for host in self.public_commons_revalidation_allowed_hosts.split(",")
                if host.strip()
            }
            callback_host = urlsplit(self.public_commons_revalidation_url).hostname
            if callback_host is None or callback_host.casefold() not in allowed_hosts:
                raise ValueError("Public commons revalidation host is not allowlisted")
        if (
            self.app_environment == "production"
            and self.public_commons_revalidation_token is not None
            and self.public_commons_revalidation_token.get_secret_value()
            == "opennosh-local-public-commons-revalidation-token-2026"
        ):
            raise ValueError("Production requires a unique public commons revalidation token")
        if (
            self.food_search_snapshot_refresh_seconds >= self.food_search_snapshot_retention_seconds
            or self.food_search_cursor_lifetime_seconds
            > self.food_search_snapshot_retention_seconds
        ):
            raise ValueError("Search snapshot retention must cover refresh and cursor lifetimes")
        if (
            self.app_environment == "production"
            and self.food_search_cursor_signing_keys.get_secret_value()
            == "v1:opennosh-development-search-cursor-key-2026"
        ):
            raise ValueError("Production requires unique food search cursor signing keys")
        if (
            self.app_environment == "production"
            and self.public_commons_latest_pointer_path is not None
            and self.public_commons_verifying_keys
            == "development:Laz0b4AQMs1TfE090-MRSPubDqxptaEJ-HZXEsZe_lw"
        ):
            raise ValueError("Production requires approved public commons verifying keys")
        if (
            self.app_environment == "production"
            and self.trusted_web_proxy_token is not None
            and self.trusted_web_proxy_token.get_secret_value()
            in {
                "opennosh-local-web-proxy-token-2026",
                "replace-with-a-unique-32-character-secret",
            }
        ):
            raise ValueError("Production requires a unique trusted web proxy token")
        longest_window = max(
            self.auth_rate_limit_window_seconds,
            self.food_search_rate_limit_window_seconds,
            self.open_food_facts_lookup_rate_limit_window_seconds,
            self.open_food_facts_upstream_rate_limit_window_seconds,
            self.open_food_facts_export_rate_limit_window_seconds,
            self.exercise_search_rate_limit_window_seconds,
            self.exercise_export_rate_limit_window_seconds,
            self.community_export_rate_limit_window_seconds,
            self.private_export_rate_limit_window_seconds,
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

    def process_database_url(self, role: ProcessRole | JobRole) -> str:
        role_urls = {
            ProcessRole.WEB: self.web_database_url,
            ProcessRole.PUBLICATION: self.publication_database_url,
            ProcessRole.EVIDENCE: self.evidence_database_url,
            ProcessRole.PROJECTION: self.projection_database_url,
            ProcessRole.RECONCILER: self.reconciler_database_url,
            ProcessRole.SCHEDULER: self.scheduler_database_url,
            JobRole.MIGRATION: self.migration_database_url,
            JobRole.ADMINISTRATION: self.administration_database_url,
        }
        role_url = role_urls[role]
        if isinstance(role_url, str) and role_url:
            return role_url
        if self.app_environment == "production":
            raise ValueError(f"Production requires {role.value.upper()}_DATABASE_URL")
        return self.database_url

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
