import json
import re
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, PositiveFloat, PositiveInt, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from opennosh_api.capacity import JobRole, ProcessRole
from opennosh_api.nonproduction_keys import (
    NON_PRODUCTION_KEY_IDS,
    NON_PRODUCTION_PUBLIC_KEYS,
)
from opennosh_api.targets.constants import (
    DEFAULT_TARGET_KCAL_FLOOR,
    MAX_KCAL,
    TARGET_QUANTUM,
)


def _manifest_key_config_uses_nonproduction_key(value: str) -> bool:
    entries = (entry.partition(":") for entry in value.split(","))
    return any(
        key_id in NON_PRODUCTION_KEY_IDS or encoded in NON_PRODUCTION_PUBLIC_KEYS
        for key_id, _, encoded in entries
    )


_R2_BUCKET = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])$")


def _receipt_key_config_uses_nonproduction_key(value: SecretStr) -> bool:
    payload = json.loads(value.get_secret_value())
    return any(
        key_id in NON_PRODUCTION_KEY_IDS
        or (isinstance(encoded, str) and encoded in NON_PRODUCTION_PUBLIC_KEYS)
        for key_id, encoded in payload.items()
    )


class Settings(BaseSettings):
    app_environment: Literal["development", "test", "production"] = "development"
    process_role: ProcessRole | JobRole | None = None
    database_url: str = "postgresql+asyncpg://opennosh:opennosh@localhost:5432/opennosh"
    web_database_url: str | None = None
    publication_database_url: str | None = None
    evidence_database_url: str | None = None
    evidence_private_source_directory: Path | None = None
    evidence_immutable_directory: Path | None = None
    evidence_verifying_keys: SecretStr = SecretStr("{}")
    evidence_uploads_enabled: bool = False
    evidence_sanitization_enabled: bool = False
    evidence_upload_max_bytes: PositiveInt = Field(default=10_485_760, le=10_485_760)
    evidence_upload_ttl_seconds: PositiveInt = Field(default=600, le=600)
    evidence_upload_observation_concurrency: PositiveInt = Field(default=4, le=8)
    evidence_upload_issue_account_attempts: PositiveInt = Field(default=20, le=20)
    evidence_upload_issue_draft_attempts: PositiveInt = Field(default=6, le=6)
    evidence_upload_complete_account_attempts: PositiveInt = Field(default=30, le=30)
    evidence_upload_complete_draft_attempts: PositiveInt = Field(default=12, le=12)
    evidence_upload_attach_account_attempts: PositiveInt = Field(default=12, le=12)
    evidence_upload_attach_draft_attempts: PositiveInt = Field(default=6, le=6)
    evidence_upload_outstanding_account_limit: PositiveInt = Field(default=5, le=5)
    evidence_upload_outstanding_draft_limit: PositiveInt = Field(default=2, le=2)
    evidence_upload_rate_limit_window_seconds: PositiveInt = Field(default=3600, le=3600)
    governance_steward_ui_enabled: bool = False
    governance_mutations_enabled: bool = False
    governance_public_decisions_enabled: bool = False
    evidence_quarantine_endpoint: str | None = None
    evidence_quarantine_region: str | None = None
    evidence_quarantine_bucket: str | None = None
    evidence_quarantine_access_key_id: SecretStr | None = None
    evidence_quarantine_secret_access_key: SecretStr | None = None
    evidence_sanitized_endpoint: str | None = None
    evidence_sanitized_region: str | None = None
    evidence_sanitized_bucket: str | None = None
    evidence_sanitized_access_key_id: SecretStr | None = None
    evidence_sanitized_secret_access_key: SecretStr | None = None
    evidence_scanner_adapter: Literal["deterministic_allow", "http"] | None = None
    evidence_scanner_endpoint: str | None = None
    evidence_scanner_bearer_token: SecretStr | None = None
    evidence_scanner_timeout_seconds: PositiveFloat = Field(default=5.0, le=10.0)
    evidence_immutable_endpoint: str | None = None
    evidence_immutable_region: str | None = None
    evidence_immutable_bucket: str | None = None
    evidence_immutable_access_key_id: SecretStr | None = None
    evidence_immutable_secret_access_key: SecretStr | None = None
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
    contribution_patch_rate_limit_attempts: PositiveInt = 120
    contribution_patch_rate_limit_window_seconds: PositiveInt = 60
    contribution_patch_account_rate_limit_attempts: PositiveInt = 240
    contribution_operation_retention_seconds: PositiveInt = Field(default=691_200, ge=604_800)
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
    public_commons_revalidation_token: SecretStr | None = Field(default=None, min_length=32)
    public_commons_revalidation_allowed_hosts: str = "web,localhost,127.0.0.1,::1"
    public_commons_verifying_keys: str = "development:Laz0b4AQMs1TfE090-MRSPubDqxptaEJ-HZXEsZe_lw"
    public_commons_stale_after_seconds: PositiveInt = 300
    public_artifact_directory: Path | None = None
    public_artifact_base_url: str | None = None
    public_artifact_checkpoint_path: Path | None = None
    public_artifact_cache_directory: Path | None = None
    public_artifact_timeout_seconds: PositiveFloat = 3.0
    publication_claims_enabled: bool = False
    publication_continuous_claims_enabled: bool = False
    publication_claim_concurrency: PositiveInt = 1
    publication_preactivation_smoke_enabled: bool = False
    publication_activation_ids: str = ""
    publication_claims_activation_contract_path: Path = Path(
        "config/publication-claims-activation.v1.json"
    )
    render_git_commit: str | None = None
    latest_refresh_enabled: bool = False
    latest_refresh_interval_seconds: PositiveFloat = 3_600.0
    latest_refresh_after_seconds: PositiveInt = 72_000
    latest_pointer_lifetime_seconds: PositiveInt = 82_800
    online_manifest_signing_key_id: str | None = None
    online_manifest_signing_key: SecretStr | None = None
    online_receipt_signing_key_id: str | None = None
    online_receipt_signing_key: SecretStr | None = None
    github_forge_repository_id: PositiveInt | None = None
    github_forge_app_id: PositiveInt | None = None
    github_forge_installation_id: PositiveInt | None = None
    github_forge_private_key: SecretStr | None = None
    github_attester_app_id: PositiveInt | None = None
    github_attester_installation_id: PositiveInt | None = None
    github_attester_private_key: SecretStr | None = None
    publication_artifact_bucket: str | None = None
    r2_account_id: str | None = None
    r2_bucket: str | None = None
    r2_access_key_id: SecretStr | None = None
    r2_secret_access_key: SecretStr | None = None
    publication_receipt_verifying_keys: SecretStr = SecretStr(
        '{"development":"Laz0b4AQMs1TfE090-MRSPubDqxptaEJ-HZXEsZe_lw"}'
    )
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

    @field_validator("publication_receipt_verifying_keys")
    @classmethod
    def validate_publication_receipt_verifying_keys(cls, value: SecretStr) -> SecretStr:
        from opennosh_api.publication.receipts import PublicationReceiptKeyRing

        PublicationReceiptKeyRing.from_json(value.get_secret_value())
        return value

    @field_validator("public_artifact_base_url")
    @classmethod
    def validate_public_artifact_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.rstrip("/")
        parsed = urlsplit(normalized)
        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError("Public artifact origin must be a safe HTTPS URL") from error
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or (port is not None and not 1 <= port <= 65_535)
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Public artifact origin must be a safe HTTPS URL")
        return normalized

    @field_validator("r2_bucket")
    @classmethod
    def validate_r2_bucket(cls, value: str | None) -> str | None:
        if value is not None and not _R2_BUCKET.fullmatch(value):
            raise ValueError("R2 bucket name must match Cloudflare naming requirements")
        return value

    @field_validator(
        "evidence_quarantine_endpoint",
        "evidence_sanitized_endpoint",
        "evidence_immutable_endpoint",
    )
    @classmethod
    def validate_evidence_object_endpoint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.rstrip("/")
        parsed = urlsplit(normalized)
        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError("Evidence object-store endpoint must be a safe HTTPS URL") from error
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or (port is not None and not 1 <= port <= 65_535)
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Evidence object-store endpoint must be a safe HTTPS URL")
        return normalized

    @field_validator(
        "evidence_quarantine_bucket",
        "evidence_sanitized_bucket",
        "evidence_immutable_bucket",
    )
    @classmethod
    def validate_evidence_bucket(cls, value: str | None) -> str | None:
        if value is not None and not _R2_BUCKET.fullmatch(value):
            raise ValueError("Evidence bucket name must match Cloudflare naming requirements")
        return value

    @field_validator("evidence_scanner_endpoint")
    @classmethod
    def validate_evidence_scanner_endpoint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError("Evidence scanner endpoint must be a safe HTTPS URL") from error
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or (port is not None and not 1 <= port <= 65_535)
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Evidence scanner endpoint must be a safe HTTPS URL")
        return value

    @field_validator("evidence_verifying_keys")
    @classmethod
    def validate_evidence_verifying_keys(cls, value: SecretStr) -> SecretStr:
        from opennosh_api.evidence.signing import EvidenceVerificationKeyRing

        EvidenceVerificationKeyRing.from_config(value.get_secret_value())
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
            raise ValueError("Public commons revalidation URL must be a safe HTTP URL") from error
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
    def blank_public_commons_revalidation_token_is_disabled(cls, value: object) -> object:
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
        publication_secret_values = (
            self.online_manifest_signing_key_id,
            self.online_manifest_signing_key,
            self.online_receipt_signing_key_id,
            self.online_receipt_signing_key,
            self.github_forge_repository_id,
            self.github_forge_app_id,
            self.github_forge_installation_id,
            self.github_forge_private_key,
            self.github_attester_app_id,
            self.github_attester_installation_id,
            self.github_attester_private_key,
            self.publication_artifact_bucket,
            self.r2_account_id,
            self.r2_bucket,
            self.r2_access_key_id,
            self.r2_secret_access_key,
        )
        has_publication_secrets = any(value is not None for value in publication_secret_values)
        activation_ids = self.publication_activation_ids.split(",")
        if self.publication_claims_enabled:
            if self.publication_continuous_claims_enabled:
                if self.publication_activation_ids:
                    raise ValueError(
                        "Continuous publication claims require "
                        "PUBLICATION_ACTIVATION_IDS to be absent"
                    )
            elif len(activation_ids) != 1 or not activation_ids[0]:
                raise ValueError(
                    "Publication claims require exactly one PUBLICATION_ACTIVATION_IDS value"
                )
            if not self.publication_continuous_claims_enabled:
                try:
                    activation_id = UUID(activation_ids[0])
                except ValueError as error:
                    raise ValueError(
                        "PUBLICATION_ACTIVATION_IDS must contain one canonical UUID"
                    ) from error
                if str(activation_id) != activation_ids[0]:
                    raise ValueError("PUBLICATION_ACTIVATION_IDS must contain one canonical UUID")
        elif self.publication_activation_ids:
            raise ValueError("Publication activation IDs require claims to be enabled")
        elif self.publication_continuous_claims_enabled:
            raise ValueError("Continuous publication claims require claims to be enabled")
        if self.app_environment == "production" and self.process_role is ProcessRole.PUBLICATION:
            if not self.publication_claims_enabled and not self.latest_refresh_enabled:
                raise ValueError("Production publication workers require an enabled runtime mode")
            if self.publication_preactivation_smoke_enabled and self.publication_claims_enabled:
                raise ValueError("Publication preactivation smoke requires claims disabled")
            if self.publication_claims_enabled or self.publication_preactivation_smoke_enabled:
                if not self.latest_refresh_enabled:
                    raise ValueError(
                        "Production publication activation requires latest refresh enabled"
                    )
                from opennosh_api.publication.credentials import (
                    validate_publication_claim_credentials,
                )

                validate_publication_claim_credentials(self)
        elif self.app_environment == "production" and (
            self.publication_claims_enabled
            or self.publication_preactivation_smoke_enabled
            or self.latest_refresh_enabled
            or has_publication_secrets
        ):
            raise ValueError(
                "Publication credentials and modes are restricted to the publication worker"
            )
        if self.latest_refresh_enabled:
            required_refresh_values = {
                "PUBLIC_ARTIFACT_BASE_URL": self.public_artifact_base_url,
                "ONLINE_MANIFEST_SIGNING_KEY_ID": self.online_manifest_signing_key_id,
                "ONLINE_MANIFEST_SIGNING_KEY": self.online_manifest_signing_key,
                "R2_ACCOUNT_ID": self.r2_account_id,
                "R2_BUCKET": self.r2_bucket,
                "R2_ACCESS_KEY_ID": self.r2_access_key_id,
                "R2_SECRET_ACCESS_KEY": self.r2_secret_access_key,
            }
            missing = sorted(key for key, value in required_refresh_values.items() if value is None)
            if missing:
                raise ValueError(
                    "Latest pointer refresh configuration is incomplete: " + ",".join(missing)
                )
            if not (
                self.latest_refresh_interval_seconds
                <= self.latest_pointer_lifetime_seconds - self.latest_refresh_after_seconds
                and self.latest_refresh_after_seconds
                < self.latest_pointer_lifetime_seconds
                <= 86_400
            ):
                raise ValueError(
                    "Latest pointer refresh timing cannot guarantee pre-expiry renewal"
                )
            assert self.online_manifest_signing_key is not None
            assert self.online_manifest_signing_key_id is not None
            from opennosh_api.public.signing import (
                decode_public_key_text,
                load_production_signing_key,
                public_key_text,
            )

            online_key = load_production_signing_key(
                self.online_manifest_signing_key,
                key_id=self.online_manifest_signing_key_id,
            )
            online_public_key = public_key_text(online_key)
            manifest_entries = tuple(
                entry.partition(":") for entry in self.public_commons_verifying_keys.split(",")
            )
            trusted_entry = (self.online_manifest_signing_key_id, ":", online_public_key)
            if trusted_entry not in manifest_entries:
                raise ValueError(
                    "Online manifest signing key must be present in the verifying key ring"
                )
            online_public_key_bytes = decode_public_key_text(online_public_key)
            independent_manifest_keys = tuple(
                decode_public_key_text(encoded)
                for key_id, _, encoded in manifest_entries
                if key_id != self.online_manifest_signing_key_id
            )
            receipt_public_keys = tuple(
                decode_public_key_text(encoded)
                for encoded in json.loads(
                    self.publication_receipt_verifying_keys.get_secret_value()
                ).values()
            )
            if (
                not independent_manifest_keys
                or online_public_key_bytes in independent_manifest_keys
                or online_public_key_bytes in receipt_public_keys
            ):
                raise ValueError(
                    "Online manifest signing key must be independent from offline and receipt keys"
                )
        if self.publication_artifact_bucket is not None and not _R2_BUCKET.fullmatch(
            self.publication_artifact_bucket
        ):
            raise ValueError(
                "Publication artifact bucket does not meet Cloudflare naming requirements"
            )
        if (self.evidence_private_source_directory is None) != (
            self.evidence_immutable_directory is None
        ):
            raise ValueError(
                "Evidence private source and immutable destination must be configured together"
            )
        if (
            self.evidence_private_source_directory is not None
            and self.evidence_immutable_directory is not None
        ):
            source_directory = self.evidence_private_source_directory.resolve(strict=False)
            immutable_directory = self.evidence_immutable_directory.resolve(strict=False)
            if source_directory == immutable_directory:
                raise ValueError(
                    "Evidence private source and immutable destination must be independent"
                )
            if self.app_environment == "production":
                raise ValueError("Production evidence durability requires a non-filesystem adapter")
        evidence_groups = {
            "quarantine": (
                self.evidence_quarantine_endpoint,
                self.evidence_quarantine_region,
                self.evidence_quarantine_bucket,
                self.evidence_quarantine_access_key_id,
                self.evidence_quarantine_secret_access_key,
            ),
            "sanitized": (
                self.evidence_sanitized_endpoint,
                self.evidence_sanitized_region,
                self.evidence_sanitized_bucket,
                self.evidence_sanitized_access_key_id,
                self.evidence_sanitized_secret_access_key,
            ),
            "immutable": (
                self.evidence_immutable_endpoint,
                self.evidence_immutable_region,
                self.evidence_immutable_bucket,
                self.evidence_immutable_access_key_id,
                self.evidence_immutable_secret_access_key,
            ),
        }
        for group_name, group in evidence_groups.items():
            configured_count = sum(value is not None for value in group)
            if configured_count not in {0, len(group)}:
                raise ValueError(
                    f"Evidence {group_name} object-store configuration is incomplete"
                )
        if self.evidence_uploads_enabled and not all(evidence_groups["quarantine"]):
            raise ValueError("Evidence uploads require complete quarantine configuration")
        if self.evidence_uploads_enabled and not self.evidence_sanitization_enabled:
            raise ValueError("Evidence uploads require sanitization to be enabled")
        scanner_http_configured = (
            self.evidence_scanner_endpoint is not None
            and self.evidence_scanner_bearer_token is not None
        )
        if self.evidence_scanner_adapter == "http" and not scanner_http_configured:
            raise ValueError("HTTP evidence scanner configuration is incomplete")
        if self.evidence_scanner_adapter != "http" and (
            self.evidence_scanner_endpoint is not None
            or self.evidence_scanner_bearer_token is not None
        ):
            raise ValueError("Evidence scanner endpoint credentials require the HTTP adapter")
        if (
            self.app_environment == "production"
            and self.evidence_scanner_adapter == "deterministic_allow"
        ):
            raise ValueError("Production evidence sanitization requires a named remote scanner")
        if self.evidence_sanitization_enabled and self.process_role is ProcessRole.EVIDENCE:
            if not all(evidence_groups["quarantine"]):
                raise ValueError("Evidence sanitization requires complete quarantine configuration")
            if not all(evidence_groups["sanitized"]):
                raise ValueError("Evidence sanitization requires complete sanitized configuration")
            if not all(evidence_groups["immutable"]):
                raise ValueError("Evidence worker requires complete immutable configuration")
            if self.evidence_scanner_adapter is None:
                raise ValueError("Evidence sanitization requires a named scanner adapter")
        configured_buckets = [
            bucket
            for bucket in (
                self.evidence_quarantine_bucket,
                self.evidence_sanitized_bucket,
                self.evidence_immutable_bucket,
            )
            if bucket is not None
        ]
        if self.app_environment == "production" and len(configured_buckets) != len(
            set(configured_buckets)
        ):
            raise ValueError("Production evidence buckets must be independent")
        configured_access_keys = [
            access_key.get_secret_value()
            for _, _, _, access_key, secret_key in evidence_groups.values()
            if access_key is not None and secret_key is not None
        ]
        configured_secret_keys = [
            secret_key.get_secret_value()
            for _, _, _, access_key, secret_key in evidence_groups.values()
            if access_key is not None and secret_key is not None
        ]
        if self.app_environment == "production" and (
            len(configured_access_keys) != len(set(configured_access_keys))
            or len(configured_secret_keys) != len(set(configured_secret_keys))
        ):
            raise ValueError("Production evidence credentials must be independent")
        if (
            self.app_environment == "production"
            and self.evidence_uploads_enabled
            and self.process_role not in {None, ProcessRole.WEB}
        ):
            raise ValueError("Evidence upload authority is restricted to the web role")
        if self.public_artifact_directory is not None and self.public_artifact_base_url is not None:
            raise ValueError("Configure one public artifact adapter, not both")
        artifact_adapter_configured = (
            self.public_artifact_directory is not None or self.public_artifact_base_url is not None
        )
        artifact_reader_state_required = self.process_role in {None, ProcessRole.WEB}
        if (
            artifact_adapter_configured
            and artifact_reader_state_required
            and self.public_artifact_checkpoint_path is None
        ):
            raise ValueError("Public artifact reads require a durable checkpoint path")
        if self.app_environment == "production" and self.public_artifact_directory is not None:
            raise ValueError("Production public artifacts require an HTTPS object-store origin")
        if (
            self.app_environment == "production"
            and self.public_artifact_base_url is not None
            and artifact_reader_state_required
            and self.public_artifact_cache_directory is None
        ):
            raise ValueError("Production public artifact reads require a durable verified cache")
        if (
            self.public_artifact_directory is not None
            and self.public_artifact_checkpoint_path is not None
            and self.public_artifact_checkpoint_path.resolve(strict=False).is_relative_to(
                self.public_artifact_directory.resolve(strict=False)
            )
        ):
            raise ValueError("Public artifact checkpoint must be separate from signed artifacts")
        if (
            self.public_artifact_cache_directory is not None
            and self.public_artifact_checkpoint_path is not None
        ):
            cache_directory = self.public_artifact_cache_directory.resolve(strict=False)
            checkpoint_path = self.public_artifact_checkpoint_path.resolve(strict=False)
            if checkpoint_path.is_relative_to(cache_directory):
                raise ValueError(
                    "Public artifact checkpoint must be separate from the verified cache"
                )
        if (
            self.public_artifact_directory is not None
            and self.public_artifact_cache_directory is not None
        ):
            raise ValueError("Local public artifacts cannot also configure a verified cache")
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
            checkpoint_lock_path = checkpoint_path.with_suffix(f"{checkpoint_path.suffix}.lock")
            projection_lock_path = projection_path.with_suffix(f"{projection_path.suffix}.lock")
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
            and self.process_role in {None, ProcessRole.WEB}
            and self.food_search_cursor_signing_keys.get_secret_value()
            == "v1:opennosh-development-search-cursor-key-2026"
        ):
            raise ValueError("Production requires unique food search cursor signing keys")
        if (
            self.app_environment == "production"
            and self.public_commons_latest_pointer_path is not None
            and _manifest_key_config_uses_nonproduction_key(self.public_commons_verifying_keys)
        ):
            raise ValueError("Production requires approved public commons verifying keys")
        if (
            self.app_environment == "production"
            and self.public_artifact_base_url is not None
            and _manifest_key_config_uses_nonproduction_key(self.public_commons_verifying_keys)
        ):
            raise ValueError("Production requires approved public artifact verifying keys")
        if (
            self.app_environment == "production"
            and self.public_artifact_base_url is not None
            and _receipt_key_config_uses_nonproduction_key(self.publication_receipt_verifying_keys)
        ):
            raise ValueError("Production requires approved publication receipt verifying keys")
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
        configured_windows = [
            self.auth_rate_limit_window_seconds,
            self.food_search_rate_limit_window_seconds,
            self.open_food_facts_lookup_rate_limit_window_seconds,
            self.open_food_facts_upstream_rate_limit_window_seconds,
            self.open_food_facts_export_rate_limit_window_seconds,
            self.exercise_search_rate_limit_window_seconds,
            self.exercise_export_rate_limit_window_seconds,
            self.community_export_rate_limit_window_seconds,
            self.private_export_rate_limit_window_seconds,
            self.contribution_patch_rate_limit_window_seconds,
        ]
        if self.evidence_uploads_enabled:
            configured_windows.append(self.evidence_upload_rate_limit_window_seconds)
        longest_window = max(configured_windows)
        if self.auth_rate_limit_retention_seconds < longest_window:
            raise ValueError("Rate-limit retention must cover every configured window")
        return self

    @property
    def publication_activation_id(self) -> UUID | None:
        if not self.publication_claims_enabled or self.publication_continuous_claims_enabled:
            return None
        return UUID(self.publication_activation_ids)

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
