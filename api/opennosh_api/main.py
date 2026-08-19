from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from opennosh_api.database import SqlAlchemyHealthProbe, build_engine
from opennosh_api.health import router as health_router
from opennosh_api.settings import Settings, get_settings


def read_app_version() -> str:
    return (Path(__file__).resolve().parents[2] / "VERSION").read_text().strip()


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = build_engine(resolved_settings.database_url)
        app.state.database_probe = SqlAlchemyHealthProbe(
            engine,
            timeout_seconds=resolved_settings.database_healthcheck_timeout_seconds,
        )
        try:
            yield
        finally:
            await engine.dispose()

    application = FastAPI(title="opennosh API", version=read_app_version(), lifespan=lifespan)
    application.include_router(health_router)
    return application


app = create_app()
