from typing import cast

import pytest
from opennosh_api import main
from opennosh_api.main import create_app
from opennosh_api.settings import Settings
from sqlalchemy.ext.asyncio import AsyncEngine


class DisposableEngine:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


@pytest.mark.asyncio
async def test_lifespan_disposes_the_engine_after_an_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = DisposableEngine()
    monkeypatch.setattr(main, "build_engine", lambda _: cast(AsyncEngine, engine))
    app = create_app(Settings(database_url="postgresql+asyncpg://unused:unused@localhost/unused"))

    with pytest.raises(RuntimeError, match="simulated application failure"):
        async with app.router.lifespan_context(app):
            raise RuntimeError("simulated application failure")

    assert engine.disposed is True
