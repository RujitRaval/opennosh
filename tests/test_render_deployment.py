from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy.engine import URL, make_url

from deploy.render_runtime import (
    MIGRATION_ROLE,
    WEB_ROLE,
    _quoted,
    api_environment,
    asyncpg_dsn,
    ensure_database_roles,
    grant_web_runtime_privileges,
    main,
    role_database_url,
    run_api,
    run_predeploy,
)

ROOT = Path(__file__).resolve().parents[1]


def _database_url(
    *,
    drivername: str = "postgresql",
    username: str = "owner",
    password: str | None = "owner-secret",
    database: str | None = "opennosh",
) -> str:
    return URL.create(
        drivername,
        username=username,
        password=password,
        host="db.internal",
        port=5432,
        database=database,
    ).render_as_string(hide_password=False)


class FakeTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_arguments: object) -> None:
        return None


class FakeConnection:
    def __init__(self, *, existing_roles: set[str] | None = None) -> None:
        self.existing_roles = existing_roles or set()
        self.executed: list[str] = []
        self.closed = False

    def transaction(self) -> FakeTransaction:
        return FakeTransaction()

    async def fetchval(self, query: str, *arguments: object) -> object:
        if "quote_ident" in query:
            return f'"{arguments[0]}"'
        if "quote_literal" in query:
            return "'" + str(arguments[0]).replace("'", "''") + "'"
        if "pg_roles" in query:
            return arguments[0] in self.existing_roles
        raise AssertionError(f"Unexpected query: {query}")

    async def execute(self, query: str) -> None:
        self.executed.append(query)

    async def close(self) -> None:
        self.closed = True


def _blueprint() -> dict[str, object]:
    loaded = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _resource(resources: list[dict[str, object]], name: str) -> dict[str, object]:
    return next(resource for resource in resources if resource["name"] == name)


def test_render_blueprint_preserves_the_bounded_launch_topology() -> None:
    blueprint = _blueprint()
    databases = blueprint["databases"]
    services = blueprint["services"]
    assert isinstance(databases, list)
    assert isinstance(services, list)

    database = _resource(databases, "opennosh-db")
    assert database == {
        "name": "opennosh-db",
        "databaseName": "opennosh",
        "user": "opennosh_admin",
        "plan": "basic-256mb",
        "diskSizeGB": 5,
        "postgresMajorVersion": "16",
        "region": "ohio",
        "ipAllowList": [],
        "storageAutoscalingEnabled": False,
    }

    api = _resource(services, "opennosh-api")
    web = _resource(services, "opennosh-web")
    assert {service["name"] for service in services} == {"opennosh-api", "opennosh-web"}
    assert api["type"] == "pserv"
    assert web["type"] == "web"
    assert api["plan"] == web["plan"] == "starter"
    assert api["region"] == web["region"] == "ohio"
    assert api["autoDeployTrigger"] == web["autoDeployTrigger"] == "checksPass"
    assert web["domains"] == ["opennosh.org"]
    assert web["healthCheckPath"] == "/api/v1/healthz"
    assert blueprint["previews"] == {"generation": "off"}


def test_render_blueprint_generates_secrets_and_keeps_the_api_private() -> None:
    services = _blueprint()["services"]
    assert isinstance(services, list)
    api = _resource(services, "opennosh-api")
    web = _resource(services, "opennosh-web")
    api_variables = {item["key"]: item for item in api["envVars"]}
    web_variables = {item["key"]: item for item in web["envVars"]}

    for key in (
        "WEB_DATABASE_PASSWORD",
        "MIGRATION_DATABASE_PASSWORD",
        "TRUSTED_WEB_PROXY_TOKEN",
        "FOOD_SEARCH_CURSOR_SECRET",
    ):
        assert api_variables[key] == {"key": key, "generateValue": True}
    assert api_variables["RENDER_DATABASE_URL"]["fromDatabase"] == {
        "name": "opennosh-db",
        "property": "connectionString",
    }
    assert web_variables["WEB_PROXY_TOKEN"]["fromService"] == {
        "type": "pserv",
        "name": "opennosh-api",
        "envVarKey": "TRUSTED_WEB_PROXY_TOKEN",
    }
    assert web_variables["API_HOSTPORT"]["fromService"] == {
        "type": "pserv",
        "name": "opennosh-api",
        "property": "hostport",
    }
    assert "healthCheckPath" not in api
    assert api["preDeployCommand"] == "python deploy/render_runtime.py predeploy"


def test_render_database_urls_encode_role_credentials_and_strip_owner_secrets() -> None:
    owner_url = _database_url()
    role_url = role_database_url(owner_url, WEB_ROLE, "web p@ss/word")
    parsed = make_url(role_url)
    assert parsed.drivername == "postgresql+asyncpg"
    assert parsed.username == WEB_ROLE
    assert parsed.password == "web p@ss/word"
    assert asyncpg_dsn(role_url).startswith("postgresql://opennosh_web:")

    environment = api_environment(
        {
            "APP_ENVIRONMENT": "production",
            "RENDER_DATABASE_URL": owner_url,
            "WEB_DATABASE_PASSWORD": "web p@ss/word",
            "MIGRATION_DATABASE_PASSWORD": "migration-secret",
            "FOOD_SEARCH_CURSOR_SECRET": "cursor-secret",
        }
    )
    assert environment["APP_ENVIRONMENT"] == "production"
    assert make_url(environment["WEB_DATABASE_URL"]).username == WEB_ROLE
    assert environment["FOOD_SEARCH_CURSOR_SIGNING_KEYS"] == "render-v1:cursor-secret"
    for removed in (
        "RENDER_DATABASE_URL",
        "WEB_DATABASE_PASSWORD",
        "MIGRATION_DATABASE_PASSWORD",
        "FOOD_SEARCH_CURSOR_SECRET",
    ):
        assert removed not in environment


@pytest.mark.parametrize(
    "source_url",
    ["sqlite:///opennosh", _database_url(password=None, database=None)],
)
def test_render_database_url_helpers_reject_invalid_sources(source_url: str) -> None:
    with pytest.raises(ValueError):
        role_database_url(source_url, WEB_ROLE, "web-secret")
    with pytest.raises(ValueError):
        asyncpg_dsn(source_url)


@pytest.mark.parametrize(
    "key",
    ["RENDER_DATABASE_URL", "WEB_DATABASE_PASSWORD", "FOOD_SEARCH_CURSOR_SECRET"],
)
def test_render_api_environment_fails_closed_when_a_required_secret_is_missing(key: str) -> None:
    environment = {
        "RENDER_DATABASE_URL": _database_url(password="secret"),
        "WEB_DATABASE_PASSWORD": "web-secret",
        "FOOD_SEARCH_CURSOR_SECRET": "cursor-secret",
    }
    environment.pop(key)
    with pytest.raises(ValueError, match=f"{key} is required"):
        api_environment(environment)


@pytest.mark.parametrize(
    "key",
    ["RENDER_DATABASE_URL", "WEB_DATABASE_PASSWORD", "FOOD_SEARCH_CURSOR_SECRET"],
)
def test_render_api_environment_rejects_whitespace_only_secrets(key: str) -> None:
    environment = {
        "RENDER_DATABASE_URL": _database_url(password="secret"),
        "WEB_DATABASE_PASSWORD": "web-secret",
        "FOOD_SEARCH_CURSOR_SECRET": "cursor-secret",
    }
    environment[key] = "   "
    with pytest.raises(ValueError, match=f"{key} is required"):
        api_environment(environment)


@pytest.mark.asyncio
async def test_render_quoting_uses_postgresql_for_identifiers_and_literals() -> None:
    connection = FakeConnection()
    assert await _quoted(connection, "role-name", identifier=True) == '"role-name"'
    assert await _quoted(connection, "secret'value", identifier=False) == "'secret''value'"

    async def return_non_text(_query: str, *_arguments: object) -> None:
        return None

    connection.fetchval = return_non_text  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="did not return text"):
        await _quoted(connection, "role", identifier=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("existing_roles", [set(), {MIGRATION_ROLE, WEB_ROLE}])
async def test_render_role_bootstrap_is_idempotent_and_always_closes(
    monkeypatch: pytest.MonkeyPatch,
    existing_roles: set[str],
) -> None:
    connection = FakeConnection(existing_roles=existing_roles)

    async def connect(_dsn: str) -> FakeConnection:
        return connection

    monkeypatch.setattr("deploy.render_runtime.asyncpg.connect", connect)
    await ensure_database_roles(
        _database_url(),
        "migration'secret",
        "web-secret",
    )

    create_statements = [
        statement for statement in connection.executed if "CREATE ROLE" in statement
    ]
    alter_statements = [
        statement for statement in connection.executed if "ALTER ROLE" in statement
    ]
    assert len(create_statements) == (0 if existing_roles else 2)
    assert len(alter_statements) == 2
    assert all("SUPERUSER" not in statement for statement in alter_statements)
    assert all("REPLICATION" not in statement for statement in alter_statements)
    assert all("NOCREATEDB NOCREATEROLE" in statement for statement in alter_statements)
    assert any("GRANT CREATE ON DATABASE" in statement for statement in connection.executed)
    assert any("ALTER SCHEMA public OWNER" in statement for statement in connection.executed)
    assert connection.closed is True


@pytest.mark.asyncio
async def test_render_role_bootstrap_closes_after_a_database_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingConnection(FakeConnection):
        async def execute(self, query: str) -> None:
            raise RuntimeError(f"database failure: {query}")

    connection = FailingConnection()

    async def connect(_dsn: str) -> FakeConnection:
        return connection

    monkeypatch.setattr("deploy.render_runtime.asyncpg.connect", connect)
    with pytest.raises(RuntimeError, match="database failure"):
        await ensure_database_roles(
            _database_url(),
            "migration-secret",
            "web-secret",
        )
    assert connection.closed is True


@pytest.mark.asyncio
async def test_render_role_bootstrap_rejects_an_owner_url_without_a_database() -> None:
    with pytest.raises(ValueError, match="must name a database"):
        await ensure_database_roles(
            _database_url(database=None),
            "migration-secret",
            "web-secret",
        )


@pytest.mark.asyncio
async def test_render_runtime_grants_are_applied_and_connection_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection()

    async def connect(_dsn: str) -> FakeConnection:
        return connection

    monkeypatch.setattr("deploy.render_runtime.asyncpg.connect", connect)
    await grant_web_runtime_privileges(
        _database_url(
            drivername="postgresql+asyncpg",
            username=MIGRATION_ROLE,
            password="secret",
        )
    )

    assert any("ALL TABLES" in statement for statement in connection.executed)
    assert any("DEFAULT PRIVILEGES" in statement for statement in connection.executed)
    assert connection.closed is True


@pytest.mark.asyncio
async def test_render_runtime_grants_close_after_a_database_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingConnection(FakeConnection):
        async def execute(self, query: str) -> None:
            raise RuntimeError(f"grant failure: {query}")

    connection = FailingConnection()

    async def connect(_dsn: str) -> FakeConnection:
        return connection

    monkeypatch.setattr("deploy.render_runtime.asyncpg.connect", connect)
    with pytest.raises(RuntimeError, match="grant failure"):
        await grant_web_runtime_privileges(
            _database_url(
                drivername="postgresql+asyncpg",
                username=MIGRATION_ROLE,
                password="secret",
            )
        )
    assert connection.closed is True


def test_render_predeploy_does_not_pass_owner_or_raw_secrets_to_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child_calls: list[tuple[list[str], dict[str, str]]] = []

    async def noop(*_arguments: object) -> None:
        return None

    def capture_run(
        command: list[str], *, check: bool, env: dict[str, str]
    ) -> None:
        assert check is True
        child_calls.append((command, env.copy()))

    monkeypatch.setattr("deploy.render_runtime.ensure_database_roles", noop)
    monkeypatch.setattr("deploy.render_runtime.grant_web_runtime_privileges", noop)
    monkeypatch.setattr("deploy.render_runtime.subprocess.run", capture_run)

    owner_url = _database_url()
    run_predeploy(
        {
            "APP_ENVIRONMENT": "production",
            "RENDER_DATABASE_URL": owner_url,
            "WEB_DATABASE_PASSWORD": "web-secret",
            "MIGRATION_DATABASE_PASSWORD": "migration-secret",
            "FOOD_SEARCH_CURSOR_SECRET": "cursor-secret",
        }
    )

    assert [command for command, _environment in child_calls] == [
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
        ["opennosh-migrate"],
    ]
    capacity_environment = child_calls[0][1]
    migration_environment = child_calls[1][1]
    assert make_url(capacity_environment["DATABASE_CAPACITY_URL"]).username == MIGRATION_ROLE
    assert "DATABASE_CAPACITY_URL" not in migration_environment
    assert make_url(migration_environment["MIGRATION_DATABASE_URL"]).username == MIGRATION_ROLE
    for environment in (capacity_environment, migration_environment):
        assert environment["FOOD_SEARCH_CURSOR_SIGNING_KEYS"] == "render-v1:cursor-secret"
        assert "owner-secret" not in repr(environment)
        for raw_secret in (
            "RENDER_DATABASE_URL",
            "WEB_DATABASE_PASSWORD",
            "MIGRATION_DATABASE_PASSWORD",
            "FOOD_SEARCH_CURSOR_SECRET",
        ):
            assert raw_secret not in environment


def test_render_predeploy_requires_the_migration_password_before_connecting() -> None:
    with pytest.raises(ValueError, match="MIGRATION_DATABASE_PASSWORD is required"):
        run_predeploy(
            {
                "RENDER_DATABASE_URL": _database_url(password="secret"),
                "WEB_DATABASE_PASSWORD": "web-secret",
                "FOOD_SEARCH_CURSOR_SECRET": "cursor-secret",
            }
        )


@pytest.mark.parametrize(
    ("failing_command", "expected_commands"),
    [
        ("opennosh-capacity-preflight", ["opennosh-capacity-preflight"]),
        ("opennosh-migrate", ["opennosh-capacity-preflight", "opennosh-migrate"]),
    ],
)
def test_render_predeploy_stops_before_grants_when_a_child_fails(
    monkeypatch: pytest.MonkeyPatch,
    failing_command: str,
    expected_commands: list[str],
) -> None:
    commands: list[str] = []
    grant_calls: list[str] = []

    async def noop_roles(*_arguments: object) -> None:
        return None

    async def record_grants(migration_url: str) -> None:
        grant_calls.append(migration_url)

    def fail_selected_child(
        command: list[str], *, check: bool, env: dict[str, str]
    ) -> None:
        assert check is True
        assert "owner-secret" not in repr(env)
        commands.append(command[0])
        if command[0] == failing_command:
            raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr("deploy.render_runtime.ensure_database_roles", noop_roles)
    monkeypatch.setattr("deploy.render_runtime.grant_web_runtime_privileges", record_grants)
    monkeypatch.setattr("deploy.render_runtime.subprocess.run", fail_selected_child)

    with pytest.raises(subprocess.CalledProcessError):
        run_predeploy(
            {
                "APP_ENVIRONMENT": "production",
                "RENDER_DATABASE_URL": _database_url(),
                "WEB_DATABASE_PASSWORD": "web-secret",
                "MIGRATION_DATABASE_PASSWORD": "migration-secret",
                "FOOD_SEARCH_CURSOR_SECRET": "cursor-secret",
            }
        )
    assert commands == expected_commands
    assert grant_calls == []


def test_render_api_execs_with_only_the_runtime_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def capture_exec(file: str, arguments: list[str], environment: dict[str, str]) -> None:
        captured.update(file=file, arguments=arguments, environment=environment)
        raise RuntimeError("exec intercepted")

    monkeypatch.setattr("deploy.render_runtime.os.execvpe", capture_exec)
    with pytest.raises(RuntimeError, match="exec intercepted"):
        run_api(
            {
                "APP_ENVIRONMENT": "production",
                "RENDER_DATABASE_URL": _database_url(),
                "WEB_DATABASE_PASSWORD": "web-secret",
                "MIGRATION_DATABASE_PASSWORD": "migration-secret",
                "FOOD_SEARCH_CURSOR_SECRET": "cursor-secret",
            }
        )

    assert captured["file"] == "opennosh-web"
    assert captured["arguments"] == ["opennosh-web"]
    assert make_url(captured["environment"]["WEB_DATABASE_URL"]).username == WEB_ROLE
    assert "owner-secret" not in repr(captured["environment"])


@pytest.mark.parametrize("mode", ["api", "predeploy"])
def test_render_cli_dispatches_the_selected_mode(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(sys, "argv", ["render_runtime.py", mode])
    monkeypatch.setattr("deploy.render_runtime.run_api", lambda _environment: calls.append("api"))
    monkeypatch.setattr(
        "deploy.render_runtime.run_predeploy",
        lambda _environment: calls.append("predeploy"),
    )

    assert main() == 0
    assert calls == [mode]


def test_render_web_start_translates_private_hostport_and_fails_closed(
    tmp_path: Path,
) -> None:
    fake_node = tmp_path / "node"
    fake_node.write_text('#!/bin/sh\nprintf "%s" "$API_URL"\n', encoding="utf-8")
    fake_node.chmod(0o755)
    script = ROOT / "deploy/render_web_start.sh"
    environment = os.environ.copy()
    environment.update(PATH=str(tmp_path), API_HOSTPORT="opennosh-api:8000")

    started = subprocess.run(
        ["/bin/sh", str(script)],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert started.stdout == "http://opennosh-api:8000"

    environment.pop("API_HOSTPORT")
    rejected = subprocess.run(
        ["/bin/sh", str(script)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert rejected.returncode != 0
    assert "API_HOSTPORT" in rejected.stderr


def test_render_commands_are_copied_into_their_production_images() -> None:
    api_dockerfile = (ROOT / "api/Dockerfile").read_text(encoding="utf-8")
    web_dockerfile = (ROOT / "web/Dockerfile").read_text(encoding="utf-8")
    web_start = (ROOT / "deploy/render_web_start.sh").read_text(encoding="utf-8")

    assert "COPY deploy/render_runtime.py ./deploy/render_runtime.py" in api_dockerfile
    assert "COPY --chown=nextjs:nodejs deploy/render_web_start.sh" in web_dockerfile
    assert "\nUSER opennosh\n" in api_dockerfile
    assert "\nUSER nextjs\n" in web_dockerfile
    assert 'export API_URL="http://${API_HOSTPORT}"' in web_start
