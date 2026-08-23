import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker

from opennosh_api.auth.router import router as auth_router
from opennosh_api.body_metrics.router import router as body_metrics_router
from opennosh_api.capacity import ProcessRole, load_capacity_manifest
from opennosh_api.contracts import common_problem_responses, install_openapi_contract
from opennosh_api.database import (
    DatabaseIdentity,
    DatabasePoolMetrics,
    SqlAlchemyHealthProbe,
    build_engine,
)
from opennosh_api.database_metrics import router as database_metrics_router
from opennosh_api.exercises.router import export_router as exercise_export_router
from opennosh_api.exercises.router import router as exercises_router
from opennosh_api.exports.router import router as exports_router
from opennosh_api.foods.router import export_router as food_export_router
from opennosh_api.foods.router import router as foods_router
from opennosh_api.health import router as health_router
from opennosh_api.integrations.open_food_facts import OpenFoodFactsClient
from opennosh_api.logs.cache_control import FoodLogNoStoreMiddleware
from opennosh_api.logs.router import router as logs_router
from opennosh_api.problems import RequestIdMiddleware, install_problem_handlers
from opennosh_api.recipes.router import router as recipes_router
from opennosh_api.settings import Settings, get_settings
from opennosh_api.targets.router import router as targets_router
from opennosh_api.workouts.router import router as workouts_router


def read_app_version() -> str:
    try:
        return version("opennosh")
    except PackageNotFoundError:
        return (Path(__file__).resolve().parents[2] / "VERSION").read_text().strip()


def create_app(
    settings: Settings | None = None,
    *,
    app_version: str | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    resolved_app_version = app_version or read_app_version()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        capacity_manifest = load_capacity_manifest(
            resolved_settings.database_capacity_manifest_path
        )
        role_budget = capacity_manifest.active_role_budget(ProcessRole.WEB)
        identity = DatabaseIdentity(
            deployment_id=capacity_manifest.deployment_id,
            role=ProcessRole.WEB.value,
        )
        database_pool_metrics = DatabasePoolMetrics(identity, role_budget.pool_size)
        engine = build_engine(
            resolved_settings.process_database_url(ProcessRole.WEB),
            identity=identity,
            budget=role_budget,
            metrics=database_pool_metrics,
        )
        open_food_facts_client = (
            OpenFoodFactsClient(
                base_url=resolved_settings.open_food_facts_base_url,
                app_version=resolved_app_version,
                contact=resolved_settings.open_food_facts_user_agent_contact,
                timeout_seconds=resolved_settings.open_food_facts_timeout_seconds,
            )
            if resolved_settings.open_food_facts_enabled
            else None
        )
        app.state.database_probe = SqlAlchemyHealthProbe(
            engine,
            timeout_seconds=resolved_settings.database_healthcheck_timeout_seconds,
        )
        app.state.session_factory = async_sessionmaker(engine, expire_on_commit=False)
        app.state.database_pool_metrics = database_pool_metrics
        app.state.database_capacity_manifest = capacity_manifest
        app.state.open_food_facts_client = open_food_facts_client
        try:
            yield
        finally:
            try:
                if open_food_facts_client is not None:
                    await open_food_facts_client.aclose()
            finally:
                await engine.dispose()

    application = FastAPI(
        title="opennosh API",
        version=resolved_app_version,
        lifespan=lifespan,
        responses=common_problem_responses(),
    )
    application.state.settings = resolved_settings
    application.state.public_export_semaphore = asyncio.Semaphore(
        resolved_settings.public_export_concurrency_limit
    )
    application.state.private_export_semaphore = asyncio.Semaphore(
        resolved_settings.private_export_concurrency_limit
    )
    application.add_middleware(RequestIdMiddleware)
    application.add_middleware(FoodLogNoStoreMiddleware)
    install_problem_handlers(application)
    application.include_router(health_router)
    application.include_router(database_metrics_router)
    application.include_router(auth_router)
    application.include_router(foods_router)
    application.include_router(food_export_router)
    application.include_router(exercises_router)
    application.include_router(exercise_export_router)
    application.include_router(exports_router)
    application.include_router(logs_router)
    application.include_router(recipes_router)
    application.include_router(targets_router)
    application.include_router(body_metrics_router)
    application.include_router(workouts_router)
    install_openapi_contract(application)
    return application


app = create_app()
