from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import opennosh_api.cli as root_cli
import opennosh_api.federation.cli as federation_cli
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from opennosh_api.federation.contracts import (
    FederationLifecycleState,
    FederationReleaseStatement,
    FederationScope,
    InvitationSecret,
    MaintainerStatus,
    SignedFederationRelease,
    encode_public_key,
)
from opennosh_api.federation.github import FederationProviderError
from opennosh_api.federation.service import FederationError
from pydantic import SecretStr, ValidationError
from sqlalchemy.exc import SQLAlchemyError

NOW = datetime(2026, 8, 29, 13, tzinfo=UTC)
MAINTAINER_ID = UUID("11111111-1111-4111-8111-111111111111")
ACTOR_ID = UUID("22222222-2222-4222-8222-222222222222")
PUBLICATION_ID = UUID("33333333-3333-4333-8333-333333333333")
SCOPE = FederationScope(
    github_account_id=280184755,
    github_login="aarolabs",
    repository_id=1339461317,
    repository="RujitRaval/opennosh",
    pack_id="common-fruits",
)


def _status(state: FederationLifecycleState = FederationLifecycleState.ACTIVE) -> MaintainerStatus:
    return MaintainerStatus(
        maintainer_id=MAINTAINER_ID,
        state=state,
        github_account_id=SCOPE.github_account_id,
        github_login=SCOPE.github_login,
        repository_id=SCOPE.repository_id,
        repository=SCOPE.repository,
        pack_id=SCOPE.pack_id,
        current_role_key_id="maintainer-2026-01",
        current_role_key_fingerprint="a" * 64,
        requested_at=NOW,
        verified_at=NOW,
        activated_at=NOW,
        quarantined_at=None,
        revoked_at=None,
    )


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        administration_database_url="postgresql://operator.invalid/opennosh",
        database_capacity_manifest_path=Path("capacity.json"),
        github_app_id=4741063,
        github_app_private_key=SecretStr("not-a-real-private-key"),
        allowed_scopes=(SCOPE,),
        allowed_public_origin="https://opennosh.org",
        inviter_actor_id=ACTOR_ID,
    )


class _Engine:
    def __init__(self) -> None:
        self.disposals = 0

    async def dispose(self) -> None:
        self.disposals += 1


class _Verifier:
    def __init__(self) -> None:
        self.closes = 0

    async def aclose(self) -> None:
        self.closes += 1


class _Service:
    def __init__(self) -> None:
        self.operations: list[str] = []
        self.rejections: list[dict[str, object]] = []
        self.fail_activate = False

    async def invite(self, scope: FederationScope, **_options: object) -> InvitationSecret:
        assert scope == SCOPE
        self.operations.append("invite")
        return InvitationSecret(
            invitation_id=UUID("44444444-4444-4444-8444-444444444444"),
            token="t" * 32,
            expires_at=NOW + timedelta(hours=1),
        )

    async def verify(self, **options: object) -> MaintainerStatus:
        assert options["token"] == "t" * 32
        assert options["scope"] == SCOPE
        self.operations.append("verify")
        return _status(FederationLifecycleState.VERIFIED)

    async def activate(self, _maintainer_id: UUID, **_options: object) -> MaintainerStatus:
        if self.fail_activate:
            raise FederationError("maintainer_transition_invalid", exit_code=3)
        self.operations.append("activate")
        return _status()

    async def rotate_key(self, _maintainer_id: UUID, **options: object) -> MaintainerStatus:
        assert options["key_id"] == "maintainer-2026-02"
        self.operations.append("rotate-key")
        return _status()

    async def publish_release(
        self, release: SignedFederationRelease, **_options: object
    ) -> str:
        assert release.statement.publication_id == PUBLICATION_ID
        self.operations.append("publish-release")
        return "d" * 64

    async def quarantine(self, _maintainer_id: UUID, **_options: object) -> MaintainerStatus:
        self.operations.append("quarantine")
        return _status(FederationLifecycleState.QUARANTINED)

    async def revoke(self, _maintainer_id: UUID, **_options: object) -> MaintainerStatus:
        self.operations.append("revoke")
        return _status(FederationLifecycleState.REVOKED)

    async def status(self, _maintainer_id: UUID) -> MaintainerStatus:
        self.operations.append("status")
        return _status()

    async def record_rejected_attempt(self, **options: object) -> None:
        self.rejections.append(options)


def _release() -> SignedFederationRelease:
    return SignedFederationRelease(
        statement=FederationReleaseStatement(
            maintainer_id=MAINTAINER_ID,
            repository_id=SCOPE.repository_id,
            repository=SCOPE.repository,
            pack_id=SCOPE.pack_id,
            publication_id=PUBLICATION_ID,
            release_version="1.2.3.4",
            manifest_digest="b" * 64,
            receipt_digest="c" * 64,
            public_url="https://opennosh.org/api/v1/public/releases/1.2.3.4/manifest",
            issued_at=NOW,
            key_id="maintainer-2026-01",
        ),
        signature="A" * 86,
    )


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[_Engine, _Verifier, _Service]:
    engine = _Engine()
    verifier = _Verifier()
    service = _Service()
    monkeypatch.setattr(federation_cli, "build_administration_engine", lambda *_a, **_k: engine)
    monkeypatch.setattr(federation_cli, "async_sessionmaker", lambda *_a, **_k: "factory")
    monkeypatch.setattr(
        federation_cli,
        "GitHubInstallationVerifier",
        lambda **_options: verifier,
    )
    monkeypatch.setattr(federation_cli, "FederationService", lambda *_a, **_k: service)
    return engine, verifier, service


def test_federation_cli_runs_every_bounded_operation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    engine, verifier, service = _install_fakes(monkeypatch)
    token_file = tmp_path / "invitation.token"
    token_file.write_text("t" * 32, encoding="utf-8")
    public_key_file = tmp_path / "maintainer.pub"
    public_key_file.write_text(
        encode_public_key(Ed25519PrivateKey.from_private_bytes(b"a" * 32).public_key()),
        encoding="ascii",
    )
    release_file = tmp_path / "release.json"
    release_file.write_text(_release().model_dump_json(), encoding="utf-8")
    maintainer = str(MAINTAINER_ID)
    commands = (
        [
            "federation",
            "invite",
            "--github-account-id",
            str(SCOPE.github_account_id),
            "--github-login",
            SCOPE.github_login,
            "--repository-id",
            str(SCOPE.repository_id),
            "--repository",
            SCOPE.repository,
            "--pack-id",
            SCOPE.pack_id,
            "--expires-at",
            (NOW + timedelta(hours=1)).isoformat(),
        ],
        [
            "federation",
            "verify",
            "--token-file",
            str(token_file),
            "--github-account-id",
            str(SCOPE.github_account_id),
            "--repository-id",
            str(SCOPE.repository_id),
            "--pack-id",
            SCOPE.pack_id,
            "--installation-id",
            "157058059",
            "--key-id",
            "maintainer-2026-01",
            "--public-key-file",
            str(public_key_file),
            "--json",
        ],
        ["federation", "activate", "--maintainer-id", maintainer, "--reason", "activate"],
        [
            "federation",
            "rotate-key",
            "--maintainer-id",
            maintainer,
            "--reason",
            "rotate",
            "--key-id",
            "maintainer-2026-02",
            "--public-key-file",
            str(public_key_file),
            "--json",
        ],
        [
            "federation",
            "publish-release",
            "--release-file",
            str(release_file),
            "--reason",
            "bind governed release",
        ],
        [
            "federation",
            "quarantine",
            "--maintainer-id",
            maintainer,
            "--reason",
            "failure drill",
            "--json",
        ],
        ["federation", "revoke", "--maintainer-id", maintainer, "--reason", "revoke"],
        ["federation", "status", "--maintainer-id", maintainer, "--json"],
    )

    all_commands = commands + (commands[0] + ["--json"], commands[4] + ["--json"])
    for command in all_commands:
        arguments = root_cli.build_parser().parse_args(command)
        assert asyncio.run(federation_cli._run(arguments, _settings())) == 0

    assert service.operations == [
        "invite",
        "verify",
        "activate",
        "rotate-key",
        "publish-release",
        "quarantine",
        "revoke",
        "status",
        "invite",
        "publish-release",
    ]
    assert engine.disposals == len(all_commands)
    assert verifier.closes == len(all_commands)
    output = capsys.readouterr()
    assert "token printed once" in output.err
    assert "release verified" in output.err
    assert '"state": "verified"' in output.out


def test_federation_cli_records_rejected_operation_before_reraising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, verifier, service = _install_fakes(monkeypatch)
    service.fail_activate = True
    arguments = root_cli.build_parser().parse_args(
        [
            "federation",
            "activate",
            "--maintainer-id",
            str(MAINTAINER_ID),
            "--reason",
            "activate",
        ]
    )

    with pytest.raises(FederationError, match="maintainer_transition_invalid"):
        asyncio.run(federation_cli._run(arguments, _settings()))

    assert service.rejections == [
        {
            "actor_id": ACTOR_ID,
            "operation": "activate",
            "code": "maintainer_transition_invalid",
            "maintainer_id": MAINTAINER_ID,
            "token": None,
        }
    ]
    assert engine.disposals == 1
    assert verifier.closes == 1


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (FederationProviderError("identity_mismatch", identity_mismatch=True), 3),
        (FederationProviderError("provider_unavailable"), 5),
        (FederationError("invitation_invalid", exit_code=7), 7),
        (ValueError("invalid settings"), 2),
        (SQLAlchemyError("database unavailable"), 5),
    ],
)
def test_federation_cli_maps_failures_to_stable_exit_codes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
    expected: int,
) -> None:
    async def fail(_arguments: argparse.Namespace, _settings: object) -> int:
        raise error

    monkeypatch.setattr(federation_cli, "FederationOperatorSettings", _settings)
    monkeypatch.setattr(federation_cli, "_run", fail)

    assert federation_cli.run_federation_command(argparse.Namespace()) == expected
    assert "Federation" in capsys.readouterr().err


def test_federation_cli_redacts_invalid_operator_configuration(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw_scope_policy = "raw-reviewed-scope-policy-must-not-be-logged"
    validation_error = ValidationError.from_exception_data(
        "FederationOperatorSettings",
        [
            {
                "type": "value_error",
                "loc": (),
                "input": {"allowed_scopes_json": raw_scope_policy},
                "ctx": {"error": ValueError("scope policy is invalid")},
            }
        ],
    )

    def invalid_settings() -> None:
        raise validation_error

    monkeypatch.setattr(federation_cli, "FederationOperatorSettings", invalid_settings)

    assert federation_cli.run_federation_command(argparse.Namespace()) == 2
    error = capsys.readouterr().err
    assert error == "Federation configuration failed: configuration_invalid\n"
    assert raw_scope_policy not in error


def test_federation_cli_rejects_missing_token_file() -> None:
    with pytest.raises(ValueError, match="token file is missing"):
        federation_cli._read_token(argparse.Namespace(token_file="not-a-path"))


def test_root_cli_dispatches_federation_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(root_cli, "run_federation_command", lambda _arguments: 19)

    assert (
        root_cli.main(
            [
                "federation",
                "status",
                "--maintainer-id",
                str(MAINTAINER_ID),
            ]
        )
        == 19
    )
