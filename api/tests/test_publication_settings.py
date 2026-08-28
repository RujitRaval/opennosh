from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from opennosh_api.capacity import ProcessRole
from opennosh_api.public.signing import public_key_text
from opennosh_api.publication.credentials import ProductionPublicationClients
from opennosh_api.settings import Settings
from pydantic import ValidationError

ONLINE_MANIFEST_KEY = Ed25519PrivateKey.from_private_bytes(b"o" * 32)
OFFLINE_MANIFEST_KEY = Ed25519PrivateKey.from_private_bytes(b"m" * 32)
PRODUCTION_RECEIPT_KEY = Ed25519PrivateKey.from_private_bytes(b"r" * 32)
FORGE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
ATTESTER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _pem(key: rsa.RSAPrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


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
        "online_receipt_signing_key_id": "receipt-production",
        "online_receipt_signing_key": (
            base64.urlsafe_b64encode(b"r" * 32).decode().rstrip("=")
        ),
        "github_repository_id": 1,
        "github_forge_app_id": 2,
        "github_forge_installation_id": 3,
        "github_forge_private_key": _pem(FORGE_KEY),
        "github_attester_app_id": 4,
        "github_attester_installation_id": 5,
        "github_attester_private_key": _pem(ATTESTER_KEY),
        "publication_artifact_bucket": "opennosh-public-commons",
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


@pytest.mark.parametrize(
    "activation_ids",
    [
        "",
        "not-a-uuid",
        "00000000-0000-4000-8000-000000000001,",
        " 00000000-0000-4000-8000-000000000001",
        "00000000-0000-4000-8000-000000000001,"
        "00000000-0000-4000-8000-000000000002",
        "aaaaaaaa-0000-4000-8000-000000000001".upper(),
    ],
)
def test_publication_claims_require_one_canonical_activation_id(
    activation_ids: str,
) -> None:
    with pytest.raises(ValidationError, match="PUBLICATION_ACTIVATION_IDS"):
        Settings(
            publication_claims_enabled=True,
            publication_activation_ids=activation_ids,
            _env_file=None,
        )


def test_publication_activation_id_requires_claims_enabled() -> None:
    with pytest.raises(ValidationError, match="require claims"):
        Settings(
            publication_activation_ids="00000000-0000-4000-8000-000000000001",
            _env_file=None,
        )


def test_combined_claims_and_refresh_accept_one_activation_id() -> None:
    settings = _refresh_settings(
        publication_claims_enabled=True,
        publication_activation_ids="00000000-0000-4000-8000-000000000001",
    )

    assert str(settings.publication_activation_id) == (
        "00000000-0000-4000-8000-000000000001"
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("github_attester_app_id", 2),
        ("github_attester_installation_id", 3),
        ("github_attester_private_key", _pem(FORGE_KEY)),
    ],
)
def test_claims_require_independent_forge_and_attester_identities(
    field: str, value: object
) -> None:
    with pytest.raises(ValidationError, match="identities must be independent"):
        _refresh_settings(
            publication_claims_enabled=True,
            publication_activation_ids="00000000-0000-4000-8000-000000000001",
            **{field: value},
        )


def test_claims_require_receipt_key_independent_from_offline_manifest_keys() -> None:
    shared = base64.urlsafe_b64encode(b"m" * 32).decode().rstrip("=")
    with pytest.raises(ValidationError, match="independent from manifest and offline"):
        _refresh_settings(
            publication_claims_enabled=True,
            publication_activation_ids="00000000-0000-4000-8000-000000000001",
            online_receipt_signing_key=shared,
            publication_receipt_verifying_keys=json.dumps(
                {"receipt-production": public_key_text(OFFLINE_MANIFEST_KEY)}
            ),
        )


def test_claims_require_the_publication_and_refresh_r2_bucket_to_match() -> None:
    with pytest.raises(ValidationError, match="must match"):
        _refresh_settings(
            publication_claims_enabled=True,
            publication_activation_ids="00000000-0000-4000-8000-000000000001",
            publication_artifact_bucket="opennosh-publication-different",
        )


def test_publication_artifact_bucket_rejects_invalid_cloudflare_name() -> None:
    with pytest.raises(ValidationError, match="Cloudflare naming requirements"):
        _refresh_settings(publication_artifact_bucket="has.dot")


def test_production_claims_cannot_run_without_latest_refresh() -> None:
    with pytest.raises(ValidationError, match="latest refresh enabled"):
        _refresh_settings(
            publication_claims_enabled=True,
            publication_activation_ids="00000000-0000-4000-8000-000000000001",
            latest_refresh_enabled=False,
        )


@pytest.mark.asyncio
async def test_claim_clients_construct_with_redacted_independent_identities() -> None:
    settings = _refresh_settings(
        publication_claims_enabled=True,
        publication_activation_ids="00000000-0000-4000-8000-000000000001",
    )

    clients = ProductionPublicationClients.from_settings(settings)
    try:
        rendered = repr(clients)
        assert clients.identity.forge.app_id == 2
        assert clients.identity.attester.app_id == 4
        assert clients.identity.forge.public_key_fingerprint != (
            clients.identity.attester.public_key_fingerprint
        )
        assert clients.identity.manifest_public_key != clients.identity.receipt_public_key
        assert _pem(FORGE_KEY) not in rendered
        assert _pem(ATTESTER_KEY) not in rendered
        assert settings.online_receipt_signing_key is not None
        assert settings.online_receipt_signing_key.get_secret_value() not in rendered
    finally:
        await clients.aclose()
