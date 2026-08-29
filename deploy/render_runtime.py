"""Render-only bootstrap for least-privilege database roles and process startup."""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
from collections.abc import Mapping
from uuid import UUID

import asyncpg  # type: ignore[import-untyped]
from sqlalchemy.engine import make_url

MIGRATION_ROLE = "opennosh_migration"
WEB_ROLE = "opennosh_web"
PUBLICATION_ROLE = "opennosh_publication"

PUBLICATION_TABLE_PRIVILEGES = {
    "accepted_events": "SELECT, INSERT",
    "evidence_manifests": "SELECT",
    "evidence_removal_tombstones": "SELECT",
    "governance_decisions": "SELECT",
    "governance_merge_authorizations": "SELECT, INSERT",
    "governance_publication_interventions": "SELECT",
    "governance_publication_pauses": "SELECT",
    "governance_recusals": "SELECT",
    "governance_role_assignments": "SELECT",
    "publication_durable_acknowledgements": "SELECT, INSERT",
    "publication_intents": "SELECT, UPDATE",
    "publication_receipts": "SELECT, INSERT",
    "publication_steps": "SELECT, INSERT, UPDATE",
    "opennosh_pgqueuer": "SELECT, INSERT, UPDATE, DELETE",
    "opennosh_pgqueuer_log": "SELECT, INSERT, UPDATE",
    "opennosh_pgqueuer_schedules": "SELECT, INSERT, UPDATE, DELETE",
    "opennosh_pgqueuer_statistics": "SELECT, INSERT, UPDATE",
}
# PostgreSQL row-locking clauses require UPDATE privilege on at least one
# selected column.  The governance gate locks the immutable evidence row while
# authorizing a merge, so grant only the primary-key column needed for that
# lock instead of table-wide UPDATE access.
PUBLICATION_COLUMN_PRIVILEGES = {
    "evidence_manifests": {"UPDATE": ("id",)},
}
PUBLICATION_SEQUENCES = (
    "opennosh_pgqueuer_id_seq",
    "opennosh_pgqueuer_log_id_seq",
    "opennosh_pgqueuer_schedules_id_seq",
    "opennosh_pgqueuer_statistics_id_seq",
)
RAW_DATABASE_SECRETS = (
    "RENDER_DATABASE_URL",
    "MIGRATION_DATABASE_PASSWORD",
    "WEB_DATABASE_PASSWORD",
    "PUBLICATION_DATABASE_PASSWORD",
)
PROCESS_RUNTIME_ENVIRONMENT_KEYS = (
    "PATH",
    "PYTHONPATH",
    "PYTHONUNBUFFERED",
    "PYTHONDONTWRITEBYTECODE",
    "LANG",
    "LC_ALL",
    "TZ",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
)
PUBLICATION_ENVIRONMENT_KEYS = (
    *PROCESS_RUNTIME_ENVIRONMENT_KEYS,
    "APP_ENVIRONMENT",
    "DATABASE_CAPACITY_MANIFEST_PATH",
    "PUBLICATION_CLAIMS_ENABLED",
    "PUBLICATION_PREACTIVATION_SMOKE_ENABLED",
    "LATEST_REFRESH_ENABLED",
    "PUBLICATION_ACTIVATION_IDS",
    "LATEST_REFRESH_INTERVAL_SECONDS",
    "LATEST_REFRESH_AFTER_SECONDS",
    "LATEST_POINTER_LIFETIME_SECONDS",
    "PUBLIC_ARTIFACT_BASE_URL",
    "PUBLIC_ARTIFACT_TIMEOUT_SECONDS",
    "PUBLIC_COMMONS_VERIFYING_KEYS",
    "PUBLICATION_RECEIPT_VERIFYING_KEYS",
    "ONLINE_MANIFEST_SIGNING_KEY_ID",
    "ONLINE_MANIFEST_SIGNING_KEY",
    "ONLINE_RECEIPT_SIGNING_KEY_ID",
    "ONLINE_RECEIPT_SIGNING_KEY",
    "GITHUB_FORGE_REPOSITORY_ID",
    "GITHUB_FORGE_APP_ID",
    "GITHUB_FORGE_INSTALLATION_ID",
    "GITHUB_FORGE_PRIVATE_KEY",
    "GITHUB_ATTESTER_APP_ID",
    "GITHUB_ATTESTER_INSTALLATION_ID",
    "GITHUB_ATTESTER_PRIVATE_KEY",
    "PUBLICATION_ARTIFACT_BUCKET",
    "R2_ACCOUNT_ID",
    "R2_BUCKET",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
)
PUBLICATION_PRIVATE_ENVIRONMENT_KEYS = (
    "ONLINE_MANIFEST_SIGNING_KEY_ID",
    "ONLINE_MANIFEST_SIGNING_KEY",
    "ONLINE_RECEIPT_SIGNING_KEY_ID",
    "ONLINE_RECEIPT_SIGNING_KEY",
    "GITHUB_FORGE_REPOSITORY_ID",
    "GITHUB_FORGE_APP_ID",
    "GITHUB_FORGE_INSTALLATION_ID",
    "GITHUB_FORGE_PRIVATE_KEY",
    "GITHUB_ATTESTER_APP_ID",
    "GITHUB_ATTESTER_INSTALLATION_ID",
    "GITHUB_ATTESTER_PRIVATE_KEY",
    "PUBLICATION_ARTIFACT_BUCKET",
    "R2_ACCOUNT_ID",
    "R2_BUCKET",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
)

ROLE_DATABASE_URLS = (
    "DATABASE_URL",
    "WEB_DATABASE_URL",
    "PUBLICATION_DATABASE_URL",
    "EVIDENCE_DATABASE_URL",
    "PROJECTION_DATABASE_URL",
    "RECONCILER_DATABASE_URL",
    "SCHEDULER_DATABASE_URL",
    "MIGRATION_DATABASE_URL",
    "ADMINISTRATION_DATABASE_URL",
    "DATABASE_CAPACITY_URL",
)


def _required(environment: Mapping[str, str], key: str) -> str:
    value = environment.get(key)
    if value is None or not value.strip():
        raise ValueError(f"{key} is required")
    return value


def role_database_url(source_url: str, username: str, password: str) -> str:
    """Replace Render's owner credentials and select SQLAlchemy's async driver."""

    parsed = make_url(source_url)
    if parsed.get_backend_name() != "postgresql" or parsed.database is None:
        raise ValueError("RENDER_DATABASE_URL must be a PostgreSQL database URL")
    return parsed.set(
        drivername="postgresql+asyncpg",
        username=username,
        password=password,
    ).render_as_string(hide_password=False)


def asyncpg_dsn(source_url: str) -> str:
    """Return an asyncpg-compatible DSN without SQLAlchemy's driver suffix."""

    parsed = make_url(source_url)
    if parsed.get_backend_name() != "postgresql" or parsed.database is None:
        raise ValueError("Database URL must use PostgreSQL and name a database")
    return parsed.set(drivername="postgresql").render_as_string(hide_password=False)


def _strip_database_credentials(
    environment: dict[str, str],
    *,
    preserve_url: str,
) -> None:
    for secret in (*RAW_DATABASE_SECRETS, *ROLE_DATABASE_URLS):
        if secret != preserve_url:
            environment.pop(secret, None)


def api_environment(source: Mapping[str, str]) -> dict[str, str]:
    """Build the API environment without retaining owner or sibling-role credentials."""

    environment = dict(source)
    owner_url = _required(environment, "RENDER_DATABASE_URL")
    web_password = _required(environment, "WEB_DATABASE_PASSWORD")
    cursor_secret = _required(environment, "FOOD_SEARCH_CURSOR_SECRET")
    environment["PROCESS_ROLE"] = "web"
    environment["WEB_DATABASE_URL"] = role_database_url(owner_url, WEB_ROLE, web_password)
    environment["FOOD_SEARCH_CURSOR_SIGNING_KEYS"] = f"render-v1:{cursor_secret}"

    _strip_database_credentials(environment, preserve_url="WEB_DATABASE_URL")
    environment.pop("FOOD_SEARCH_CURSOR_SECRET", None)
    for key in PUBLICATION_PRIVATE_ENVIRONMENT_KEYS:
        environment.pop(key, None)
    return environment


def publication_environment(source: Mapping[str, str]) -> dict[str, str]:
    """Build a mode-bounded worker environment without retaining sibling credentials."""

    environment = {
        key: value for key in PUBLICATION_ENVIRONMENT_KEYS if (value := source.get(key)) is not None
    }
    claims_enabled = environment.get("PUBLICATION_CLAIMS_ENABLED", "false").casefold() == "true"
    smoke_enabled = (
        environment.get("PUBLICATION_PREACTIVATION_SMOKE_ENABLED", "false").casefold()
        == "true"
    )
    refresh_enabled = environment.get("LATEST_REFRESH_ENABLED", "false").casefold() == "true"
    if not claims_enabled and not refresh_enabled:
        raise ValueError("Publication worker requires an enabled runtime mode")
    if claims_enabled and not refresh_enabled:
        raise ValueError("Publication claims require latest refresh to remain enabled")
    if claims_enabled and smoke_enabled:
        raise ValueError("Publication preactivation smoke requires claims disabled")
    environment["PROCESS_ROLE"] = "publication"
    if claims_enabled or smoke_enabled:
        if not refresh_enabled:
            raise ValueError("Publication activation requires latest refresh enabled")
    if claims_enabled:
        activation_id = _required(source, "PUBLICATION_ACTIVATION_IDS")
        try:
            parsed_activation_id = UUID(activation_id)
        except ValueError as error:
            raise ValueError("PUBLICATION_ACTIVATION_IDS must be one canonical UUID") from error
        if str(parsed_activation_id) != activation_id:
            raise ValueError("PUBLICATION_ACTIVATION_IDS must be one canonical UUID")
    if claims_enabled or smoke_enabled:
        for key in (
            "GITHUB_FORGE_REPOSITORY_ID",
            "GITHUB_FORGE_APP_ID",
            "GITHUB_FORGE_INSTALLATION_ID",
            "GITHUB_FORGE_PRIVATE_KEY",
            "GITHUB_ATTESTER_APP_ID",
            "GITHUB_ATTESTER_INSTALLATION_ID",
            "GITHUB_ATTESTER_PRIVATE_KEY",
            "ONLINE_RECEIPT_SIGNING_KEY_ID",
            "ONLINE_RECEIPT_SIGNING_KEY",
            "PUBLICATION_ARTIFACT_BUCKET",
        ):
            _required(source, key)
    if claims_enabled:
        owner_url = _required(source, "RENDER_DATABASE_URL")
        publication_password = _required(source, "PUBLICATION_DATABASE_PASSWORD")
        environment["PUBLICATION_DATABASE_URL"] = role_database_url(
            owner_url,
            PUBLICATION_ROLE,
            publication_password,
        )
    return environment


async def _quoted(connection: asyncpg.Connection, value: str, *, identifier: bool) -> str:
    function = "quote_ident" if identifier else "quote_literal"
    quoted = await connection.fetchval(f"SELECT {function}($1)", value)
    if not isinstance(quoted, str):
        raise RuntimeError(f"PostgreSQL {function} did not return text")
    return quoted


async def ensure_database_roles(
    owner_url: str,
    migration_password: str,
    web_password: str,
    publication_password: str | None = None,
) -> None:
    """Create or rotate bounded production roles using Render's database owner."""

    database_name = make_url(owner_url).database
    if database_name is None:
        raise ValueError("RENDER_DATABASE_URL must name a database")

    connection = await asyncpg.connect(asyncpg_dsn(owner_url))
    try:
        database_identifier = await _quoted(connection, database_name, identifier=True)
        migration_password_literal = await _quoted(connection, migration_password, identifier=False)
        web_password_literal = await _quoted(connection, web_password, identifier=False)
        role_passwords = [
            (MIGRATION_ROLE, migration_password_literal),
            (WEB_ROLE, web_password_literal),
        ]
        if publication_password is not None:
            publication_password_literal = await _quoted(
                connection, publication_password, identifier=False
            )
            role_passwords.append((PUBLICATION_ROLE, publication_password_literal))
        async with connection.transaction():
            for role, _password_literal in role_passwords:
                role_identifier = await _quoted(connection, role, identifier=True)
                exists = await connection.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = $1)", role
                )
                if not exists:
                    await connection.execute(f"CREATE ROLE {role_identifier} LOGIN")

            for role, password_literal in role_passwords:
                await connection.execute(
                    f"ALTER ROLE {role} LOGIN PASSWORD {password_literal} "
                    "NOCREATEDB NOCREATEROLE NOINHERIT"
                )
                bounded = await connection.fetchval(
                    "SELECT NOT ("
                    "rolsuper OR rolcreatedb OR rolcreaterole OR rolinherit "
                    "OR rolreplication OR rolbypassrls"
                    ") FROM pg_roles WHERE rolname = $1",
                    role,
                )
                if bounded is not True:
                    raise RuntimeError(f"Database role {role} is not bounded")
            connected_roles = ", ".join(role for role, _password in role_passwords)
            await connection.execute(
                f"GRANT CONNECT ON DATABASE {database_identifier} TO {connected_roles}"
            )
            await connection.execute(
                f"GRANT CREATE ON DATABASE {database_identifier} TO {MIGRATION_ROLE}"
            )
            await connection.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            await connection.execute(f"GRANT USAGE, CREATE ON SCHEMA public TO {MIGRATION_ROLE}")
            await connection.execute(f"GRANT USAGE ON SCHEMA public TO {WEB_ROLE}")
            if publication_password is not None:
                await connection.execute(f"GRANT USAGE ON SCHEMA public TO {PUBLICATION_ROLE}")
    finally:
        await connection.close()


async def grant_web_runtime_privileges(migration_url: str) -> None:
    """Refresh runtime grants after Alembic creates or changes database objects."""

    connection = await asyncpg.connect(asyncpg_dsn(migration_url))
    try:
        async with connection.transaction():
            await connection.execute(f"GRANT USAGE ON SCHEMA public TO {WEB_ROLE}")
            await connection.execute(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {WEB_ROLE}"
            )
            await connection.execute(
                f"GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO {WEB_ROLE}"
            )
            await connection.execute(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES "
                f"TO {WEB_ROLE}"
            )
            await connection.execute(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                f"GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO {WEB_ROLE}"
            )
    finally:
        await connection.close()


async def grant_publication_runtime_privileges(migration_url: str) -> None:
    """Grant the worker only its reviewed ledger and queue database objects."""

    connection = await asyncpg.connect(asyncpg_dsn(migration_url))
    try:
        async with connection.transaction():
            await connection.execute(f"GRANT USAGE ON SCHEMA public TO {PUBLICATION_ROLE}")
            for table, privileges in PUBLICATION_TABLE_PRIVILEGES.items():
                await connection.execute(
                    f"GRANT {privileges} ON TABLE {table} TO {PUBLICATION_ROLE}"
                )
            for table, grants in PUBLICATION_COLUMN_PRIVILEGES.items():
                for privilege, columns in grants.items():
                    column_list = ", ".join(columns)
                    await connection.execute(
                        f"GRANT {privilege} ({column_list}) ON TABLE {table} TO {PUBLICATION_ROLE}"
                    )
            for sequence in PUBLICATION_SEQUENCES:
                await connection.execute(
                    f"GRANT USAGE, SELECT, UPDATE ON SEQUENCE {sequence} TO {PUBLICATION_ROLE}"
                )
    finally:
        await connection.close()


def run_predeploy(source: Mapping[str, str]) -> None:
    owner_url = _required(source, "RENDER_DATABASE_URL")
    migration_password = _required(source, "MIGRATION_DATABASE_PASSWORD")
    web_password = _required(source, "WEB_DATABASE_PASSWORD")
    publication_password = source.get("PUBLICATION_DATABASE_PASSWORD")
    if publication_password is not None:
        publication_password = _required(source, "PUBLICATION_DATABASE_PASSWORD")
    migration_url = role_database_url(owner_url, MIGRATION_ROLE, migration_password)

    asyncio.run(
        ensure_database_roles(
            owner_url,
            migration_password,
            web_password,
            publication_password,
        )
    )

    environment = api_environment(source)
    environment["DATABASE_CAPACITY_URL"] = migration_url
    subprocess.run(
        [
            "opennosh-capacity-preflight",
            "--manifest",
            "/app/config/database-capacity.v1.json",
            "--require-live-database",
            "--require-deployment-topology",
            "--deployed-role",
            "web=1",
            "--deployed-role",
            "publication=1",
            "--deployed-role",
            "evidence=0",
            "--deployed-role",
            "projection=0",
            "--deployed-role",
            "reconciler=0",
            "--deployed-role",
            "scheduler=0",
        ],
        check=True,
        env=environment,
    )
    environment.pop("DATABASE_CAPACITY_URL")
    environment["MIGRATION_DATABASE_URL"] = migration_url
    subprocess.run(["opennosh-migrate"], check=True, env=environment)
    asyncio.run(grant_web_runtime_privileges(migration_url))
    if publication_password is not None:
        asyncio.run(grant_publication_runtime_privileges(migration_url))
    environment["ADMINISTRATION_DATABASE_URL"] = migration_url
    subprocess.run(
        ["opennosh", "foods", "load", "/app/packs", "--json"],
        check=True,
        env=environment,
    )


def run_api(source: Mapping[str, str]) -> None:
    os.execvpe("opennosh-web", ["opennosh-web"], api_environment(source))


def run_publication(source: Mapping[str, str]) -> None:
    os.execvpe(
        "opennosh-publication-worker",
        ["opennosh-publication-worker"],
        publication_environment(source),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("api", "predeploy", "publication"))
    arguments = parser.parse_args()
    if arguments.mode == "predeploy":
        run_predeploy(os.environ)
    elif arguments.mode == "api":
        run_api(os.environ)
    else:
        run_publication(os.environ)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
