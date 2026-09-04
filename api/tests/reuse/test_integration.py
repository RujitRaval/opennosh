from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from alembic import command as alembic_command
from opennosh_api.reuse.contracts import (
    ReuseDeclarationCreate,
    ReuseDeclarationPatch,
    ReuseEventType,
    ReuseRegionLevel,
)
from opennosh_api.reuse.service import (
    ReuseRegistryError,
    create_declaration,
    patch_declaration,
    read_owned_declaration,
    transition_declaration,
)
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.tests.test_migrations import migration_config

INTEGRATION_DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL")
NOW = datetime(2026, 9, 3, 22, tzinfo=UTC)


async def _exercise_registry(database_url: str) -> None:
    owner = uuid4()
    other = uuid4()
    key = UUID("30000000-0000-4000-8000-000000000001")
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO users (id, email, password_hash) VALUES "
                    "(:owner, :owner_email, 'hash'), (:other, :other_email, 'hash')"
                ),
                {
                    "owner": owner,
                    "owner_email": f"reuse-owner-{owner.hex}@example.test",
                    "other": other,
                    "other_email": f"reuse-other-{other.hex}@example.test",
                },
            )

        request = ReuseDeclarationCreate(
            organization_name="Community Kitchen",
            project_name="Meal Commons",
            project_url="https://example.test/reuse",
            use_case="Uses verified records in a public menu.",
            region_level=ReuseRegionLevel.COUNTRY,
            region_code="US",
        )
        async with sessions() as session, session.begin():
            declaration, created = await create_declaration(
                session,
                owner_actor_id=owner,
                request=request,
                idempotency_key=key,
                now=NOW,
            )
        assert created
        declaration_id = declaration.id

        async with sessions() as session, session.begin():
            replayed, replay_created = await create_declaration(
                session,
                owner_actor_id=owner,
                request=request,
                idempotency_key=key,
                now=NOW + timedelta(minutes=1),
            )
        assert replayed.id == declaration_id
        assert not replay_created

        async with sessions() as session, session.begin():
            with pytest.raises(ReuseRegistryError, match="idempotency_payload_mismatch"):
                await create_declaration(
                    session,
                    owner_actor_id=owner,
                    request=request.model_copy(update={"project_name": "Other project"}),
                    idempotency_key=key,
                    now=NOW + timedelta(minutes=2),
                )

        async with sessions() as session, session.begin():
            declaration = await patch_declaration(
                session,
                declaration_id=declaration_id,
                owner_actor_id=owner,
                expected_revision=1,
                request=ReuseDeclarationPatch(use_case="Corrected public menu use."),
                idempotency_key=uuid4(),
                now=NOW + timedelta(minutes=3),
            )
        assert declaration.revision == 2

        async with sessions() as session, session.begin():
            declaration = await transition_declaration(
                session,
                declaration_id=declaration_id,
                owner_actor_id=owner,
                expected_revision=2,
                action=ReuseEventType.SUBMITTED,
                idempotency_key=uuid4(),
                reason="Ready for independent review.",
                now=NOW + timedelta(minutes=4),
            )
        assert declaration.state == "verification_pending"

        async with sessions() as session, session.begin():
            with pytest.raises(ReuseRegistryError, match="not_found"):
                await read_owned_declaration(
                    session,
                    declaration_id=declaration_id,
                    owner_actor_id=other,
                )

        async with sessions() as session:
            event_count = await session.scalar(
                text(
                    "SELECT count(*) FROM reuse_declaration_events "
                    "WHERE declaration_id = :declaration_id"
                ),
                {"declaration_id": declaration_id},
            )
            assert event_count == 3

        async with engine.begin() as connection:
            with pytest.raises(DBAPIError, match="append-only"):
                await connection.execute(
                    text(
                        "UPDATE reuse_declaration_events SET reason = 'rewritten' "
                        "WHERE declaration_id = :declaration_id"
                    ),
                    {"declaration_id": declaration_id},
                )

    finally:
        await engine.dispose()


@pytest.mark.skipif(
    INTEGRATION_DATABASE_URL is None,
    reason="INTEGRATION_DATABASE_URL is required for PostgreSQL integration tests",
)
def test_registry_lifecycle_is_idempotent_owner_scoped_and_append_only() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    alembic_command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_exercise_registry(INTEGRATION_DATABASE_URL))
