from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from opennosh_api.cli import build_parser
from opennosh_api.federation.contracts import (
    FederationReleaseStatement,
    FederationScope,
    SignedFederationRelease,
    decode_public_key,
    encode_public_key,
    load_public_key,
    public_key_fingerprint,
    release_signature_material,
    release_statement_digest,
    validate_key_id,
    validate_scope_labels,
    verify_release_signature,
)
from opennosh_api.federation.github import (
    FederationProviderError,
    GitHubInstallationVerifier,
)
from opennosh_api.federation.repository import federation_scope_allows_claim
from opennosh_api.federation.service import FederationService
from opennosh_api.federation.settings import FederationOperatorSettings
from pydantic import ValidationError

NOW = datetime(2026, 8, 29, 13, tzinfo=UTC)
SCOPE = FederationScope(
    github_account_id=280184755,
    github_login="aarolabs",
    repository_id=1339461317,
    repository="RujitRaval/opennosh",
    pack_id="common-fruits",
)
SECOND_SCOPE = FederationScope(
    github_account_id=280184756,
    github_login="second-maintainer",
    repository_id=1339461318,
    repository="OpenNutrition/regional-produce",
    pack_id="regional-produce",
)
THIRD_SCOPE = FederationScope(
    github_account_id=280184756,
    github_login="second-maintainer",
    repository_id=1339461319,
    repository="OpenNutrition/heritage-grains",
    pack_id="heritage-grains",
)


def _rsa_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")


def _release(
    key: Ed25519PrivateKey, *, key_id: str = "maintainer-2026-01"
) -> SignedFederationRelease:
    statement = FederationReleaseStatement(
        maintainer_id=uuid4(),
        repository_id=SCOPE.repository_id,
        repository=SCOPE.repository,
        pack_id=SCOPE.pack_id,
        publication_id=uuid4(),
        release_version="1.2.3.4",
        manifest_digest="a" * 64,
        receipt_digest="b" * 64,
        public_url="https://opennosh.org/api/v1/public/releases/1.2.3.4/manifest",
        issued_at=NOW,
        key_id=key_id,
    )
    signature = (
        base64.urlsafe_b64encode(key.sign(release_signature_material(statement)))
        .decode("ascii")
        .rstrip("=")
    )
    return SignedFederationRelease(statement=statement, signature=signature)


def test_external_maintainer_may_control_a_repository_owned_by_another_account() -> None:
    assert SCOPE.github_login == "aarolabs"
    assert SCOPE.repository == "RujitRaval/opennosh"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("github_account_id", 0),
        ("github_login", "bad login"),
        ("repository_id", -1),
        ("repository", "missing-owner"),
        ("pack_id", "../private"),
        ("pack_id", "UPPER"),
    ],
)
def test_scope_rejects_unbounded_identity_values(field: str, value: object) -> None:
    payload = SCOPE.model_dump()
    payload[field] = value
    with pytest.raises(ValidationError):
        FederationScope.model_validate(payload)


@pytest.mark.parametrize("key_id", ["", " space", "a/b", "x" * 65])
def test_role_key_ids_are_bounded(key_id: str) -> None:
    with pytest.raises(ValueError, match="key ID"):
        validate_key_id(key_id)


@pytest.mark.parametrize(
    ("repository", "pack_id"),
    [("owner/repo", "pack"), ("RujitRaval/opennosh", "common-fruits")],
)
def test_scope_labels_accept_canonical_values(repository: str, pack_id: str) -> None:
    validate_scope_labels(repository, pack_id)


def test_public_key_loader_accepts_pem_and_reports_stable_fingerprint(tmp_path: Path) -> None:
    public_key = Ed25519PrivateKey.from_private_bytes(b"a" * 32).public_key()
    path = tmp_path / "maintainer.pub"
    path.write_bytes(
        public_key.public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    encoded, fingerprint = load_public_key(path)

    assert encoded == encode_public_key(public_key)
    assert len(fingerprint) == 64
    assert fingerprint == public_key_fingerprint(encoded)


@pytest.mark.parametrize("value", ["not-base64*", base64.urlsafe_b64encode(b"short").decode()])
def test_public_key_decoder_rejects_invalid_material(value: str) -> None:
    with pytest.raises(ValueError, match="encoding|32 bytes"):
        decode_public_key(value)


def test_public_key_loader_rejects_binary_and_non_ed25519_keys(tmp_path: Path) -> None:
    binary = tmp_path / "binary.pub"
    binary.write_bytes(b"\xff\xfe")
    with pytest.raises(ValueError, match="file is invalid"):
        load_public_key(binary)

    rsa_public = tmp_path / "rsa.pub"
    rsa_public.write_bytes(
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    with pytest.raises(ValueError, match="must be Ed25519"):
        load_public_key(rsa_public)


def test_federation_release_signature_is_domain_separated_and_verified() -> None:
    key = Ed25519PrivateKey.from_private_bytes(b"b" * 32)
    release = _release(key)

    verify_release_signature(release, encoded_public_key=encode_public_key(key.public_key()))
    assert len(release_statement_digest(release.statement)) == 64


def test_federation_release_rejects_another_role_key() -> None:
    release = _release(Ed25519PrivateKey.from_private_bytes(b"c" * 32))
    other = Ed25519PrivateKey.from_private_bytes(b"d" * 32)

    with pytest.raises(ValueError, match="signature is invalid"):
        verify_release_signature(release, encoded_public_key=encode_public_key(other.public_key()))


def _operator_settings(**scope_options: object) -> FederationOperatorSettings:
    return FederationOperatorSettings(
        administration_database_url="postgresql+asyncpg://admin:test@localhost/opennosh",
        allowed_public_origin="https://opennosh.org",
        inviter_actor_id=uuid4(),
        github_app_id=4741063,
        github_app_private_key=_rsa_pem(),
        **scope_options,
    )


def test_operator_settings_preserve_the_legacy_exact_scope() -> None:
    settings = _operator_settings(
        allowed_github_account_id=SCOPE.github_account_id,
        allowed_github_login=SCOPE.github_login,
        allowed_repository_id=SCOPE.repository_id,
        allowed_repository=SCOPE.repository,
        allowed_pack_id=SCOPE.pack_id,
    )

    assert settings.allowed_scopes == (SCOPE,)
    assert settings.allowed_scope == SCOPE
    assert settings.allowed_public_origin == "https://opennosh.org"
    assert settings.ingestion_enabled is False
    assert settings.projection_enabled is False
    assert "PRIVATE KEY" not in repr(settings)


def test_operator_settings_require_manifest_keys_before_ingestion_activation() -> None:
    scope_options = {
        "allowed_github_account_id": SCOPE.github_account_id,
        "allowed_github_login": SCOPE.github_login,
        "allowed_repository_id": SCOPE.repository_id,
        "allowed_repository": SCOPE.repository,
        "allowed_pack_id": SCOPE.pack_id,
    }

    with pytest.raises(ValidationError, match="verifying keys are required"):
        _operator_settings(ingestion_enabled=True, **scope_options)

    settings = _operator_settings(
        ingestion_enabled=True,
        manifest_verifying_keys='{"manifest-v1":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}',
        **scope_options,
    )
    assert settings.ingestion_enabled is True
    assert settings.manifest_verifying_keys is not None
    assert "manifest-v1" not in repr(settings)


def test_operator_settings_load_a_bounded_immutable_scope_allowlist() -> None:
    payload = json.dumps(
        [scope.model_dump(mode="json") for scope in (SCOPE, SECOND_SCOPE, THIRD_SCOPE)]
    )
    settings = _operator_settings(allowed_scopes_json=payload)

    assert settings.allowed_scopes == (SCOPE, SECOND_SCOPE, THIRD_SCOPE)
    assert payload not in repr(settings)
    with pytest.raises(ValueError, match="multiple scopes"):
        _ = settings.allowed_scope


@pytest.mark.parametrize(
    ("scope_options", "message"),
    [
        ({"allowed_scopes_json": "not-json"}, "JSON is invalid"),
        ({"allowed_scopes_json": "{}"}, "JSON is invalid"),
        (
            {
                "allowed_scopes_json": json.dumps(
                    [{**SCOPE.model_dump(), "unexpected": True}]
                )
            },
            "JSON is invalid",
        ),
        ({"allowed_scopes_json": "[]"}, "1 to 32"),
        (
            {"allowed_scopes_json": json.dumps([SCOPE.model_dump()] * 2)},
            "duplicate scope",
        ),
        (
            {
                "allowed_scopes_json": json.dumps(
                    [
                        SCOPE.model_dump(),
                        SECOND_SCOPE.model_copy(update={"pack_id": SCOPE.pack_id}).model_dump(),
                    ]
                )
            },
            "conflicting identities",
        ),
        (
            {
                "allowed_scopes_json": json.dumps(
                    [
                        SCOPE.model_dump(),
                        SECOND_SCOPE.model_copy(
                            update={
                                "github_account_id": SCOPE.github_account_id,
                                "github_login": "conflicting-login",
                            }
                        ).model_dump(),
                    ]
                )
            },
            "conflicting identities",
        ),
        (
            {
                "allowed_scopes_json": json.dumps(
                    [
                        SCOPE.model_dump(),
                        SECOND_SCOPE.model_copy(
                            update={
                                "repository_id": SCOPE.repository_id,
                                "repository": "DifferentOwner/different-repository",
                            }
                        ).model_dump(),
                    ]
                )
            },
            "conflicting identities",
        ),
        (
            {
                "allowed_scopes_json": json.dumps([SCOPE.model_dump()]),
                "allowed_github_account_id": SCOPE.github_account_id,
            },
            "mutually exclusive",
        ),
        (
            {"allowed_github_account_id": SCOPE.github_account_id},
            "legacy scope configuration is incomplete",
        ),
    ],
)
def test_operator_settings_reject_ambiguous_or_incomplete_scope_policy(
    scope_options: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        _operator_settings(**scope_options)


def test_operator_settings_reject_more_than_32_scopes() -> None:
    scopes = [
        SCOPE.model_copy(
            update={
                "repository_id": SCOPE.repository_id + index,
                "repository": f"RujitRaval/opennosh-{index}",
                "pack_id": f"common-fruits-{index}",
            }
        ).model_dump(mode="json")
        for index in range(1, 34)
    ]

    with pytest.raises(ValidationError, match="1 to 32"):
        _operator_settings(allowed_scopes_json=json.dumps(scopes))


def test_federation_service_rejects_duplicate_allowed_scopes() -> None:
    with pytest.raises(ValueError, match="distinct allowed scopes"):
        FederationService(
            object(),  # type: ignore[arg-type]
            allowed_scopes=(SCOPE, SCOPE),
            allowed_public_origin="https://opennosh.org",
            installation_verifier=object(),  # type: ignore[arg-type]
        )


def test_cli_exposes_all_steward_lifecycle_commands() -> None:
    parser = build_parser()
    for command in (
        "invite",
        "verify",
        "activate",
        "rotate-key",
        "publish-release",
        "quarantine",
        "revoke",
        "status",
    ):
        with pytest.raises(SystemExit) as raised:
            parser.parse_args(["federation", command, "--help"])
        assert raised.value.code == 0


@pytest.mark.asyncio
async def test_github_verifier_binds_account_repository_and_write_control() -> None:
    responses = iter(
        (
            httpx.Response(200, json={"id": 157058059, "account": {"id": 29613540}}),
            httpx.Response(201, json={"token": "installation-token"}),
            httpx.Response(
                200,
                json={
                    "id": SCOPE.repository_id,
                    "full_name": SCOPE.repository,
                    "owner": {"id": 29613540},
                },
            ),
            httpx.Response(200, json={"id": SCOPE.github_account_id, "login": SCOPE.github_login}),
            httpx.Response(200, json={"permission": "write"}),
        )
    )

    def handler(request: httpx.Request) -> httpx.Response:
        response = next(responses)
        response.request = request
        return response

    client = httpx.AsyncClient(
        base_url="https://api.github.test", transport=httpx.MockTransport(handler)
    )
    verifier = GitHubInstallationVerifier(
        app_id=4741063,
        private_key_pem=_rsa_pem(),
        client=client,
        clock=lambda: NOW,
    )
    try:
        await verifier.verify(SCOPE, installation_id=157058059)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_github_verifier_rejects_read_only_collaborator() -> None:
    payloads = [
        (200, {"id": 157058059}),
        (201, {"token": "installation-token"}),
        (200, {"id": SCOPE.repository_id, "full_name": SCOPE.repository, "owner": {"id": 1}}),
        (200, {"id": SCOPE.github_account_id, "login": SCOPE.github_login}),
        (200, {"permission": "read"}),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        status, payload = payloads.pop(0)
        return httpx.Response(status, json=payload, request=request)

    client = httpx.AsyncClient(
        base_url="https://api.github.test", transport=httpx.MockTransport(handler)
    )
    verifier = GitHubInstallationVerifier(
        app_id=4741063,
        private_key_pem=_rsa_pem(),
        client=client,
        clock=lambda: NOW,
    )
    try:
        with pytest.raises(FederationProviderError, match="repository_control_missing"):
            await verifier.verify(SCOPE, installation_id=157058059)
    finally:
        await client.aclose()


class _ClaimConnection:
    def __init__(self, blocked: bool) -> None:
        self.blocked = blocked
        self.arguments: tuple[object, ...] = ()

    async def fetchval(self, _query: str, *args: object) -> object:
        self.arguments = args
        return self.blocked


@pytest.mark.asyncio
@pytest.mark.parametrize(("blocked", "allowed"), [(False, True), (True, False)])
async def test_claim_scope_fails_closed_for_nonactive_enrollment(
    blocked: bool, allowed: bool
) -> None:
    connection = _ClaimConnection(blocked)

    assert (
        await federation_scope_allows_claim(
            connection,
            repository=SCOPE.repository,
            pack_id=SCOPE.pack_id,
        )
        is allowed
    )
    assert connection.arguments == (SCOPE.repository, SCOPE.pack_id)


def test_release_statement_rejects_naive_time() -> None:
    key = Ed25519PrivateKey.from_private_bytes(b"e" * 32)
    payload = _release(key).statement.model_dump()
    payload["issued_at"] = datetime(2026, 8, 29, 13)

    with pytest.raises(ValidationError, match="timezone"):
        FederationReleaseStatement.model_validate(payload)


@pytest.mark.parametrize(
    "public_url",
    (
        "http://opennosh.org/api/v1/public/releases/1.2.3.4/manifest",
        "https://user@opennosh.org/api/v1/public/releases/1.2.3.4/manifest",
        "https://opennosh.org/api/v1/public/releases/1.2.3.4/manifest?unsafe=true",
    ),
)
def test_release_statement_rejects_noncanonical_public_url(public_url: str) -> None:
    payload = _release(Ed25519PrivateKey.from_private_bytes(b"h" * 32)).statement.model_dump()
    payload["public_url"] = public_url

    with pytest.raises(ValidationError, match="canonical HTTPS"):
        FederationReleaseStatement.model_validate(payload)


@pytest.mark.parametrize(
    ("repository", "pack_id", "message"),
    [("missing-owner", "common-fruits", "repository"), (SCOPE.repository, "UPPER", "pack")],
)
def test_scope_label_validator_rejects_noncanonical_values(
    repository: str, pack_id: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        validate_scope_labels(repository, pack_id)
