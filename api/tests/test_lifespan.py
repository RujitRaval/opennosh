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


class DisposableOpenFoodFactsClient:
    def __init__(self, **_kwargs: object) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


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


@pytest.mark.asyncio
async def test_lifespan_closes_the_optional_open_food_facts_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = DisposableEngine()
    client = DisposableOpenFoodFactsClient()
    monkeypatch.setattr(main, "build_engine", lambda _: cast(AsyncEngine, engine))
    monkeypatch.setattr(main, "OpenFoodFactsClient", lambda **_kwargs: client)
    app = create_app(
        Settings(
            database_url="postgresql+asyncpg://unused:unused@localhost/unused",
            open_food_facts_enabled=True,
            _env_file=None,
        )
    )

    async with app.router.lifespan_context(app):
        assert app.state.open_food_facts_client is client

    assert client.closed is True
    assert engine.disposed is True
