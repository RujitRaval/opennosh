from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from opennosh_api.database import build_administration_engine
from opennosh_api.federation.contracts import (
    FederationScope,
    MaintainerStatus,
    SignedFederationRelease,
    load_public_key,
)
from opennosh_api.federation.github import (
    FederationProviderError,
    GitHubInstallationVerifier,
)
from opennosh_api.federation.service import FederationError, FederationService
from opennosh_api.federation.settings import FederationOperatorSettings


def add_federation_parser(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    federation = commands.add_parser(
        "federation", help="Manage invitation-only federation enrollment"
    )
    operations = federation.add_subparsers(dest="federation_command", required=True)

    invite = operations.add_parser("invite", help="Create the single configured invitation")
    _add_scope_arguments(invite, include_login=True)
    invite.add_argument("--expires-at", type=datetime.fromisoformat, required=True)
    invite.add_argument("--json", action="store_true")

    verify = operations.add_parser("verify", help="Consume and verify an invitation")
    verify.add_argument("--token-file", type=Path, required=True)
    verify.add_argument("--github-account-id", type=int, required=True)
    verify.add_argument("--repository-id", type=int, required=True)
    verify.add_argument("--pack-id", required=True)
    verify.add_argument("--installation-id", type=int, required=True)
    verify.add_argument("--key-id", required=True)
    verify.add_argument("--public-key-file", type=Path, required=True)
    verify.add_argument("--json", action="store_true")

    activate = operations.add_parser("activate", help="Activate one verified maintainer")
    _add_lifecycle_arguments(activate)

    rotate = operations.add_parser("rotate-key", help="Rotate the active online role key")
    _add_lifecycle_arguments(rotate)
    rotate.add_argument("--key-id", required=True)
    rotate.add_argument("--public-key-file", type=Path, required=True)

    publish = operations.add_parser(
        "publish-release",
        help="Bind one maintainer-signed release to a governed publication receipt",
    )
    publish.add_argument("--release-file", type=Path, required=True)
    publish.add_argument("--reason", required=True)
    publish.add_argument("--json", action="store_true")

    quarantine = operations.add_parser("quarantine", help="Quarantine an active scope")
    _add_lifecycle_arguments(quarantine)
    revoke = operations.add_parser("revoke", help="Revoke an active scope")
    _add_lifecycle_arguments(revoke)

    status = operations.add_parser("status", help="Read redacted maintainer status")
    status.add_argument("--maintainer-id", type=UUID, required=True)
    status.add_argument("--json", action="store_true")


def _add_scope_arguments(parser: argparse.ArgumentParser, *, include_login: bool) -> None:
    parser.add_argument("--github-account-id", type=int, required=True)
    if include_login:
        parser.add_argument("--github-login", required=True)
    parser.add_argument("--repository-id", type=int, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pack-id", required=True)


def _add_lifecycle_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--maintainer-id", type=UUID, required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--json", action="store_true")


def run_federation_command(arguments: argparse.Namespace) -> int:
    try:
        settings = FederationOperatorSettings()  # type: ignore[call-arg]
        return asyncio.run(_run(arguments, settings))
    except FederationProviderError as error:
        prefix = "identity" if error.identity_mismatch else "provider"
        print(f"Federation {prefix} verification failed: {error.code}", file=sys.stderr)
        return 3 if error.identity_mismatch else 5
    except FederationError as error:
        print(f"Federation operation failed: {error.code}", file=sys.stderr)
        return error.exit_code
    except (ValidationError, json.JSONDecodeError, OSError, ValueError) as error:
        print(f"Federation configuration failed: {error}", file=sys.stderr)
        return 2
    except (DBAPIError, IntegrityError, SQLAlchemyError, LookupError):
        print("Federation operation failed: database operation failed", file=sys.stderr)
        return 5


async def _run(
    arguments: argparse.Namespace,
    settings: FederationOperatorSettings,
) -> int:
    engine = build_administration_engine(
        settings.administration_database_url,
        manifest_path=settings.database_capacity_manifest_path,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    verifier = GitHubInstallationVerifier(
        app_id=settings.github_app_id,
        private_key_pem=settings.github_app_private_key.get_secret_value(),
    )
    service = FederationService(
        factory,
        allowed_scope=settings.allowed_scope,
        allowed_public_origin=settings.allowed_public_origin,
        installation_verifier=verifier,
    )
    try:
        command = arguments.federation_command
        if command == "invite":
            scope = FederationScope(
                github_account_id=arguments.github_account_id,
                github_login=arguments.github_login,
                repository_id=arguments.repository_id,
                repository=arguments.repository,
                pack_id=arguments.pack_id,
            )
            invitation = await service.invite(
                scope,
                inviter_actor_id=settings.inviter_actor_id,
                expires_at=arguments.expires_at,
            )
            if arguments.json:
                print(json.dumps(invitation.model_dump(mode="json"), sort_keys=True))
            else:
                print(invitation.token)
                print(
                    f"Invitation {invitation.invitation_id} created; token printed once",
                    file=sys.stderr,
                )
            return 0
        if command == "verify":
            scope = FederationScope(
                github_account_id=arguments.github_account_id,
                github_login=settings.allowed_github_login,
                repository_id=arguments.repository_id,
                repository=settings.allowed_repository,
                pack_id=arguments.pack_id,
            )
            encoded_key, fingerprint = load_public_key(arguments.public_key_file)
            status = await service.verify(
                token=_read_token(arguments),
                scope=scope,
                installation_id=arguments.installation_id,
                key_id=arguments.key_id,
                public_key=encoded_key,
                public_key_fingerprint=fingerprint,
            )
            _print_status(status, as_json=arguments.json)
            return 0
        if command == "activate":
            status = await service.activate(
                arguments.maintainer_id,
                actor_id=settings.inviter_actor_id,
                reason=arguments.reason,
            )
            _print_status(status, as_json=arguments.json)
            return 0
        if command == "rotate-key":
            encoded_key, fingerprint = load_public_key(arguments.public_key_file)
            status = await service.rotate_key(
                arguments.maintainer_id,
                key_id=arguments.key_id,
                public_key=encoded_key,
                public_key_fingerprint=fingerprint,
                actor_id=settings.inviter_actor_id,
                reason=arguments.reason,
            )
            _print_status(status, as_json=arguments.json)
            return 0
        if command == "publish-release":
            release = SignedFederationRelease.model_validate_json(
                arguments.release_file.read_text(encoding="utf-8")
            )
            digest = await service.publish_release(
                release,
                actor_id=settings.inviter_actor_id,
                reason=arguments.reason,
            )
            result = {"schema_version": "1.0", "statement_digest": digest, "verified": True}
            if arguments.json:
                print(json.dumps(result, sort_keys=True))
            else:
                print(f"Federation release verified: {digest}", file=sys.stderr)
            return 0
        if command == "quarantine":
            status = await service.quarantine(
                arguments.maintainer_id,
                actor_id=settings.inviter_actor_id,
                reason=arguments.reason,
            )
            _print_status(status, as_json=arguments.json)
            return 0
        if command == "revoke":
            status = await service.revoke(
                arguments.maintainer_id,
                actor_id=settings.inviter_actor_id,
                reason=arguments.reason,
            )
            _print_status(status, as_json=arguments.json)
            return 0
        if command == "status":
            _print_status(await service.status(arguments.maintainer_id), as_json=arguments.json)
            return 0
        raise AssertionError(f"unsupported federation command: {command}")
    except (FederationError, FederationProviderError, LookupError) as error:
        code = getattr(error, "code", str(error))
        await service.record_rejected_attempt(
            actor_id=_command_actor(arguments, settings),
            operation=arguments.federation_command,
            code=code,
            maintainer_id=getattr(arguments, "maintainer_id", None),
            token=_read_token(arguments) if arguments.federation_command == "verify" else None,
        )
        raise
    finally:
        await verifier.aclose()
        await engine.dispose()


def _read_token(arguments: argparse.Namespace) -> str:
    path = arguments.token_file
    if not isinstance(path, Path):
        raise ValueError("Federation invitation token file is missing")
    return path.read_text(encoding="utf-8").strip()


def _command_actor(
    _arguments: argparse.Namespace,
    settings: FederationOperatorSettings,
) -> UUID:
    return settings.inviter_actor_id


def _print_status(status: MaintainerStatus, *, as_json: bool) -> None:
    payload = status.model_dump(mode="json")
    if as_json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(
            f"Federation maintainer {payload['maintainer_id']}: {payload['state']}",
            file=sys.stderr,
        )
