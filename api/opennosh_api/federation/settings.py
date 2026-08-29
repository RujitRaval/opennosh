from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import PositiveInt, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from opennosh_api.federation.contracts import FederationScope

_GITHUB_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")


class FederationOperatorSettings(BaseSettings):
    """One-ceremony settings loaded only by the administration CLI."""

    model_config = SettingsConfigDict(
        env_prefix="FEDERATION_",
        case_sensitive=False,
        extra="ignore",
    )

    administration_database_url: str
    database_capacity_manifest_path: Path | None = None
    allowed_github_account_id: PositiveInt
    allowed_github_login: str
    allowed_repository_id: PositiveInt
    allowed_repository: str
    allowed_pack_id: str
    allowed_public_origin: str
    inviter_actor_id: UUID
    github_app_id: PositiveInt
    github_app_private_key: SecretStr

    @field_validator("allowed_github_login")
    @classmethod
    def validate_login(cls, value: str) -> str:
        if not _GITHUB_LOGIN.fullmatch(value):
            raise ValueError("Federation GitHub login is invalid")
        return value

    @field_validator("github_app_private_key")
    @classmethod
    def validate_private_key(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if "BEGIN RSA PRIVATE KEY" not in raw and "BEGIN PRIVATE KEY" not in raw:
            raise ValueError("Federation GitHub App private key is invalid")
        return value

    @field_validator("allowed_public_origin")
    @classmethod
    def validate_public_origin(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname is None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("Federation public origin must be an HTTPS origin")
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_scope(self) -> FederationOperatorSettings:
        FederationScope(
            github_account_id=self.allowed_github_account_id,
            github_login=self.allowed_github_login,
            repository_id=self.allowed_repository_id,
            repository=self.allowed_repository,
            pack_id=self.allowed_pack_id,
        )
        if not self.administration_database_url.startswith(
            ("postgresql+asyncpg://", "postgresql://")
        ):
            raise ValueError("Federation administration database URL must be PostgreSQL")
        return self

    @property
    def allowed_scope(self) -> FederationScope:
        return FederationScope(
            github_account_id=self.allowed_github_account_id,
            github_login=self.allowed_github_login,
            repository_id=self.allowed_repository_id,
            repository=self.allowed_repository,
            pack_id=self.allowed_pack_id,
        )
