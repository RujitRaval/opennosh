from __future__ import annotations

import pytest
from opennosh_api.first_contribution.settings import FirstContributionOperatorSettings
from pydantic import ValidationError


def test_operator_settings_require_isolated_complete_credentials() -> None:
    settings = FirstContributionOperatorSettings(
        administration_database_url="postgresql+asyncpg://operator@example.test/opennosh",
        reviewed_base_commit="b" * 40,
        reviewed_package_digest="c" * 64,
        r2_account_id="a" * 32,
        r2_bucket="opennosh-public-commons",
        r2_access_key_id="access-key",
        r2_secret_access_key="secret-key",
        _env_file=None,
    )

    assert settings.r2_bucket == "opennosh-public-commons"
    assert settings.reviewed_base_commit == "b" * 40
    assert settings.reviewed_package_digest == "c" * 64
    assert settings.r2_secret_access_key.get_secret_value() == "secret-key"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("r2_account_id", "not-an-account"),
        ("r2_bucket", "Invalid_Bucket"),
        ("r2_bucket", "plausible-but-untrusted-bucket"),
        ("reviewed_base_commit", "not-a-commit"),
        ("reviewed_package_digest", "not-a-digest"),
        ("r2_access_key_id", "contains whitespace"),
        ("r2_secret_access_key", " "),
    ],
)
def test_operator_settings_reject_invalid_provider_authority(field: str, value: str) -> None:
    material: dict[str, object] = {
        "administration_database_url": "postgresql+asyncpg://operator@example.test/opennosh",
        "reviewed_base_commit": "b" * 40,
        "reviewed_package_digest": "c" * 64,
        "r2_account_id": "a" * 32,
        "r2_bucket": "opennosh-public-commons",
        "r2_access_key_id": "access-key",
        "r2_secret_access_key": "secret-key",
        "_env_file": None,
    }
    material[field] = value

    with pytest.raises(ValidationError):
        FirstContributionOperatorSettings(**material)  # type: ignore[arg-type]
