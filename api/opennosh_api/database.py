import asyncio
from collections.abc import AsyncIterator
from typing import Protocol

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine


class DatabaseHealthProbe(Protocol):
    async def check(self) -> None: ...


class SqlAlchemyHealthProbe:
    def __init__(self, engine: AsyncEngine, timeout_seconds: float) -> None:
        self._engine = engine
        self._timeout_seconds = timeout_seconds

    async def check(self) -> None:
        async with asyncio.timeout(self._timeout_seconds):
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))


def build_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, pool_pre_ping=True)


async def get_database_probe(request: Request) -> AsyncIterator[DatabaseHealthProbe]:
    yield request.app.state.database_probe


async def get_database_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        yield session
