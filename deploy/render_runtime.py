"""Render-only bootstrap for least-privilege database roles and API startup."""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
from collections.abc import Mapping

import asyncpg  # type: ignore[import-untyped]
from sqlalchemy.engine import make_url

MIGRATION_ROLE = "opennosh_migration"
WEB_ROLE = "opennosh_web"


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


def api_environment(source: Mapping[str, str]) -> dict[str, str]:
    """Build the API environment without retaining Render's owner credential."""

    environment = dict(source)
    owner_url = _required(environment, "RENDER_DATABASE_URL")
    web_password = _required(environment, "WEB_DATABASE_PASSWORD")
    cursor_secret = _required(environment, "FOOD_SEARCH_CURSOR_SECRET")
    environment["WEB_DATABASE_URL"] = role_database_url(owner_url, WEB_ROLE, web_password)
    environment["FOOD_SEARCH_CURSOR_SIGNING_KEYS"] = f"render-v1:{cursor_secret}"

    for secret in (
        "RENDER_DATABASE_URL",
        "MIGRATION_DATABASE_PASSWORD",
        "WEB_DATABASE_PASSWORD",
        "FOOD_SEARCH_CURSOR_SECRET",
    ):
        environment.pop(secret, None)
    return environment


async def _quoted(connection: asyncpg.Connection, value: str, *, identifier: bool) -> str:
    function = "quote_ident" if identifier else "quote_literal"
    quoted = await connection.fetchval(f"SELECT {function}($1)", value)
    if not isinstance(quoted, str):
        raise RuntimeError(f"PostgreSQL {function} did not return text")
    return quoted


async def ensure_database_roles(owner_url: str, migration_password: str, web_password: str) -> None:
    """Create or rotate the two production roles using Render's database owner."""

    database_name = make_url(owner_url).database
    if database_name is None:
        raise ValueError("RENDER_DATABASE_URL must name a database")

    connection = await asyncpg.connect(asyncpg_dsn(owner_url))
    try:
        database_identifier = await _quoted(connection, database_name, identifier=True)
        migration_password_literal = await _quoted(
            connection, migration_password, identifier=False
        )
        web_password_literal = await _quoted(connection, web_password, identifier=False)
        async with connection.transaction():
            for role in (MIGRATION_ROLE, WEB_ROLE):
                role_identifier = await _quoted(connection, role, identifier=True)
                exists = await connection.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = $1)", role
                )
                if not exists:
                    await connection.execute(f"CREATE ROLE {role_identifier} LOGIN")

            await connection.execute(
                f"ALTER ROLE {MIGRATION_ROLE} LOGIN PASSWORD {migration_password_literal} "
                "NOCREATEDB NOCREATEROLE"
            )
            await connection.execute(
                f"ALTER ROLE {WEB_ROLE} LOGIN PASSWORD {web_password_literal} "
                "NOCREATEDB NOCREATEROLE"
            )
            await connection.execute(
                f"GRANT CONNECT ON DATABASE {database_identifier} TO {MIGRATION_ROLE}, {WEB_ROLE}"
            )
            await connection.execute(
                f"GRANT CREATE ON DATABASE {database_identifier} TO {MIGRATION_ROLE}"
            )
            await connection.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            await connection.execute(
                f"GRANT USAGE, CREATE ON SCHEMA public TO {MIGRATION_ROLE}"
            )
            await connection.execute(f"GRANT USAGE ON SCHEMA public TO {WEB_ROLE}")
    finally:
        await connection.close()


async def grant_web_runtime_privileges(migration_url: str) -> None:
    """Refresh runtime grants after Alembic creates or changes database objects."""

    connection = await asyncpg.connect(asyncpg_dsn(migration_url))
    try:
        async with connection.transaction():
            await connection.execute(f"GRANT USAGE ON SCHEMA public TO {WEB_ROLE}")
            await connection.execute(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public "
                f"TO {WEB_ROLE}"
            )
            await connection.execute(
                "GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public "
                f"TO {WEB_ROLE}"
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


def run_predeploy(source: Mapping[str, str]) -> None:
    owner_url = _required(source, "RENDER_DATABASE_URL")
    migration_password = _required(source, "MIGRATION_DATABASE_PASSWORD")
    web_password = _required(source, "WEB_DATABASE_PASSWORD")
    migration_url = role_database_url(owner_url, MIGRATION_ROLE, migration_password)

    asyncio.run(ensure_database_roles(owner_url, migration_password, web_password))

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
            "publication=0",
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


def run_api(source: Mapping[str, str]) -> None:
    os.execvpe("opennosh-web", ["opennosh-web"], api_environment(source))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("api", "predeploy"))
    arguments = parser.parse_args()
    if arguments.mode == "predeploy":
        run_predeploy(os.environ)
    else:
        run_api(os.environ)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
