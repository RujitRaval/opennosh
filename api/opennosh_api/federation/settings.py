from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import PositiveInt, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from opennosh_api.federation.contracts import FederationScope

_GITHUB_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")


class FederationOperatorSettings(BaseSettings):
    """Reviewed scope settings loaded only by the administration CLI."""

    model_config = SettingsConfigDict(
        env_prefix="FEDERATION_",
        case_sensitive=False,
        extra="ignore",
    )

    administration_database_url: str
    database_capacity_manifest_path: Path | None = None
    allowed_scopes_json: SecretStr | None = None
    allowed_github_account_id: PositiveInt | None = None
    allowed_github_login: str | None = None
    allowed_repository_id: PositiveInt | None = None
    allowed_repository: str | None = None
    allowed_pack_id: str | None = None
    allowed_public_origin: str
    inviter_actor_id: UUID
    github_app_id: PositiveInt
    github_app_private_key: SecretStr
    ingestion_enabled: bool = False
    projection_enabled: bool = False
    manifest_verifying_keys: SecretStr | None = None

    @field_validator("allowed_github_login")
    @classmethod
    def validate_login(cls, value: str | None) -> str | None:
        if value is None:
            return None
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
    def validate_scopes(self) -> FederationOperatorSettings:
        self._configured_scopes()
        if not self.administration_database_url.startswith(
            ("postgresql+asyncpg://", "postgresql://")
        ):
            raise ValueError("Federation administration database URL must be PostgreSQL")
        if self.ingestion_enabled and self.manifest_verifying_keys is None:
            raise ValueError(
                "Federation manifest verifying keys are required when ingestion is enabled"
            )
        return self

    @property
    def allowed_scopes(self) -> tuple[FederationScope, ...]:
        return self._configured_scopes()

    @property
    def allowed_scope(self) -> FederationScope:
        """Keep the T33 single-scope accessor for legacy operator callers."""

        scopes = self.allowed_scopes
        if len(scopes) != 1:
            raise ValueError("Federation operator configuration contains multiple scopes")
        return scopes[0]

    def _configured_scopes(self) -> tuple[FederationScope, ...]:
        legacy = (
            self.allowed_github_account_id,
            self.allowed_github_login,
            self.allowed_repository_id,
            self.allowed_repository,
            self.allowed_pack_id,
        )
        has_legacy = any(value is not None for value in legacy)
        if self.allowed_scopes_json is not None and has_legacy:
            raise ValueError(
                "Federation scope allowlist and legacy scope fields are mutually exclusive"
            )
        if self.allowed_scopes_json is None:
            if not all(value is not None for value in legacy):
                raise ValueError("Federation legacy scope configuration is incomplete")
            assert self.allowed_github_account_id is not None
            assert self.allowed_github_login is not None
            assert self.allowed_repository_id is not None
            assert self.allowed_repository is not None
            assert self.allowed_pack_id is not None
            scopes: tuple[FederationScope, ...] = (
                FederationScope(
                    github_account_id=self.allowed_github_account_id,
                    github_login=self.allowed_github_login,
                    repository_id=self.allowed_repository_id,
                    repository=self.allowed_repository,
                    pack_id=self.allowed_pack_id,
                ),
            )
        else:
            try:
                payload = json.loads(self.allowed_scopes_json.get_secret_value())
                if not isinstance(payload, list):
                    raise TypeError
                scopes = tuple(FederationScope.model_validate(item) for item in payload)
            except (json.JSONDecodeError, TypeError, ValidationError) as error:
                raise ValueError("Federation scope allowlist JSON is invalid") from error
            if not 1 <= len(scopes) <= 32:
                raise ValueError("Federation scope allowlist must contain 1 to 32 scopes")
        _validate_scope_identities(scopes)
        return scopes


def _validate_scope_identities(scopes: tuple[FederationScope, ...]) -> None:
    seen_scope_keys: set[tuple[int, str]] = set()
    account_ids: dict[int, str] = {}
    account_logins: dict[str, int] = {}
    repository_ids: dict[int, str] = {}
    repositories: dict[str, int] = {}
    pack_scopes: dict[str, tuple[int, str]] = {}
    for scope in scopes:
        if not _GITHUB_LOGIN.fullmatch(scope.github_login):
            raise ValueError("Federation GitHub login is invalid")
        scope_key = (scope.repository_id, scope.pack_id)
        account_login = scope.github_login.casefold()
        repository = scope.repository.casefold()
        if scope_key in seen_scope_keys:
            raise ValueError("Federation scope allowlist contains a duplicate scope")
        if (
            account_ids.get(scope.github_account_id, account_login) != account_login
            or account_logins.get(account_login, scope.github_account_id)
            != scope.github_account_id
            or repository_ids.get(scope.repository_id, repository) != repository
            or repositories.get(repository, scope.repository_id) != scope.repository_id
            or pack_scopes.get(scope.pack_id, scope_key) != scope_key
        ):
            raise ValueError("Federation scope allowlist contains conflicting identities")
        seen_scope_keys.add(scope_key)
        account_ids[scope.github_account_id] = account_login
        account_logins[account_login] = scope.github_account_id
        repository_ids[scope.repository_id] = repository
        repositories[repository] = scope.repository_id
        pack_scopes[scope.pack_id] = scope_key
