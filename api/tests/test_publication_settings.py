from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from opennosh_api.capacity import ProcessRole
from opennosh_api.public.signing import public_key_text
from opennosh_api.settings import Settings
from pydantic import ValidationError

ONLINE_MANIFEST_KEY = Ed25519PrivateKey.from_private_bytes(b"o" * 32)
OFFLINE_MANIFEST_KEY = Ed25519PrivateKey.from_private_bytes(b"m" * 32)
PRODUCTION_RECEIPT_KEY = Ed25519PrivateKey.from_private_bytes(b"r" * 32)


def _refresh_settings(**overrides: object) -> Settings:
    encoded_online_key = base64.urlsafe_b64encode(b"o" * 32).decode().rstrip("=")
    values: dict[str, object] = {
        "app_environment": "production",
        "process_role": ProcessRole.PUBLICATION,
        "latest_refresh_enabled": True,
        "public_artifact_base_url": "https://commons-artifacts.opennosh.org",
        "public_commons_verifying_keys": (
            "manifest-offline:"
            + public_key_text(OFFLINE_MANIFEST_KEY)
            + ",manifest-online:"
            + public_key_text(ONLINE_MANIFEST_KEY)
        ),
        "publication_receipt_verifying_keys": json.dumps(
            {"receipt-production": public_key_text(PRODUCTION_RECEIPT_KEY)}
        ),
        "online_manifest_signing_key_id": "manifest-online",
        "online_manifest_signing_key": encoded_online_key,
        "r2_account_id": "a" * 32,
        "r2_bucket": "opennosh-public-commons",
        "r2_access_key_id": "access-key",
        "r2_secret_access_key": "secret-key",
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_refresh_only_settings_require_worker_isolation_and_no_reader_disk() -> None:
    settings = _refresh_settings()

    assert settings.process_role is ProcessRole.PUBLICATION
    assert settings.publication_claims_enabled is False
    assert settings.latest_refresh_enabled is True
    assert settings.public_artifact_checkpoint_path is None
    assert settings.public_artifact_cache_directory is None
    assert "secret-key" not in repr(settings)


@pytest.mark.parametrize(
    "missing",
    [
        "public_artifact_base_url",
        "online_manifest_signing_key_id",
        "online_manifest_signing_key",
        "r2_account_id",
        "r2_bucket",
        "r2_access_key_id",
        "r2_secret_access_key",
    ],
)
def test_refresh_only_settings_fail_closed_when_one_dependency_is_missing(
    missing: str,
) -> None:
    with pytest.raises(ValidationError, match=missing.upper()):
        _refresh_settings(**{missing: None})


def test_refresh_only_settings_reject_malformed_base64url_signing_secret() -> None:
    valid = base64.urlsafe_b64encode(b"o" * 32).decode().rstrip("=")
    malformed = valid[:10] + "!!!!" + valid[10:]

    with pytest.raises(ValidationError, match="valid base64url") as captured:
        _refresh_settings(online_manifest_signing_key=malformed)

    assert malformed not in str(captured.value)


@pytest.mark.parametrize("bucket", ["ab", "UPPERCASE", "-leading", "trailing-", "has.dot"])
def test_refresh_only_settings_reject_invalid_r2_bucket(bucket: str) -> None:
    with pytest.raises(ValidationError, match="Cloudflare naming requirements"):
        _refresh_settings(r2_bucket=bucket)


def test_refresh_only_settings_require_independent_online_and_offline_keys() -> None:
    shared = public_key_text(ONLINE_MANIFEST_KEY)
    with pytest.raises(ValidationError, match="independent from offline"):
        _refresh_settings(
            public_commons_verifying_keys=(
                f"manifest-offline:{shared},manifest-online:{shared}"
            )
        )


def test_refresh_only_settings_compare_decoded_manifest_key_material() -> None:
    shared = public_key_text(ONLINE_MANIFEST_KEY)
    with pytest.raises(ValidationError, match="independent from offline"):
        _refresh_settings(
            public_commons_verifying_keys=(
                f"manifest-offline:{shared}=,manifest-online:{shared}"
            )
        )


def test_refresh_only_settings_require_independent_manifest_and_receipt_keys() -> None:
    shared = public_key_text(ONLINE_MANIFEST_KEY)
    with pytest.raises(ValidationError, match="independent from offline"):
        _refresh_settings(
            publication_receipt_verifying_keys=json.dumps({"receipt-production": shared})
        )


def test_refresh_only_settings_compare_decoded_receipt_key_material() -> None:
    shared = public_key_text(ONLINE_MANIFEST_KEY)
    with pytest.raises(ValidationError, match="independent from offline"):
        _refresh_settings(
            publication_receipt_verifying_keys=json.dumps(
                {"receipt-production": shared + "="}
            )
        )


def test_refresh_only_settings_require_an_offline_manifest_authority() -> None:
    with pytest.raises(ValidationError, match="independent from offline"):
        _refresh_settings(
            public_commons_verifying_keys=(
                "manifest-online:" + public_key_text(ONLINE_MANIFEST_KEY)
            )
        )


def test_refresh_only_settings_require_the_online_public_key_in_the_ring() -> None:
    with pytest.raises(ValidationError, match="present in the verifying key ring"):
        _refresh_settings(
            public_commons_verifying_keys=(
                "different-production:" + public_key_text(PRODUCTION_RECEIPT_KEY)
            )
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"latest_refresh_after_seconds": 82_800},
        {"latest_pointer_lifetime_seconds": 86_401},
        {
            "latest_refresh_interval_seconds": 10_801,
            "latest_refresh_after_seconds": 72_000,
            "latest_pointer_lifetime_seconds": 82_800,
        },
    ],
)
def test_refresh_only_settings_guarantee_a_pre_expiry_attempt(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="pre-expiry renewal"):
        _refresh_settings(**overrides)


def test_publication_secrets_are_rejected_from_the_production_web_process() -> None:
    with pytest.raises(ValidationError, match="restricted to the publication worker"):
        _refresh_settings(
            process_role=ProcessRole.WEB,
            food_search_cursor_signing_keys="prod-v1:33333333333333333333333333333333",
        )


def test_production_publication_worker_requires_one_runtime_mode() -> None:
    with pytest.raises(ValidationError, match="enabled runtime mode"):
        Settings(
            app_environment="production",
            process_role=ProcessRole.PUBLICATION,
            _env_file=None,
        )
