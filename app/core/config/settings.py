from functools import lru_cache

from pydantic import Field, field_validator
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

    serpapi_api_key: str | None = Field(
        default=None,
        alias="SERPAPI_API_KEY",
    )

    serpapi_base_url: str = Field(
        default="https://serpapi.com/search.json",
        alias="SERPAPI_BASE_URL",
    )

    serpapi_timeout_seconds: float = Field(
        default=10.0,
        alias="SERPAPI_TIMEOUT_SECONDS",
    )

    openai_api_key: str | None = Field(
        default=None,
        alias="OPENAI_API_KEY",
        repr=False,
    )

    openai_model: str | None = Field(
        default=None,
        alias="OPENAI_MODEL",
    )

    openai_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        le=120,
        alias="OPENAI_TIMEOUT_SECONDS",
    )

    openai_max_output_tokens: int = Field(
        default=600,
        ge=100,
        le=2000,
        alias="OPENAI_MAX_OUTPUT_TOKENS",
    )

    debug: bool = Field(
        default=False,
        alias="DEBUG",
    )

    @field_validator("openai_timeout_seconds", mode="before")
    @classmethod
    def reject_boolean_openai_timeout(cls, value: object) -> object:
        if type(value) is bool:
            raise ValueError("OpenAI timeout must be numeric.")
        return value

    @field_validator("openai_max_output_tokens", mode="before")
    @classmethod
    def reject_non_integer_openai_tokens(cls, value: object) -> object:
        if type(value) not in {int, str}:
            raise ValueError("OpenAI maximum output tokens must be an integer.")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
