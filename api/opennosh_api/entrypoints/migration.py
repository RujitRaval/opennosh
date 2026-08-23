from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config

from opennosh_api.capacity import JobRole
from opennosh_api.settings import get_settings


def main() -> None:
    settings = get_settings()
    database_url = settings.process_database_url(JobRole.MIGRATION)
    previous_database_url = os.environ.get("MIGRATION_DATABASE_URL")
    os.environ["MIGRATION_DATABASE_URL"] = database_url
    try:
        config_path = Path(os.environ.get("ALEMBIC_CONFIG", "api/alembic.ini"))
        command.upgrade(Config(str(config_path)), "head")
    finally:
        if previous_database_url is None:
            os.environ.pop("MIGRATION_DATABASE_URL", None)
        else:
            os.environ["MIGRATION_DATABASE_URL"] = previous_database_url
