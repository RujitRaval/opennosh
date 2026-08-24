import asyncio
from typing import cast

import httpx
import pytest
from opennosh_api import main
from opennosh_api.main import create_app
from opennosh_api.public_commons.manifests import PublicCommonsSnapshotService
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


class MaterializerService:
    def __init__(self, cache_status: str) -> None:
        self.cache_status = cache_status
        self.refreshes = 0
        self.materialization_enabled = True

    async def refresh_response(self):  # type: ignore[no-untyped-def]
        self.refreshes += 1
        return type("RefreshResult", (), {"cache_status": self.cache_status})()


class RecordingAsyncClient:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict[str, str]]] = []
        self.fail = False

    async def __aenter__(self) -> "RecordingAsyncClient":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def post(self, url: str, *, headers: dict[str, str]) -> httpx.Response:
        self.posts.append((url, headers))
        request = httpx.Request("POST", url)
        if self.fail:
            raise httpx.ConnectError("simulated web outage", request=request)
        return httpx.Response(204, request=request)


@pytest.mark.asyncio
async def test_periodic_public_commons_materializer_refreshes_after_each_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps = 0

    async def controlled_sleep(_seconds: float) -> None:
        nonlocal sleeps
        sleeps += 1
        if sleeps == 2:
            raise asyncio.CancelledError

    service = MaterializerService("projection")
    monkeypatch.setattr(main.asyncio, "sleep", controlled_sleep)

    with pytest.raises(asyncio.CancelledError):
        await main.run_public_commons_materializer(
            cast(PublicCommonsSnapshotService, service), Settings(_env_file=None)
        )

    assert service.refreshes == 1


@pytest.mark.asyncio
async def test_lifespan_disposes_the_engine_after_an_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = DisposableEngine()
    monkeypatch.setattr(main, "build_engine", lambda *_args, **_kwargs: cast(AsyncEngine, engine))
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
    monkeypatch.setattr(main, "build_engine", lambda *_args, **_kwargs: cast(AsyncEngine, engine))
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


@pytest.mark.asyncio
async def test_public_commons_materializer_invalidates_only_after_rebuild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callback_url = "http://web:3000/api/internal/public-commons/revalidate"
    settings = Settings(
        public_commons_revalidation_url=callback_url,
        public_commons_revalidation_token="test-public-commons-revalidation-token",
        _env_file=None,
    )
    service = MaterializerService("projection")
    client = RecordingAsyncClient()
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda **_kwargs: client)

    await main.refresh_public_commons_once(
        cast(PublicCommonsSnapshotService, service), settings
    )
    assert client.posts == []

    service.cache_status = "rebuilt"
    await main.refresh_public_commons_once(
        cast(PublicCommonsSnapshotService, service), settings
    )
    assert client.posts == [
        (
            callback_url,
            {"x-opennosh-proxy-token": "test-public-commons-revalidation-token"},
        )
    ]

    client.fail = True
    await main.refresh_public_commons_once(
        cast(PublicCommonsSnapshotService, service), settings
    )
    assert service.refreshes == 3


@pytest.mark.asyncio
async def test_lifespan_startup_rebuild_invalidates_the_public_commons_edge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = DisposableEngine()
    service = MaterializerService("rebuilt")
    client = RecordingAsyncClient()
    callback_url = "http://web:3000/api/internal/public-commons/revalidate"
    settings = Settings(
        database_url="postgresql+asyncpg://unused:unused@localhost/unused",
        public_commons_revalidation_url=callback_url,
        public_commons_revalidation_token="test-public-commons-revalidation-token",
        _env_file=None,
    )
    monkeypatch.setattr(main, "build_engine", lambda *_args, **_kwargs: cast(AsyncEngine, engine))
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda **_kwargs: client)
    app = create_app(settings)
    app.state.public_commons_snapshot_service = cast(PublicCommonsSnapshotService, service)

    async with app.router.lifespan_context(app):
        assert service.refreshes == 1

    assert client.posts == [
        (
            callback_url,
            {"x-opennosh-proxy-token": "test-public-commons-revalidation-token"},
        )
    ]
    assert engine.disposed is True
