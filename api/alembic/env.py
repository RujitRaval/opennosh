from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from opennosh_api.capacity import JobRole, load_capacity_manifest
from opennosh_api.models.registry import metadata
from opennosh_api.settings import Settings
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = Settings()
database_url = os.getenv("INTEGRATION_DATABASE_URL") or settings.process_database_url(
    JobRole.MIGRATION
)
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

target_metadata = metadata
capacity_manifest = load_capacity_manifest(settings.database_capacity_manifest_path)
migration_budget = capacity_manifest.jobs[JobRole.MIGRATION]


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args={
            "server_settings": {
                "application_name": (
                    f"opennosh:{capacity_manifest.deployment_id}:migration"[:63]
                ),
                "statement_timeout": str(migration_budget.statement_timeout_ms),
            }
        },
    )

    try:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
