"""Keep the default search projection ready without charging visitors for refreshes."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from opennosh_api.foods.service import _fresh_snapshot
from opennosh_api.settings import Settings

logger = logging.getLogger(__name__)


async def warm_food_search_once(
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> bool:
    try:
        async with asyncio.timeout(settings.food_search_snapshot_build_timeout_ms / 1_000):
            async with sessions() as database:
                await database.execute(
                    text("SELECT set_config('statement_timeout', :timeout, true)"),
                    {"timeout": f"{settings.food_search_snapshot_build_timeout_ms}ms"},
                )
                await _fresh_snapshot(
                    database,
                    now=datetime.now(UTC),
                    refresh_seconds=settings.food_search_snapshot_refresh_seconds,
                    retention_seconds=settings.food_search_snapshot_retention_seconds,
                )
        return True
    except Exception as error:
        logger.warning("Food search background refresh failed error_type=%s", type(error).__name__)
        return False


async def run_food_search_warmer(
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    while True:
        await asyncio.sleep(min(30.0, settings.food_search_snapshot_refresh_seconds / 2))
        await warm_food_search_once(sessions, settings)
