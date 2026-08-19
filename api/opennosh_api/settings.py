from functools import lru_cache

from pydantic import PositiveFloat
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://opennosh:opennosh@localhost:5432/opennosh"
    database_healthcheck_timeout_seconds: PositiveFloat = 2.0

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
