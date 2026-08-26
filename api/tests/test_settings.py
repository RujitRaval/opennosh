from decimal import Decimal
from pathlib import Path

import pytest
from opennosh_api.capacity import JobRole, ProcessRole
from opennosh_api.settings import Settings
from pydantic import ValidationError


def test_settings_have_safe_development_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.database_url.endswith("@localhost:5432/opennosh")
    assert settings.database_healthcheck_timeout_seconds == 2.0
    assert settings.food_search_rate_limit_attempts == 120
    assert settings.food_search_rate_limit_window_seconds == 60
    assert settings.contribution_patch_rate_limit_attempts == 120
    assert settings.contribution_patch_rate_limit_window_seconds == 60
    assert settings.contribution_patch_account_rate_limit_attempts == 240
    assert settings.contribution_operation_retention_seconds == 691_200
    assert settings.food_search_statement_timeout_ms == 500
    assert settings.food_search_snapshot_build_timeout_ms == 30_000
    assert settings.open_food_facts_enabled is False
    assert settings.open_food_facts_base_url == "https://world.openfoodfacts.org"
    assert settings.open_food_facts_timeout_seconds == 3.0
    assert settings.open_food_facts_lookup_rate_limit_attempts == 10
    assert settings.open_food_facts_lookup_rate_limit_window_seconds == 60
    assert settings.open_food_facts_upstream_rate_limit_attempts == 10
    assert settings.open_food_facts_upstream_rate_limit_window_seconds == 60
    assert settings.open_food_facts_export_rate_limit_attempts == 10
    assert settings.open_food_facts_export_statement_timeout_ms == 2_000
    assert settings.exercise_search_rate_limit_attempts == 120
    assert settings.exercise_search_rate_limit_window_seconds == 60
    assert settings.exercise_search_statement_timeout_ms == 500
    assert settings.exercise_export_rate_limit_attempts == 10
    assert settings.exercise_export_rate_limit_window_seconds == 60
    assert settings.exercise_export_statement_timeout_ms == 2_000
    assert settings.community_export_rate_limit_attempts == 10
    assert settings.community_export_rate_limit_window_seconds == 60
    assert settings.community_export_statement_timeout_ms == 2_000
    assert settings.private_export_rate_limit_attempts == 10
    assert settings.private_export_rate_limit_window_seconds == 60
    assert settings.private_export_statement_timeout_ms == 5_000
    assert settings.public_export_concurrency_limit == 2
    assert settings.private_export_concurrency_limit == 1
    assert settings.export_capacity_wait_seconds == 1.0
    assert settings.public_export_response_timeout_seconds == 300.0
    assert settings.private_export_response_timeout_seconds == 1_800.0
    assert settings.target_kcal_floor == Decimal("1200")
    assert settings.session_cookie_name == "opennosh_session"
    assert settings.session_cookie_secure is False


def test_public_artifact_origin_configuration_is_safe() -> None:
    defaults = Settings(_env_file=None)
    assert defaults.public_artifact_directory is None
    assert defaults.public_artifact_base_url is None
    assert defaults.public_artifact_checkpoint_path is None

    with pytest.raises(ValidationError, match="safe HTTPS URL"):
        Settings(public_artifact_base_url="http://artifacts.example.test", _env_file=None)
    with pytest.raises(ValidationError, match="durable checkpoint path"):
        Settings(public_artifact_directory="/artifacts", _env_file=None)
    with pytest.raises(ValidationError, match="one public artifact adapter"):
        Settings(
            public_artifact_directory="/artifacts",
            public_artifact_base_url="https://artifacts.example.test",
            _env_file=None,
        )


def test_production_public_artifacts_require_https_origin_and_checkpoint() -> None:
    production = {
        "app_environment": "production",
        "food_search_cursor_signing_keys": ("prod-v1:33333333333333333333333333333333"),
        "public_commons_verifying_keys": ("production:Laz0b4AQMs1TfE090-MRSPubDqxptaEJ-HZXEsZe_lw"),
        "publication_receipt_verifying_keys": (
            '{"production":"Laz0b4AQMs1TfE090-MRSPubDqxptaEJ-HZXEsZe_lw"}'
        ),
        "_env_file": None,
    }
    with pytest.raises(ValidationError, match="HTTPS object-store origin"):
        Settings(  # type: ignore[arg-type]
            **production,
            public_artifact_directory="/artifacts",
            public_artifact_checkpoint_path="/state/public-artifacts.json",
        )
    with pytest.raises(ValidationError, match="durable checkpoint path"):
        Settings(
            **production,  # type: ignore[arg-type]
            public_artifact_base_url="https://artifacts.opennosh.org",
        )

    with pytest.raises(ValidationError, match="separate from signed artifacts"):
        Settings(
            public_artifact_directory="/artifacts",
            public_artifact_checkpoint_path="/artifacts/checkpoint.json",
            _env_file=None,
        )

    settings = Settings(
        **production,  # type: ignore[arg-type]
        public_artifact_base_url="https://artifacts.opennosh.org",
        public_artifact_checkpoint_path="/state/public-artifacts.json",
    )
    assert settings.public_artifact_base_url == "https://artifacts.opennosh.org"


def test_production_settings_use_secure_host_only_cookie_names() -> None:
    settings = Settings(
        app_environment="production",
        food_search_cursor_signing_keys="prod-v1:33333333333333333333333333333333",
        _env_file=None,
    )

    assert settings.session_cookie_name == "__Host-opennosh-session"
    assert settings.csrf_cookie_name == "__Host-opennosh-csrf"
    assert settings.session_cookie_secure is True


def test_proxy_token_must_be_long_and_unique_in_production() -> None:
    with pytest.raises(ValidationError):
        Settings(trusted_web_proxy_token="too-short", _env_file=None)
    for unsafe_token in (
        "opennosh-local-web-proxy-token-2026",
        "replace-with-a-unique-32-character-secret",
    ):
        with pytest.raises(ValidationError):
            Settings(
                app_environment="production",
                trusted_web_proxy_token=unsafe_token,
                _env_file=None,
            )


def test_production_public_commons_requires_a_durable_projection_path() -> None:
    configured = {
        "app_environment": "production",
        "food_search_cursor_signing_keys": "prod-v1:33333333333333333333333333333333",
        "public_commons_latest_pointer_path": "/artifacts/latest.json",
        "public_commons_release_directory": "/artifacts/releases",
        "public_commons_checkpoint_path": "/state/checkpoint.json",
        "public_commons_verifying_keys": ("production:Laz0b4AQMs1TfE090-MRSPubDqxptaEJ-HZXEsZe_lw"),
        "_env_file": None,
    }

    with pytest.raises(ValidationError, match="durable projection path"):
        Settings(**configured)  # type: ignore[arg-type]

    settings = Settings(
        **configured,  # type: ignore[arg-type]
        public_commons_projection_path="/state/homepage-snapshot.json",
    )
    assert settings.public_commons_projection_path is not None


@pytest.mark.parametrize(
    ("checkpoint_path", "projection_path", "message"),
    [
        ("/state/shared.json", "/state/shared.json", "must be distinct"),
        ("/state/homepage-snapshot.json.lock", "/state/homepage-snapshot.json", "must be distinct"),
        ("/state/checkpoint.json", "/state/checkpoint.json.lock", "must be distinct"),
        (
            "/artifacts/latest.json",
            "/state/homepage-snapshot.json",
            "separate from signed artifacts",
        ),
        (
            "/state/checkpoint.json",
            "/artifacts/releases/snapshot.json",
            "separate from signed artifacts",
        ),
    ],
)
def test_production_public_commons_state_paths_cannot_alias_signed_artifacts(
    checkpoint_path: str, projection_path: str, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        Settings(
            app_environment="production",
            food_search_cursor_signing_keys="prod-v1:33333333333333333333333333333333",
            public_commons_latest_pointer_path="/artifacts/latest.json",
            public_commons_release_directory="/artifacts/releases",
            public_commons_checkpoint_path=checkpoint_path,
            public_commons_projection_path=projection_path,
            public_commons_verifying_keys=(
                "production:Laz0b4AQMs1TfE090-MRSPubDqxptaEJ-HZXEsZe_lw"
            ),
            _env_file=None,
        )


def test_public_commons_revalidation_requires_scoped_token_and_allowlisted_host() -> None:
    callback_url = "http://web:3000/api/internal/public-commons/revalidate"
    with pytest.raises(ValidationError, match="requires a scoped token"):
        Settings(
            public_commons_revalidation_url=callback_url,
            _env_file=None,
        )
    settings = Settings(
        public_commons_revalidation_url=callback_url,
        public_commons_revalidation_token="test-public-commons-revalidation-token",
        _env_file=None,
    )
    assert settings.public_commons_revalidation_url == callback_url

    with pytest.raises(ValidationError, match="not allowlisted"):
        Settings(
            public_commons_revalidation_url=(
                "http://169.254.169.254/api/internal/public-commons/revalidate"
            ),
            public_commons_revalidation_token="test-public-commons-revalidation-token",
            _env_file=None,
        )
    with pytest.raises(ValidationError, match="safe HTTP URL"):
        Settings(
            public_commons_revalidation_url="http://web:3000/latest/meta-data",
            public_commons_revalidation_token="test-public-commons-revalidation-token",
            _env_file=None,
        )


def test_blank_public_commons_revalidation_token_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PUBLIC_COMMONS_REVALIDATION_TOKEN", "")

    assert Settings(_env_file=None).public_commons_revalidation_token is None


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
    with pytest.raises(ValidationError):
        Settings(
            open_food_facts_lookup_rate_limit_window_seconds=300,
            auth_rate_limit_retention_seconds=299,
            _env_file=None,
        )
    with pytest.raises(ValidationError):
        Settings(
            open_food_facts_export_rate_limit_window_seconds=300,
            auth_rate_limit_retention_seconds=299,
            _env_file=None,
        )
    with pytest.raises(ValidationError):
        Settings(
            exercise_search_rate_limit_window_seconds=300,
            auth_rate_limit_retention_seconds=299,
            _env_file=None,
        )
    with pytest.raises(ValidationError):
        Settings(
            exercise_export_rate_limit_window_seconds=300,
            auth_rate_limit_retention_seconds=299,
            _env_file=None,
        )
    with pytest.raises(ValidationError):
        Settings(
            community_export_rate_limit_window_seconds=300,
            auth_rate_limit_retention_seconds=299,
            _env_file=None,
        )
    with pytest.raises(ValidationError):
        Settings(
            private_export_rate_limit_window_seconds=300,
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


def test_open_food_facts_settings_reject_unsafe_values() -> None:
    with pytest.raises(ValidationError):
        Settings(open_food_facts_base_url="http://world.openfoodfacts.org", _env_file=None)
    with pytest.raises(ValidationError):
        Settings(open_food_facts_timeout_seconds=0, _env_file=None)
    with pytest.raises(ValidationError):
        Settings(open_food_facts_lookup_rate_limit_attempts=16, _env_file=None)
    with pytest.raises(ValidationError):
        Settings(open_food_facts_upstream_rate_limit_attempts=16, _env_file=None)
    with pytest.raises(ValidationError):
        Settings(open_food_facts_upstream_rate_limit_window_seconds=59, _env_file=None)
    with pytest.raises(ValidationError):
        Settings(open_food_facts_base_url="https://example.test:99999", _env_file=None)
    with pytest.raises(ValidationError):
        Settings(open_food_facts_user_agent_contact="contact\nInjected: header", _env_file=None)
    with pytest.raises(ValidationError):
        Settings(open_food_facts_user_agent_contact="Maintainer 🚀", _env_file=None)


def test_production_roles_require_specific_least_privilege_database_urls() -> None:
    production = Settings(
        app_environment="production",
        food_search_cursor_signing_keys="prod-v1:33333333333333333333333333333333",
        _env_file=None,
    )

    with pytest.raises(ValueError, match="WEB_DATABASE_URL"):
        production.process_database_url(ProcessRole.WEB)
    with pytest.raises(ValueError, match="MIGRATION_DATABASE_URL"):
        production.process_database_url(JobRole.MIGRATION)

    configured = production.model_copy(
        update={"web_database_url": "postgresql+asyncpg://web-role@database/opennosh"}
    )
    assert configured.process_database_url(ProcessRole.WEB).startswith(
        "postgresql+asyncpg://web-role@"
    )


def test_local_evidence_directories_are_paired_independent_and_non_production(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValidationError, match="configured together"):
        Settings(evidence_private_source_directory=tmp_path / "source", _env_file=None)
    with pytest.raises(ValidationError, match="must be independent"):
        Settings(
            evidence_private_source_directory=tmp_path / "same",
            evidence_immutable_directory=tmp_path / "same",
            _env_file=None,
        )
    configured = Settings(
        evidence_private_source_directory=tmp_path / "source",
        evidence_immutable_directory=tmp_path / "immutable",
        _env_file=None,
    )
    assert configured.evidence_private_source_directory == tmp_path / "source"
    with pytest.raises(ValidationError, match="valid JSON"):
        Settings(evidence_verifying_keys="not-json", _env_file=None)
    with pytest.raises(ValidationError, match="non-filesystem adapter"):
        Settings(
            app_environment="production",
            evidence_private_source_directory=tmp_path / "source",
            evidence_immutable_directory=tmp_path / "immutable",
            food_search_cursor_signing_keys="prod-v1:33333333333333333333333333333333",
            _env_file=None,
        )
