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
    PUBLICATION_COLUMN_PRIVILEGES,
    PUBLICATION_ROLE,
    PUBLICATION_SEQUENCES,
    PUBLICATION_TABLE_PRIVILEGES,
    WEB_ROLE,
    _quoted,
    api_environment,
    asyncpg_dsn,
    ensure_database_roles,
    evidence_environment,
    grant_publication_runtime_privileges,
    grant_web_runtime_privileges,
    main,
    predeploy_environment,
    publication_environment,
    role_database_url,
    run_api,
    run_predeploy,
    run_publication,
    run_publication_readiness,
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


def _claims_environment() -> dict[str, str]:
    return {
        "APP_ENVIRONMENT": "production",
        "RENDER_DATABASE_URL": _database_url(),
        "PUBLICATION_DATABASE_PASSWORD": "publication-secret",
        "PUBLICATION_CLAIMS_ENABLED": "true",
        "LATEST_REFRESH_ENABLED": "true",
        "PUBLICATION_ACTIVATION_IDS": "00000000-0000-4000-8000-000000000001",
        "GITHUB_FORGE_REPOSITORY_ID": "123",
        "GITHUB_FORGE_APP_ID": "456",
        "GITHUB_FORGE_INSTALLATION_ID": "789",
        "GITHUB_FORGE_PRIVATE_KEY": "forge-private",
        "GITHUB_ATTESTER_APP_ID": "654",
        "GITHUB_ATTESTER_INSTALLATION_ID": "987",
        "GITHUB_ATTESTER_PRIVATE_KEY": "attester-private",
        "ONLINE_RECEIPT_SIGNING_KEY_ID": "receipt-online",
        "ONLINE_RECEIPT_SIGNING_KEY": "receipt-private",
        "PUBLICATION_ARTIFACT_BUCKET": "opennosh-public-commons",
    }


class FakeTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_arguments: object) -> None:
        return None


class FakeConnection:
    def __init__(
        self,
        *,
        existing_roles: set[str] | None = None,
        unbounded_roles: set[str] | None = None,
    ) -> None:
        self.existing_roles = existing_roles or set()
        self.unbounded_roles = unbounded_roles or set()
        self.bounded_checks: list[str] = []
        self.executed: list[str] = []
        self.closed = False

    def transaction(self) -> FakeTransaction:
        return FakeTransaction()

    async def fetchval(self, query: str, *arguments: object) -> object:
        if "quote_ident" in query:
            return f'"{arguments[0]}"'
        if "quote_literal" in query:
            return "'" + str(arguments[0]).replace("'", "''") + "'"
        if "SELECT NOT" in query and "pg_roles" in query:
            role = str(arguments[0])
            self.bounded_checks.append(role)
            return role not in self.unbounded_roles
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
    publication = _resource(services, "opennosh-publication")
    web = _resource(services, "opennosh-web")
    assert {service["name"] for service in services} == {
        "opennosh-api",
        "opennosh-publication",
        "opennosh-web",
    }
    assert api["type"] == "pserv"
    assert publication["type"] == "worker"
    assert web["type"] == "web"
    assert api["plan"] == publication["plan"] == web["plan"] == "starter"
    assert api["region"] == publication["region"] == web["region"] == "ohio"
    assert publication["autoDeployTrigger"] == "checksPass"
    assert publication["dockerCommand"] == "python deploy/render_runtime.py publication"
    assert "healthCheckPath" not in publication
    assert web["domains"] == ["opennosh.org"]
    assert web["healthCheckPath"] == "/healthz"
    assert api["disk"] == {
        "name": "opennosh-public-artifact-state",
        "mountPath": "/var/lib/opennosh/public-artifacts",
        "sizeGB": 1,
    }
    assert blueprint["previews"] == {"generation": "off"}


def test_disk_backed_api_uses_render_supported_shutdown_settings() -> None:
    services = _blueprint()["services"]
    api = next(service for service in services if service["name"] == "opennosh-api")

    assert "disk" in api
    assert "maxShutdownDelaySeconds" not in api


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
        "PUBLICATION_DATABASE_PASSWORD",
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
    assert web_variables["PUBLIC_ARTIFACT_READS_ENABLED"] == {
        "key": "PUBLIC_ARTIFACT_READS_ENABLED",
        "value": "false",
    }
    assert api_variables["PUBLIC_ARTIFACT_CHECKPOINT_PATH"] == {
        "key": "PUBLIC_ARTIFACT_CHECKPOINT_PATH",
        "value": "/var/lib/opennosh/public-artifacts/checkpoint/latest-v1.json",
    }
    assert api_variables["PUBLIC_ARTIFACT_CACHE_DIRECTORY"] == {
        "key": "PUBLIC_ARTIFACT_CACHE_DIRECTORY",
        "value": "/var/lib/opennosh/public-artifacts/cache",
    }
    assert "healthCheckPath" not in api
    assert api["preDeployCommand"] == "python deploy/render_runtime.py predeploy"


def test_render_blueprint_links_claim_bootstrap_and_refresh_credentials_to_worker() -> None:
    services = _blueprint()["services"]
    assert isinstance(services, list)
    publication = _resource(services, "opennosh-publication")
    entries = publication["envVars"]
    groups = {entry["fromGroup"] for entry in entries if "fromGroup" in entry}
    variables = {entry["key"]: entry for entry in entries if "key" in entry}

    assert groups == {
        "opennosh-online-manifest-signer",
        "opennosh-r2-writer",
    }
    assert variables["PUBLICATION_CLAIMS_ENABLED"]["value"] == "false"
    assert variables["PUBLICATION_PREACTIVATION_SMOKE_ENABLED"]["value"] == "false"
    assert variables["LATEST_REFRESH_ENABLED"]["value"] == "true"
    assert variables["RENDER_DATABASE_URL"]["fromDatabase"] == {
        "name": "opennosh-db",
        "property": "connectionString",
    }
    assert variables["PUBLICATION_DATABASE_PASSWORD"]["fromService"] == {
        "type": "pserv",
        "name": "opennosh-api",
        "envVarKey": "PUBLICATION_DATABASE_PASSWORD",
    }
    assert variables["PUBLIC_ARTIFACT_BASE_URL"]["value"] == (
        "https://commons-artifacts.opennosh.org"
    )
    assert variables["PUBLIC_ARTIFACT_TIMEOUT_SECONDS"]["value"] == "2"
    for excluded in (
        "PUBLICATION_DATABASE_URL",
        "TRUSTED_WEB_PROXY_TOKEN",
        "FOOD_SEARCH_CURSOR_SECRET",
    ):
        assert excluded not in variables


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
            "PUBLICATION_DATABASE_URL": "postgresql+asyncpg://sibling:secret@db/opennosh",
            "ADMINISTRATION_DATABASE_URL": "postgresql+asyncpg://admin:secret@db/opennosh",
            "GITHUB_FORGE_PRIVATE_KEY": "forge-private",
            "GITHUB_ATTESTER_PRIVATE_KEY": "attester-private",
            "ONLINE_RECEIPT_SIGNING_KEY": "receipt-private",
            "ONLINE_MANIFEST_SIGNING_KEY": "manifest-private",
            "R2_SECRET_ACCESS_KEY": "r2-private",
        }
    )
    assert environment["APP_ENVIRONMENT"] == "production"
    assert environment["PROCESS_ROLE"] == "web"
    assert make_url(environment["WEB_DATABASE_URL"]).username == WEB_ROLE
    assert environment["FOOD_SEARCH_CURSOR_SIGNING_KEYS"] == "render-v1:cursor-secret"
    for removed in (
        "RENDER_DATABASE_URL",
        "WEB_DATABASE_PASSWORD",
        "MIGRATION_DATABASE_PASSWORD",
        "PUBLICATION_DATABASE_PASSWORD",
        "FOOD_SEARCH_CURSOR_SECRET",
        "PUBLICATION_DATABASE_URL",
        "ADMINISTRATION_DATABASE_URL",
        "GITHUB_FORGE_PRIVATE_KEY",
        "GITHUB_ATTESTER_PRIVATE_KEY",
        "ONLINE_RECEIPT_SIGNING_KEY",
        "ONLINE_MANIFEST_SIGNING_KEY",
        "R2_SECRET_ACCESS_KEY",
    ):
        assert removed not in environment


def _evidence_environment_source() -> dict[str, str]:
    source = {
        "APP_ENVIRONMENT": "production",
        "RENDER_DATABASE_URL": _database_url(),
        "EVIDENCE_DATABASE_PASSWORD": "evidence-database-secret",
        "EVIDENCE_UPLOADS_ENABLED": "false",
        "EVIDENCE_VERIFYING_KEYS": "{}",
    }
    for purpose in ("QUARANTINE", "SANITIZED", "IMMUTABLE"):
        source[f"EVIDENCE_{purpose}_ENDPOINT"] = "https://account.r2.cloudflarestorage.com"
        source[f"EVIDENCE_{purpose}_REGION"] = "auto"
        source[f"EVIDENCE_{purpose}_BUCKET"] = f"opennosh-evidence-{purpose.lower()}"
        source[f"EVIDENCE_{purpose}_ACCESS_KEY_ID"] = f"{purpose.lower()}-access"
        source[f"EVIDENCE_{purpose}_SECRET_ACCESS_KEY"] = f"{purpose.lower()}-secret"
    return source


def test_api_environment_retains_only_quarantine_upload_authority() -> None:
    source = _evidence_environment_source() | {
        "WEB_DATABASE_PASSWORD": "web-secret",
        "FOOD_SEARCH_CURSOR_SECRET": "cursor-secret",
    }
    environment = api_environment(source)

    assert environment["EVIDENCE_QUARANTINE_BUCKET"] == "opennosh-evidence-quarantine"
    assert environment["EVIDENCE_QUARANTINE_SECRET_ACCESS_KEY"] == "quarantine-secret"
    for purpose in ("SANITIZED", "IMMUTABLE"):
        assert f"EVIDENCE_{purpose}_BUCKET" not in environment
        assert f"EVIDENCE_{purpose}_ACCESS_KEY_ID" not in environment
        assert f"EVIDENCE_{purpose}_SECRET_ACCESS_KEY" not in environment
    assert "EVIDENCE_DATABASE_PASSWORD" not in environment
    assert "EVIDENCE_VERIFYING_KEYS" not in environment


def test_evidence_environment_retains_only_evidence_worker_authority() -> None:
    source = _evidence_environment_source() | {
        "WEB_DATABASE_PASSWORD": "web-secret",
        "PUBLICATION_DATABASE_PASSWORD": "publication-secret",
        "GITHUB_FORGE_PRIVATE_KEY": "publication-secret",
        "UNRELATED_SECRET": "unrelated-secret",
    }
    environment = evidence_environment(source)
    assert environment["EVIDENCE_UPLOADS_ENABLED"] == "false"

    assert environment["PROCESS_ROLE"] == "evidence"
    assert make_url(environment["EVIDENCE_DATABASE_URL"]).username == "opennosh_evidence"
    assert environment["EVIDENCE_VERIFYING_KEYS"] == "{}"
    for purpose in ("QUARANTINE", "SANITIZED", "IMMUTABLE"):
        assert environment[f"EVIDENCE_{purpose}_BUCKET"] == (f"opennosh-evidence-{purpose.lower()}")
    for excluded in (
        "RENDER_DATABASE_URL",
        "EVIDENCE_DATABASE_PASSWORD",
        "WEB_DATABASE_PASSWORD",
        "PUBLICATION_DATABASE_PASSWORD",
        "GITHUB_FORGE_PRIVATE_KEY",
        "UNRELATED_SECRET",
    ):
        assert excluded not in environment


def test_publication_and_predeploy_environments_strip_all_evidence_authority() -> None:
    source = (
        _evidence_environment_source()
        | _claims_environment()
        | {
            "WEB_DATABASE_PASSWORD": "web-secret",
            "MIGRATION_DATABASE_PASSWORD": "migration-secret",
            "FOOD_SEARCH_CURSOR_SECRET": "cursor-secret",
        }
    )

    publication = publication_environment(source)
    predeploy = predeploy_environment(source)

    assert not any(key.startswith("EVIDENCE_") for key in publication)
    assert predeploy["EVIDENCE_UPLOADS_ENABLED"] == "false"
    assert not any(
        key.startswith("EVIDENCE_") and key != "EVIDENCE_UPLOADS_ENABLED" for key in predeploy
    )


def test_render_combined_environment_uses_only_the_worker_database_identity() -> None:
    source = _claims_environment()
    source.update(
        {
            "WEB_DATABASE_PASSWORD": "web-secret",
            "MIGRATION_DATABASE_PASSWORD": "migration-secret",
            "FOOD_SEARCH_CURSOR_SECRET": "cursor-secret",
            "PATH": "/usr/local/bin:/usr/bin",
            "TRUSTED_WEB_PROXY_TOKEN": "web-only-secret",
            "UNRELATED_GENERATED_SECRET": "must-not-survive",
            "WEB_DATABASE_URL": "postgresql+asyncpg://sibling:secret@db/opennosh",
            "MIGRATION_DATABASE_URL": "postgresql+asyncpg://migration:secret@db/opennosh",
        }
    )
    environment = publication_environment(source)

    assert environment["APP_ENVIRONMENT"] == "production"
    assert environment["PROCESS_ROLE"] == "publication"
    assert environment["PUBLICATION_CLAIMS_ENABLED"] == "true"
    assert environment.get("PUBLICATION_CONTINUOUS_CLAIMS_ENABLED", "false") == "false"
    assert environment["LATEST_REFRESH_ENABLED"] == "true"
    assert environment["PUBLICATION_ACTIVATION_IDS"] == ("00000000-0000-4000-8000-000000000001")
    assert environment["PATH"] == "/usr/local/bin:/usr/bin"
    assert make_url(environment["PUBLICATION_DATABASE_URL"]).username == PUBLICATION_ROLE
    assert environment["GITHUB_FORGE_PRIVATE_KEY"] == "forge-private"
    assert environment["GITHUB_ATTESTER_PRIVATE_KEY"] == "attester-private"
    assert environment["ONLINE_RECEIPT_SIGNING_KEY"] == "receipt-private"
    assert "owner-secret" not in repr(environment)
    for removed in (
        "RENDER_DATABASE_URL",
        "WEB_DATABASE_PASSWORD",
        "MIGRATION_DATABASE_PASSWORD",
        "PUBLICATION_DATABASE_PASSWORD",
        "FOOD_SEARCH_CURSOR_SECRET",
        "WEB_DATABASE_URL",
        "MIGRATION_DATABASE_URL",
        "TRUSTED_WEB_PROXY_TOKEN",
        "UNRELATED_GENERATED_SECRET",
    ):
        assert removed not in environment


@pytest.mark.parametrize(
    "key",
    [
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
    ],
)
def test_render_claims_fail_closed_without_each_isolated_credential(key: str) -> None:
    source = _claims_environment()
    source.pop(key)
    with pytest.raises(ValueError, match=key):
        publication_environment(source)


def test_render_refresh_environment_has_no_database_or_sibling_credentials() -> None:
    environment = publication_environment(
        {
            "APP_ENVIRONMENT": "production",
            "PUBLICATION_CLAIMS_ENABLED": "false",
            "LATEST_REFRESH_ENABLED": "true",
            "PUBLIC_ARTIFACT_BASE_URL": "https://commons-artifacts.opennosh.org",
            "ONLINE_MANIFEST_SIGNING_KEY_ID": "manifest-online",
            "ONLINE_MANIFEST_SIGNING_KEY": "signing-secret",
            "R2_ACCOUNT_ID": "a" * 32,
            "R2_BUCKET": "opennosh-public-commons",
            "R2_ACCESS_KEY_ID": "access-key",
            "R2_SECRET_ACCESS_KEY": "r2-secret",
            "RENDER_DATABASE_URL": _database_url(),
            "PUBLICATION_DATABASE_PASSWORD": "publication-secret",
            "TRUSTED_WEB_PROXY_TOKEN": "web-only-secret",
        }
    )

    assert environment["PROCESS_ROLE"] == "publication"
    assert environment["LATEST_REFRESH_ENABLED"] == "true"
    assert environment["ONLINE_MANIFEST_SIGNING_KEY"] == "signing-secret"
    assert environment["R2_SECRET_ACCESS_KEY"] == "r2-secret"
    for excluded in (
        "RENDER_DATABASE_URL",
        "PUBLICATION_DATABASE_PASSWORD",
        "PUBLICATION_DATABASE_URL",
        "TRUSTED_WEB_PROXY_TOKEN",
    ):
        assert excluded not in environment


def test_render_readiness_uses_publication_role_without_enabling_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _claims_environment()
    source["PUBLICATION_CLAIMS_ENABLED"] = "false"
    source.pop("PUBLICATION_ACTIVATION_IDS")
    captured: dict[str, object] = {}

    def run(command: list[str], **options: object) -> None:
        captured["command"] = command
        captured["options"] = options

    monkeypatch.setattr("deploy.render_runtime.subprocess.run", run)

    run_publication_readiness(source)

    assert captured["command"] == [
        "opennosh",
        "commons",
        "production-claims-readiness",
        "--json",
    ]
    options = captured["options"]
    assert isinstance(options, dict)
    environment = options["env"]
    assert isinstance(environment, dict)
    assert environment["PUBLICATION_CLAIMS_ENABLED"] == "false"
    assert make_url(environment["PUBLICATION_DATABASE_URL"]).username == PUBLICATION_ROLE
    assert "RENDER_DATABASE_URL" not in environment
    assert options["check"] is True


def test_render_preactivation_smoke_keeps_live_adapters_but_no_claim_identity() -> None:
    source = _claims_environment()
    source.update(
        {
            "PUBLICATION_CLAIMS_ENABLED": "false",
            "PUBLICATION_PREACTIVATION_SMOKE_ENABLED": "true",
            "ONLINE_MANIFEST_SIGNING_KEY_ID": "manifest-online",
            "ONLINE_MANIFEST_SIGNING_KEY": "manifest-private",
            "R2_ACCOUNT_ID": "a" * 32,
            "R2_BUCKET": "opennosh-public-commons",
            "R2_ACCESS_KEY_ID": "access-key",
            "R2_SECRET_ACCESS_KEY": "r2-secret",
        }
    )
    source.pop("PUBLICATION_ACTIVATION_IDS")

    environment = publication_environment(source)

    assert environment["PUBLICATION_PREACTIVATION_SMOKE_ENABLED"] == "true"
    assert environment["PUBLICATION_CLAIMS_ENABLED"] == "false"
    assert environment["GITHUB_FORGE_PRIVATE_KEY"] == "forge-private"
    assert environment["GITHUB_ATTESTER_PRIVATE_KEY"] == "attester-private"
    assert environment["ONLINE_RECEIPT_SIGNING_KEY"] == "receipt-private"
    for excluded in (
        "PUBLICATION_ACTIVATION_IDS",
        "RENDER_DATABASE_URL",
        "PUBLICATION_DATABASE_PASSWORD",
        "PUBLICATION_DATABASE_URL",
    ):
        assert excluded not in environment


@pytest.mark.parametrize(
    "activation_id",
    [None, "not-a-uuid", "AAAAAAAA-0000-4000-8000-000000000001"],
)
def test_render_claims_fail_closed_without_one_canonical_activation_id(
    activation_id: str | None,
) -> None:
    source = {
        "PUBLICATION_CLAIMS_ENABLED": "true",
        "LATEST_REFRESH_ENABLED": "true",
        "RENDER_DATABASE_URL": _database_url(),
        "PUBLICATION_DATABASE_PASSWORD": "publication-secret",
    }
    if activation_id is not None:
        source["PUBLICATION_ACTIVATION_IDS"] = activation_id
    with pytest.raises(ValueError, match="PUBLICATION_ACTIVATION_IDS"):
        publication_environment(source)


def test_render_continuous_claims_require_no_activation_id() -> None:
    source = _claims_environment()
    source["PUBLICATION_CONTINUOUS_CLAIMS_ENABLED"] = "true"
    source.pop("PUBLICATION_ACTIVATION_IDS")

    environment = publication_environment(source)

    assert environment["PUBLICATION_CONTINUOUS_CLAIMS_ENABLED"] == "true"
    assert "PUBLICATION_ACTIVATION_IDS" not in environment
    assert make_url(environment["PUBLICATION_DATABASE_URL"]).username == PUBLICATION_ROLE


def test_render_continuous_claims_reject_activation_id() -> None:
    source = _claims_environment()
    source["PUBLICATION_CONTINUOUS_CLAIMS_ENABLED"] = "true"

    with pytest.raises(ValueError, match="require PUBLICATION_ACTIVATION_IDS absent"):
        publication_environment(source)


def test_render_continuous_claims_require_master_switch() -> None:
    with pytest.raises(ValueError, match="require claims to be enabled"):
        publication_environment(
            {
                "PUBLICATION_CLAIMS_ENABLED": "false",
                "PUBLICATION_CONTINUOUS_CLAIMS_ENABLED": "true",
                "LATEST_REFRESH_ENABLED": "true",
            }
        )


@pytest.mark.parametrize("value", ["0", "-1", "not-an-integer"])
def test_render_claim_concurrency_must_be_positive(value: str) -> None:
    with pytest.raises(ValueError, match="PUBLICATION_CLAIM_CONCURRENCY"):
        publication_environment(
            {
                "PUBLICATION_CLAIMS_ENABLED": "false",
                "PUBLICATION_CLAIM_CONCURRENCY": value,
                "LATEST_REFRESH_ENABLED": "true",
            }
        )


def test_render_claims_cannot_disable_latest_refresh() -> None:
    with pytest.raises(ValueError, match="latest refresh"):
        publication_environment(
            {
                "PUBLICATION_CLAIMS_ENABLED": "true",
                "LATEST_REFRESH_ENABLED": "false",
            }
        )


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
    alter_statements = [statement for statement in connection.executed if "ALTER ROLE" in statement]
    assert len(create_statements) == (0 if existing_roles else 2)
    assert len(alter_statements) == 2
    assert all("SUPERUSER" not in statement for statement in alter_statements)
    assert all("REPLICATION" not in statement for statement in alter_statements)
    assert all("BYPASSRLS" not in statement for statement in alter_statements)
    assert all("NOCREATEDB NOCREATEROLE NOINHERIT" in statement for statement in alter_statements)
    assert set(connection.bounded_checks) == {MIGRATION_ROLE, WEB_ROLE}
    assert any("GRANT CREATE ON DATABASE" in statement for statement in connection.executed)
    assert not any("ALTER SCHEMA public OWNER" in statement for statement in connection.executed)
    assert any(
        "GRANT USAGE, CREATE ON SCHEMA public" in statement for statement in connection.executed
    )
    assert connection.closed is True


@pytest.mark.asyncio
async def test_render_publication_role_is_optional_bounded_and_rotated() -> None:
    connection = FakeConnection()

    async def connect(_dsn: str) -> FakeConnection:
        return connection

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("deploy.render_runtime.asyncpg.connect", connect)
        await ensure_database_roles(
            _database_url(),
            "migration-secret",
            "web-secret",
            "publication-secret",
        )

    create_statements = [
        statement for statement in connection.executed if "CREATE ROLE" in statement
    ]
    alter_statements = [statement for statement in connection.executed if "ALTER ROLE" in statement]
    assert len(create_statements) == 3
    assert len(alter_statements) == 3
    publication_alter = next(
        statement for statement in alter_statements if PUBLICATION_ROLE in statement
    )
    for denied_capability in ("NOCREATEDB", "NOCREATEROLE", "NOINHERIT"):
        assert denied_capability in publication_alter
    for superuser_only_capability in ("SUPERUSER", "REPLICATION", "BYPASSRLS"):
        assert superuser_only_capability not in publication_alter
    assert set(connection.bounded_checks) == {MIGRATION_ROLE, WEB_ROLE, PUBLICATION_ROLE}
    assert any(
        f"GRANT USAGE ON SCHEMA public TO {PUBLICATION_ROLE}" in statement
        for statement in connection.executed
    )
    assert not any(
        "GRANT CREATE ON DATABASE" in statement and PUBLICATION_ROLE in statement
        for statement in connection.executed
    )


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
async def test_render_role_bootstrap_fails_closed_for_an_unbounded_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection(unbounded_roles={WEB_ROLE})

    async def connect(_dsn: str) -> FakeConnection:
        return connection

    monkeypatch.setattr("deploy.render_runtime.asyncpg.connect", connect)
    with pytest.raises(RuntimeError, match=f"Database role {WEB_ROLE} is not bounded"):
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
async def test_publication_runtime_grants_only_reviewed_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection()

    async def connect(_dsn: str) -> FakeConnection:
        return connection

    monkeypatch.setattr("deploy.render_runtime.asyncpg.connect", connect)
    await grant_publication_runtime_privileges(
        _database_url(
            drivername="postgresql+asyncpg",
            username=MIGRATION_ROLE,
            password="secret",
        )
    )

    assert not any("ALL TABLES" in statement for statement in connection.executed)
    for table, privileges in PUBLICATION_TABLE_PRIVILEGES.items():
        assert f"GRANT {privileges} ON TABLE {table} TO {PUBLICATION_ROLE}" in connection.executed
    for table, grants in PUBLICATION_COLUMN_PRIVILEGES.items():
        for privilege, columns in grants.items():
            column_list = ", ".join(columns)
            assert (
                f"GRANT {privilege} ({column_list}) ON TABLE {table} TO {PUBLICATION_ROLE}"
                in connection.executed
            )
    for sequence in PUBLICATION_SEQUENCES:
        assert (
            f"GRANT USAGE, SELECT, UPDATE ON SEQUENCE {sequence} TO {PUBLICATION_ROLE}"
            in connection.executed
        )
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

    def capture_run(command: list[str], *, check: bool, env: dict[str, str]) -> None:
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
        ["opennosh-migrate"],
        ["opennosh", "foods", "load", "/app/packs", "--json"],
    ]
    capacity_environment = child_calls[0][1]
    migration_environment = child_calls[1][1]
    food_load_environment = child_calls[2][1]
    assert make_url(capacity_environment["DATABASE_CAPACITY_URL"]).username == MIGRATION_ROLE
    assert "DATABASE_CAPACITY_URL" not in migration_environment
    assert make_url(migration_environment["MIGRATION_DATABASE_URL"]).username == MIGRATION_ROLE
    assert make_url(food_load_environment["ADMINISTRATION_DATABASE_URL"]).username == MIGRATION_ROLE
    for environment in (capacity_environment, migration_environment, food_load_environment):
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

    def fail_selected_child(command: list[str], *, check: bool, env: dict[str, str]) -> None:
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


def test_render_publication_execs_with_only_the_worker_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def capture_exec(file: str, arguments: list[str], environment: dict[str, str]) -> None:
        captured.update(file=file, arguments=arguments, environment=environment)
        raise RuntimeError("exec intercepted")

    monkeypatch.setattr("deploy.render_runtime.os.execvpe", capture_exec)
    with pytest.raises(RuntimeError, match="exec intercepted"):
        run_publication(
            {
                "APP_ENVIRONMENT": "production",
                "PUBLICATION_CLAIMS_ENABLED": "false",
                "LATEST_REFRESH_ENABLED": "true",
                "RENDER_DATABASE_URL": _database_url(),
                "WEB_DATABASE_PASSWORD": "web-secret",
                "MIGRATION_DATABASE_PASSWORD": "migration-secret",
                "PUBLICATION_DATABASE_PASSWORD": "publication-secret",
                "FOOD_SEARCH_CURSOR_SECRET": "cursor-secret",
            }
        )

    assert captured["file"] == "opennosh-publication-worker"
    assert captured["arguments"] == ["opennosh-publication-worker"]
    environment = captured["environment"]
    assert environment["PROCESS_ROLE"] == "publication"
    assert environment["PUBLICATION_CLAIMS_ENABLED"] == "false"
    assert environment["LATEST_REFRESH_ENABLED"] == "true"
    assert "PUBLICATION_DATABASE_URL" not in environment
    assert "owner-secret" not in repr(environment)


@pytest.mark.parametrize("mode", ["api", "predeploy", "publication"])
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
    monkeypatch.setattr(
        "deploy.render_runtime.run_publication",
        lambda _environment: calls.append("publication"),
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
    assert "COPY --from=builder --chown=nextjs:nodejs /app/public ./public" in web_dockerfile
    assert "\nUSER opennosh\n" in api_dockerfile
    assert "\nUSER nextjs\n" in web_dockerfile
    assert 'export API_URL="http://${API_HOSTPORT}"' in web_start
