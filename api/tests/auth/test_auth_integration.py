from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator

import pytest
from alembic import command
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from opennosh_api.auth.client_address import client_address
from opennosh_api.auth.rate_limit import enforce_auth_rate_limit
from opennosh_api.auth.schemas import Credentials
from opennosh_api.auth.tokens import hash_token
from opennosh_api.main import create_app
from opennosh_api.settings import Settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.tests.test_migrations import migration_config

INTEGRATION_DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL")


async def _clear_auth_data(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("TRUNCATE auth_rate_limits, auth_sessions, users CASCADE")
            )
    finally:
        await engine.dispose()


async def _stored_password_hash(database_url: str, email: str) -> str:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            value = await connection.scalar(
                text("SELECT password_hash FROM users WHERE email = :email"),
                {"email": email},
            )
            assert isinstance(value, str)
            return value
    finally:
        await engine.dispose()


async def _backdate_rate_limits(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE auth_rate_limits
                    SET window_started_at = now() - INTERVAL '2 minutes',
                        updated_at = now() - INTERVAL '2 minutes'
                    """
                )
            )
    finally:
        await engine.dispose()


async def _rate_limit_row_count(database_url: str) -> int:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            count = await connection.scalar(text("SELECT count(*) FROM auth_rate_limits"))
            assert isinstance(count, int)
            return count
    finally:
        await engine.dispose()


async def _expire_session(database_url: str, token_hash: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE auth_sessions
                    SET expires_at = now() - INTERVAL '1 minute'
                    WHERE token_hash = :token_hash
                    """
                ),
                {"token_hash": token_hash},
            )
    finally:
        await engine.dispose()


async def _concurrent_rate_limit_statuses(database_url: str) -> list[int]:
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        database_url=database_url,
        app_environment="test",
        auth_rate_limit_attempts=2,
        auth_rate_limit_window_seconds=60,
        _env_file=None,
    )

    async def attempt() -> int:
        async with session_factory() as session:
            try:
                await enforce_auth_rate_limit(
                    session,
                    scope="concurrent-test",
                    key="same-client",
                    settings=settings,
                )
            except HTTPException as error:
                return error.status_code
            return 200

    try:
        return await asyncio.gather(attempt(), attempt(), attempt())
    finally:
        await engine.dispose()


@pytest.fixture
def auth_client() -> Iterator[TestClient]:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_clear_auth_data(INTEGRATION_DATABASE_URL))
    app = create_app(
        Settings(
            database_url=INTEGRATION_DATABASE_URL,
            app_environment="test",
            auth_rate_limit_attempts=20,
            _env_file=None,
        )
    )
    with TestClient(app) as client:
        yield client


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_register_session_login_and_csrf_protected_logout(auth_client: TestClient) -> None:
    credentials = {
        "email": "Athlete@Example.test",
        "password": "correct horse battery staple",
    }

    assert auth_client.get("/api/v1/auth/session").status_code == 401
    registered = auth_client.post("/api/v1/auth/register", json=credentials)

    assert registered.status_code == 201
    assert registered.json()["user"]["email"] == "athlete@example.test"
    csrf_token = registered.json()["csrf_token"]
    assert auth_client.get("/api/v1/auth/session").status_code == 200
    assert registered.headers["cache-control"] == "no-store"
    assert "HttpOnly" in registered.headers["set-cookie"]
    session_token = auth_client.cookies.get("opennosh_session")
    assert session_token is not None

    assert auth_client.post("/api/v1/auth/logout").status_code == 403
    assert (
        auth_client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": "wrong-token"}).status_code
        == 403
    )
    auth_client.cookies.set("opennosh_csrf", "matching-but-not-stored")
    assert (
        auth_client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": "matching-but-not-stored"},
        ).status_code
        == 403
    )
    auth_client.cookies.set("opennosh_csrf", csrf_token)
    logged_out = auth_client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf_token})
    assert logged_out.status_code == 204
    cleared_cookies = logged_out.headers.get_list("set-cookie")
    assert any(
        cookie.startswith("opennosh_session=") and "Max-Age=0" in cookie
        for cookie in cleared_cookies
    )
    assert any(
        cookie.startswith("opennosh_csrf=") and "Max-Age=0" in cookie for cookie in cleared_cookies
    )
    auth_client.cookies.set("opennosh_session", session_token)
    assert auth_client.get("/api/v1/auth/session").status_code == 401
    auth_client.cookies.clear()

    bad_login = auth_client.post(
        "/api/v1/auth/login", json={**credentials, "password": "a wrong password"}
    )
    assert bad_login.status_code == 401
    logged_in = auth_client.post("/api/v1/auth/login", json=credentials)
    assert logged_in.status_code == 200
    assert logged_in.json()["user"]["email"] == "athlete@example.test"
    assert auth_client.get("/api/v1/auth/session").status_code == 200
    auth_client.cookies.set("opennosh_csrf", csrf_token)
    assert (
        auth_client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": csrf_token},
        ).status_code
        == 403
    )
    auth_client.cookies.set("opennosh_csrf", logged_in.json()["csrf_token"])
    assert (
        auth_client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": logged_in.json()["csrf_token"]},
        ).status_code
        == 204
    )

    assert INTEGRATION_DATABASE_URL is not None
    password_hash = asyncio.run(
        _stored_password_hash(INTEGRATION_DATABASE_URL, "athlete@example.test")
    )
    assert password_hash.startswith("$argon2id$")
    assert credentials["password"] not in password_hash


@pytest.mark.parametrize(
    "email",
    ["missing-at.example.test", "@example.test", "person@localhost", "person@.example.test"],
)
def test_credentials_reject_malformed_email(email: str) -> None:
    with pytest.raises(ValueError):
        Credentials(email=email, password="a sufficiently long password")


def test_credentials_enforce_password_character_and_byte_boundaries() -> None:
    assert Credentials(email="a@example.test", password="a" * 12).password == "a" * 12
    with pytest.raises(ValueError):
        Credentials(email="a@example.test", password="a" * 11)
    with pytest.raises(ValueError):
        Credentials(email="a@example.test", password="é" * 600)


def test_client_address_has_a_stable_fallback() -> None:
    request = Request({"type": "http", "headers": [], "client": None})
    assert client_address(request, Settings(_env_file=None)) == "unknown"


def test_client_address_only_trusts_authenticated_proxy_headers() -> None:
    token = "a-unique-test-proxy-token-that-is-long-enough"
    settings = Settings(trusted_web_proxy_token=token, _env_file=None)
    spoofed = Request(
        {
            "type": "http",
            "headers": [
                (b"x-forwarded-for", b"198.51.100.9"),
                (b"x-opennosh-client-address", b"198.51.100.10"),
                (b"x-opennosh-proxy-token", b"wrong-token-that-is-still-long-enough"),
            ],
            "client": ("203.0.113.5", 50_000),
        }
    )
    trusted = Request(
        {
            "type": "http",
            "headers": [
                (b"x-opennosh-client-address", b"2001:db8::10"),
                (b"x-opennosh-proxy-token", token.encode()),
            ],
            "client": ("172.20.0.4", 50_000),
        }
    )
    invalid_address = Request(
        {
            "type": "http",
            "headers": [
                (b"x-opennosh-client-address", b"not-an-address"),
                (b"x-opennosh-proxy-token", token.encode()),
            ],
            "client": ("172.20.0.4", 50_000),
        }
    )

    assert client_address(spoofed, settings) == "203.0.113.5"
    assert client_address(trusted, settings) == "2001:db8::10"
    assert client_address(invalid_address, settings) == "172.20.0.4"


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_registration_is_case_insensitively_unique(auth_client: TestClient) -> None:
    first = auth_client.post(
        "/api/v1/auth/register",
        json={"email": "same@example.test", "password": "a sufficiently long password"},
    )
    duplicate = auth_client.post(
        "/api/v1/auth/register",
        json={"email": "SAME@example.test", "password": "a sufficiently long password"},
    )

    assert first.status_code == 201
    assert duplicate.status_code == 409


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_login_is_limited_by_account_across_source_ips_and_window_resets() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_clear_auth_data(INTEGRATION_DATABASE_URL))
    app = create_app(
        Settings(
            database_url=INTEGRATION_DATABASE_URL,
            app_environment="test",
            auth_rate_limit_attempts=2,
            auth_rate_limit_window_seconds=60,
            _env_file=None,
        )
    )
    credentials = {
        "email": "missing@example.test",
        "password": "a sufficiently long password",
    }
    responses = []
    for index in range(3):
        with TestClient(app, client=(f"198.51.100.{index + 1}", 50_000)) as client:
            responses.append(client.post("/api/v1/auth/login", json=credentials))

    assert [response.status_code for response in responses] == [401, 401, 429]
    assert int(responses[-1].headers["retry-after"]) > 0

    asyncio.run(_backdate_rate_limits(INTEGRATION_DATABASE_URL))
    with TestClient(app, client=("198.51.100.4", 50_000)) as client:
        after_window = client.post("/api/v1/auth/login", json=credentials)

    assert after_window.status_code == 401


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_login_is_limited_by_source_ip_across_accounts() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_clear_auth_data(INTEGRATION_DATABASE_URL))
    app = create_app(
        Settings(
            database_url=INTEGRATION_DATABASE_URL,
            app_environment="test",
            auth_rate_limit_attempts=2,
            auth_rate_limit_window_seconds=60,
            _env_file=None,
        )
    )
    with TestClient(app, client=("198.51.100.10", 50_000)) as client:
        responses = [
            client.post(
                "/api/v1/auth/login",
                json={
                    "email": f"missing-{index}@example.test",
                    "password": "a sufficiently long password",
                },
            )
            for index in range(3)
        ]

    assert [response.status_code for response in responses] == [401, 401, 429]


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_expired_rate_limit_keys_are_deleted() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_clear_auth_data(INTEGRATION_DATABASE_URL))
    app = create_app(
        Settings(
            database_url=INTEGRATION_DATABASE_URL,
            app_environment="test",
            auth_rate_limit_attempts=5,
            auth_rate_limit_window_seconds=60,
            auth_rate_limit_retention_seconds=60,
            _env_file=None,
        )
    )
    with TestClient(app, client=("198.51.100.20", 50_000)) as client:
        first = client.post(
            "/api/v1/auth/login",
            json={
                "email": "first-missing@example.test",
                "password": "a sufficiently long password",
            },
        )
        assert first.status_code == 401
        assert asyncio.run(_rate_limit_row_count(INTEGRATION_DATABASE_URL)) == 2
        asyncio.run(_backdate_rate_limits(INTEGRATION_DATABASE_URL))
        second = client.post(
            "/api/v1/auth/login",
            json={
                "email": "second-missing@example.test",
                "password": "a sufficiently long password",
            },
        )

    assert second.status_code == 401
    assert asyncio.run(_rate_limit_row_count(INTEGRATION_DATABASE_URL)) == 2


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_registration_attempts_are_rate_limited() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_clear_auth_data(INTEGRATION_DATABASE_URL))
    app = create_app(
        Settings(
            database_url=INTEGRATION_DATABASE_URL,
            app_environment="test",
            auth_rate_limit_attempts=2,
            auth_rate_limit_window_seconds=60,
            _env_file=None,
        )
    )
    with TestClient(app) as client:
        for index in range(2):
            response = client.post(
                "/api/v1/auth/register",
                json={
                    "email": f"athlete-{index}@example.test",
                    "password": "a sufficiently long password",
                },
            )
            assert response.status_code == 201
        limited = client.post(
            "/api/v1/auth/register",
            json={
                "email": "athlete-2@example.test",
                "password": "a sufficiently long password",
            },
        )

    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) > 0


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_concurrent_rate_limit_attempts_are_counted_atomically() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_clear_auth_data(INTEGRATION_DATABASE_URL))

    statuses = asyncio.run(_concurrent_rate_limit_statuses(INTEGRATION_DATABASE_URL))

    assert sorted(statuses) == [200, 200, 429]


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_expired_session_is_rejected(auth_client: TestClient) -> None:
    registered = auth_client.post(
        "/api/v1/auth/register",
        json={
            "email": "expired@example.test",
            "password": "a sufficiently long password",
        },
    )
    session_token = auth_client.cookies.get("opennosh_session")
    assert registered.status_code == 201
    assert session_token is not None
    assert INTEGRATION_DATABASE_URL is not None
    asyncio.run(_expire_session(INTEGRATION_DATABASE_URL, hash_token(session_token)))

    assert auth_client.get("/api/v1/auth/session").status_code == 401


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_production_session_cookie_uses_host_prefix_and_secure_defaults() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_clear_auth_data(INTEGRATION_DATABASE_URL))
    app = create_app(
        Settings(
            database_url=INTEGRATION_DATABASE_URL,
            app_environment="production",
            auth_rate_limit_attempts=20,
            food_search_cursor_signing_keys=(
                "prod-v1:33333333333333333333333333333333"
            ),
            _env_file=None,
        )
    )
    with TestClient(app, base_url="https://opennosh.example.test") as client:
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": "secure@example.test",
                "password": "a sufficiently long password",
            },
        )

    cookie_headers = response.headers.get_list("set-cookie")
    session_cookie = next(
        cookie for cookie in cookie_headers if cookie.startswith("__Host-opennosh-session=")
    )
    assert "Secure" in session_cookie
    assert "HttpOnly" in session_cookie
    assert "SameSite=lax" in session_cookie
    assert "Path=/" in session_cookie
    assert "Domain=" not in session_cookie
    csrf_cookie = next(
        cookie for cookie in cookie_headers if cookie.startswith("__Host-opennosh-csrf=")
    )
    assert "Secure" in csrf_cookie
    assert "SameSite=strict" in csrf_cookie
    assert "Path=/" in csrf_cookie
    assert "HttpOnly" not in csrf_cookie
