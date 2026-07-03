from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(
        default="Bali Leads Platform",
        alias="APP_NAME",
    )

    database_url: str = Field(
        default="sqlite:///data/bali_leads.db",
        alias="DATABASE_URL",
    )

    debug: bool = Field(
        default=False,
        alias="DEBUG",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
