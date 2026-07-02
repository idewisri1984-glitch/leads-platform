from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings.

    Values are loaded from the .env file and environment variables.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------

    app_name: str = "Bali Leads Platform"
    app_version: str = "0.1.0"
    environment: str = "development"
    debug: bool = True

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------

    database_url: str = Field(
        default="sqlite:///data/bali_leads.db",
    )

    # ------------------------------------------------------------------
    # OpenAI
    # ------------------------------------------------------------------

    openai_api_key: str = ""

    # ------------------------------------------------------------------
    # SerpAPI
    # ------------------------------------------------------------------

    serpapi_api_key: str = ""

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """
    Return cached application settings.
    """

    return Settings()


settings = get_settings()