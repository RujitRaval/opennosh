from __future__ import annotations

import argparse
import asyncio
import json
import os
import stat
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
from opennosh_api.federation.drills import (
    DEFAULT_DRILL_CONTRACT_PATH,
    FailureDrillInvariantError,
    FailureDrillSecretError,
    canonical_digest,
    load_failure_drill_contract,
    parse_failure_drill_report,
    validate_failure_drill_report,
)
from opennosh_api.federation.github import (
    FederationProviderError,
    GitHubInstallationVerifier,
)
from opennosh_api.federation.service import FederationError, FederationService
from opennosh_api.federation.settings import FederationOperatorSettings
from opennosh_api.public.artifacts import MAX_MANIFEST_BYTES, MAX_PACK_BYTES
from opennosh_api.public_commons.manifests import ManifestKeyRing


def add_federation_parser(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    federation = commands.add_parser(
        "federation", help="Manage invitation-only federation enrollment"
    )
    operations = federation.add_subparsers(dest="federation_command", required=True)

    invite = operations.add_parser("invite", help="Create one configured-scope invitation")
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

    verify_artifacts = operations.add_parser(
        "verify-artifacts",
        help="Verify one signed release manifest and food-pack artifact",
    )
    verify_artifacts.add_argument("--statement-digest", required=True)
    verify_artifacts.add_argument("--manifest-file", type=Path, required=True)
    verify_artifacts.add_argument("--pack-file", type=Path, required=True)
    verify_artifacts.add_argument("--reason", required=True)
    verify_artifacts.add_argument("--json", action="store_true")

    quarantine_release = operations.add_parser(
        "quarantine-release",
        help="Exclude one verified release from future projections",
    )
    quarantine_release.add_argument("--statement-digest", required=True)
    quarantine_release.add_argument("--reason", required=True)
    quarantine_release.add_argument("--json", action="store_true")

    build_projection = operations.add_parser(
        "build-projection",
        help="Atomically build and activate the verified release-set projection",
    )
    build_projection.add_argument("--reason", required=True)
    build_projection.add_argument("--json", action="store_true")

    quarantine = operations.add_parser("quarantine", help="Quarantine an active scope")
    _add_lifecycle_arguments(quarantine)
    revoke = operations.add_parser("revoke", help="Revoke an active scope")
    _add_lifecycle_arguments(revoke)

    status = operations.add_parser("status", help="Read redacted maintainer status")
    status.add_argument("--maintainer-id", type=UUID, required=True)
    status.add_argument("--json", action="store_true")

    plan = operations.add_parser("drill-plan", help="Print the canonical failure-drill matrix")
    plan.add_argument("--contract-file", type=Path, default=DEFAULT_DRILL_CONTRACT_PATH)
    plan.add_argument("--json", action="store_true")

    validate = operations.add_parser(
        "validate-drill-report",
        help="Validate a redacted failure-drill report without production credentials",
    )
    validate.add_argument("--contract-file", type=Path, default=DEFAULT_DRILL_CONTRACT_PATH)
    validate.add_argument("--report-file", type=Path, required=True)
    validate.add_argument("--json", action="store_true")


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
    if getattr(arguments, "federation_command", None) in {
        "drill-plan",
        "validate-drill-report",
    }:
        return _run_failure_drill_command(arguments)
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
    except ValidationError:
        print("Federation configuration failed: configuration_invalid", file=sys.stderr)
        return 2
    except (json.JSONDecodeError, OSError, ValueError) as error:
        print(f"Federation configuration failed: {error}", file=sys.stderr)
        return 2
    except (DBAPIError, IntegrityError, SQLAlchemyError, LookupError):
        print("Federation operation failed: database operation failed", file=sys.stderr)
        return 5


def _run_failure_drill_command(arguments: argparse.Namespace) -> int:
    try:
        contract = load_failure_drill_contract(arguments.contract_file)
    except (OSError, ValidationError, json.JSONDecodeError, ValueError):
        print("Federation drill validation failed: drill_contract_invalid", file=sys.stderr)
        return 2

    if arguments.federation_command == "drill-plan":
        payload = contract.model_dump(mode="json")
        if arguments.json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print(
                f"Federation failure-drill matrix: {len(contract.cases)} cases "
                f"({canonical_digest(contract)})"
            )
        return 0

    try:
        report_payload = arguments.report_file.read_bytes()
    except OSError:
        print("Federation drill validation failed: drill_report_io_failed", file=sys.stderr)
        return 5
    try:
        report = parse_failure_drill_report(report_payload)
    except FailureDrillSecretError:
        print(
            "Federation drill validation failed: drill_report_secret_pattern_detected",
            file=sys.stderr,
        )
        return 2
    except (ValidationError, json.JSONDecodeError, ValueError):
        print("Federation drill validation failed: drill_report_invalid", file=sys.stderr)
        return 2
    try:
        report_digest = validate_failure_drill_report(report, contract)
    except FailureDrillInvariantError as error:
        print(f"Federation drill validation failed: {error}", file=sys.stderr)
        return 3
    summary = {
        "case_count": len(report.drills),
        "contract_digest": report.contract_digest,
        "report_digest": report_digest,
        "schema_version": report.schema_version,
        "status": "passed",
    }
    if arguments.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(f"Federation failure-drill report passed: {report_digest}")
    return 0


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
        allowed_scopes=settings.allowed_scopes,
        allowed_public_origin=settings.allowed_public_origin,
        installation_verifier=verifier,
        manifest_keys=(
            ManifestKeyRing.from_config(
                settings.manifest_verifying_keys.get_secret_value()
            )
            if settings.manifest_verifying_keys is not None
            else None
        ),
        ingestion_enabled=settings.ingestion_enabled,
        projection_enabled=settings.projection_enabled,
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
            verified_scope = next(
                (
                    candidate
                    for candidate in settings.allowed_scopes
                    if candidate.github_account_id == arguments.github_account_id
                    and candidate.repository_id == arguments.repository_id
                    and candidate.pack_id == arguments.pack_id
                ),
                None,
            )
            if verified_scope is None:
                raise FederationError("federation_scope_not_invited", exit_code=3)
            encoded_key, fingerprint = load_public_key(arguments.public_key_file)
            status = await service.verify(
                token=_read_token(arguments),
                scope=verified_scope,
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
        if command == "verify-artifacts":
            artifact_status = await service.verify_release_artifacts(
                arguments.statement_digest,
                manifest_bytes=_read_limited(
                    arguments.manifest_file,
                    max_bytes=MAX_MANIFEST_BYTES,
                ),
                pack_bytes=_read_limited(arguments.pack_file, max_bytes=MAX_PACK_BYTES),
                actor_id=settings.inviter_actor_id,
                reason=arguments.reason,
            )
            _print_payload(
                artifact_status.model_dump(mode="json"),
                as_json=arguments.json,
            )
            return 0
        if command == "quarantine-release":
            release_status = await service.quarantine_release(
                arguments.statement_digest,
                actor_id=settings.inviter_actor_id,
                reason=arguments.reason,
            )
            _print_payload(
                release_status.model_dump(mode="json"),
                as_json=arguments.json,
            )
            return 0
        if command == "build-projection":
            projection_status = await service.build_projection(
                actor_id=settings.inviter_actor_id,
                reason=arguments.reason,
            )
            _print_payload(
                projection_status.model_dump(mode="json"),
                as_json=arguments.json,
            )
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


def _read_limited(path: Path, *, max_bytes: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ValueError("Federation artifact input must be a regular file") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("Federation artifact input must be a regular file")
        if metadata.st_size > max_bytes:
            raise ValueError("Federation artifact input exceeds its size limit")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            payload = stream.read(max_bytes + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not payload or len(payload) > max_bytes:
        raise ValueError("Federation artifact input has an invalid size")
    return payload


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


def _print_payload(payload: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(json.dumps(payload, sort_keys=True), file=sys.stderr)
