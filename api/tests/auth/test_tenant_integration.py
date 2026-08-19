from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from opennosh_api.auth.dependencies import CurrentSession
from opennosh_api.auth.tenant import (
    delete_owned_resource,
    get_owned_resource,
    update_owned_resource,
)
from opennosh_api.models import AuthSession, FoodCustom, User
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.tests.auth.idor_assertions import assert_cross_user_access_is_denied
from api.tests.test_migrations import migration_config

INTEGRATION_DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL")


async def _exercise_tenant_helpers(database_url: str) -> None:
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE auth_sessions, users CASCADE"))

        async with session_factory() as session:
            owner = User(email="owner@example.test", password_hash="hash")
            attacker = User(email="attacker@example.test", password_hash="hash")
            session.add_all([owner, attacker])
            await session.flush()
            food = FoodCustom(
                user_id=owner.id,
                name="Owner's oats",
                nutrients_json={},
                portions_json=[],
            )
            session.add(food)
            owner_session = AuthSession(
                user_id=owner.id,
                token_hash="a" * 64,
                csrf_token_hash="b" * 64,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            attacker_session = AuthSession(
                user_id=attacker.id,
                token_hash="c" * 64,
                csrf_token_hash="d" * 64,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            session.add_all([owner_session, attacker_session])
            await session.commit()

            owner_identity = CurrentSession(user=owner, session=owner_session)
            attacker_identity = CurrentSession(user=attacker, session=attacker_session)

            await assert_cross_user_access_is_denied(
                session,
                FoodCustom,
                resource_id=food.id,
                attacker=attacker_identity,
                changes={"name": "Stolen oats"},
            )
            owned = await get_owned_resource(
                session, FoodCustom, resource_id=food.id, current=owner_identity
            )
            assert owned is not None
            assert owned.name == "Owner's oats"
            updated = await update_owned_resource(
                session,
                FoodCustom,
                resource_id=food.id,
                current=owner_identity,
                changes={"name": "Owner's updated oats"},
            )
            assert updated is not None
            assert updated.name == "Owner's updated oats"
            with pytest.raises(ValueError, match="protected tenant field"):
                await update_owned_resource(
                    session,
                    FoodCustom,
                    resource_id=food.id,
                    current=owner_identity,
                    changes={"user_id": attacker.id},
                )
            assert (
                await delete_owned_resource(
                    session,
                    FoodCustom,
                    resource_id=food.id,
                    current=owner_identity,
                )
                is True
            )
            assert (
                await get_owned_resource(
                    session, FoodCustom, resource_id=food.id, current=owner_identity
                )
                is None
            )
            await session.commit()
    finally:
        await engine.dispose()


@pytest.mark.skipif(INTEGRATION_DATABASE_URL is None, reason="PostgreSQL is not configured")
def test_tenant_helpers_deny_cross_user_reads_writes_and_deletes() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_exercise_tenant_helpers(INTEGRATION_DATABASE_URL))
