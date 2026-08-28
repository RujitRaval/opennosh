from __future__ import annotations

import re
from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ACCOUNT_ID = re.compile(r"^[0-9a-f]{32}$")
_BUCKET = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])$")
_GIT_HASH = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
FIRST_CONTRIBUTION_BUCKET = "opennosh-public-commons"


class FirstContributionOperatorSettings(BaseSettings):
    """Credentials loaded only by the one-use administration command."""

    model_config = SettingsConfigDict(
        env_prefix="FIRST_CONTRIBUTION_",
        case_sensitive=False,
        extra="ignore",
    )

    administration_database_url: str
    database_capacity_manifest_path: Path | None = None
    reviewed_base_commit: str
    reviewed_package_digest: str
    r2_account_id: str
    r2_bucket: str
    r2_access_key_id: SecretStr
    r2_secret_access_key: SecretStr

    @field_validator("r2_account_id")
    @classmethod
    def validate_account_id(cls, value: str) -> str:
        if not _ACCOUNT_ID.fullmatch(value):
            raise ValueError("First-contribution R2 account ID is invalid")
        return value

    @field_validator("r2_bucket")
    @classmethod
    def validate_bucket(cls, value: str) -> str:
        if not _BUCKET.fullmatch(value) or value != FIRST_CONTRIBUTION_BUCKET:
            raise ValueError("First-contribution R2 bucket is invalid")
        return value

    @field_validator("reviewed_base_commit")
    @classmethod
    def validate_reviewed_base_commit(cls, value: str) -> str:
        if not _GIT_HASH.fullmatch(value):
            raise ValueError("First-contribution reviewed base commit is invalid")
        return value

    @field_validator("reviewed_package_digest")
    @classmethod
    def validate_reviewed_package_digest(cls, value: str) -> str:
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("First-contribution reviewed package digest is invalid")
        return value

    @field_validator("r2_access_key_id", "r2_secret_access_key")
    @classmethod
    def validate_secret(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if not raw.strip() or any(character.isspace() for character in raw):
            raise ValueError("First-contribution R2 credential is invalid")
        return value
