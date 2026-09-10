"""The privileged owner command preserves one-way, audited authorization history."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from alembic import command
from opennosh_api.governance import owner_admin
from opennosh_api.governance.models import GovernanceRoleAssignment
from opennosh_api.models.auth import User
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.tests.test_migrations import migration_config

DATABASE = os.getenv("INTEGRATION_DATABASE_URL")


async def _exercise(database: str) -> None:
    engine = create_async_engine(database)
    sessions = async_sessionmaker(engine)
    actor, pack = uuid4(), f"owner-admin-{uuid4()}"
    args = dict(actor_id=actor, pack_id=pack, authorized_by=actor, reason="Owner pilot")
    try:
        async with sessions() as session, session.begin():
            session.add(User(id=actor, email=f"{actor}@example.test", password_hash="test"))
        with pytest.raises(ValueError, match="existing active pack steward"):
            await owner_admin.change_owner_authorization(database, **args)
        with pytest.raises(ValueError, match="does not exist"):
            await owner_admin.change_owner_authorization(database, **args, revoke=True)
        with pytest.raises(ValueError, match="bounded pack"):
            await owner_admin.change_owner_authorization(database, **{**args, "reason": " "})
        async with sessions() as session, session.begin():
            session.add(
                GovernanceRoleAssignment(
                    pack_id=pack,
                    actor_id=actor,
                    role="steward",
                    granted_by_actor_id=actor,
                    grant_reason="Actual owner assignment",
                    granted_at=datetime.now(UTC) - timedelta(seconds=1),
                )
            )
        granted = await owner_admin.change_owner_authorization(database, **args)
        assert granted["state"] == "active"
        assert granted["actor_id"] == str(actor)
        assert granted["pack_id"] == pack
        assert await owner_admin.change_owner_authorization(database, **args) == granted
        revoked = await owner_admin.change_owner_authorization(database, **args, revoke=True)
        assert revoked == {**granted, "state": "revoked"}
        assert (
            await owner_admin.change_owner_authorization(database, **args, revoke=True) == revoked
        )
        with pytest.raises(ValueError, match="cannot be rewritten"):
            await owner_admin.change_owner_authorization(database, **args)
    finally:
        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE users CASCADE"))
        await engine.dispose()


@pytest.mark.skipif(DATABASE is None, reason="local PostgreSQL integration database required")
def test_owner_authorization_admin_lifecycle() -> None:
    assert DATABASE is not None
    command.upgrade(migration_config(DATABASE), "head")
    asyncio.run(_exercise(DATABASE))


def test_owner_admin_cli_requires_private_database_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = str(uuid4())
    monkeypatch.setattr(
        "sys.argv",
        [
            "owner_admin",
            "--actor-id",
            actor,
            "--pack-id",
            "test",
            "--authorized-by",
            actor,
            "--reason",
            "Owner pilot",
        ],
    )
    monkeypatch.delenv("MIGRATION_DATABASE_URL", raising=False)
    with pytest.raises(SystemExit, match="MIGRATION_DATABASE_URL is required"):
        owner_admin.main()


def test_owner_admin_cli_binds_explicit_arguments(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    actor = str(uuid4())
    monkeypatch.setattr(
        "sys.argv",
        [
            "owner_admin",
            "--actor-id",
            actor,
            "--pack-id",
            "test",
            "--authorized-by",
            actor,
            "--reason",
            "Owner pilot",
            "--revoke",
        ],
    )
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "private-test-url")

    async def change(database_url: str, **kwargs: object) -> dict[str, str]:
        assert database_url == "private-test-url"
        assert str(kwargs["actor_id"]) == actor
        assert str(kwargs["authorized_by"]) == actor
        assert kwargs["pack_id"] == "test"
        assert kwargs["reason"] == "Owner pilot"
        assert kwargs["revoke"] is True
        return {"state": "revoked"}

    monkeypatch.setattr(owner_admin, "change_owner_authorization", change)
    owner_admin.main()
    assert json.loads(capsys.readouterr().out) == {"state": "revoked"}
