from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
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

    smtp_host: str | None = Field(default=None, alias="SMTP_HOST")
    smtp_port: int = Field(default=587, ge=1, le=65535, alias="SMTP_PORT")
    smtp_security_mode: str = Field(default="STARTTLS", alias="SMTP_SECURITY_MODE")
    smtp_username: str | None = Field(default=None, alias="SMTP_USERNAME")
    smtp_password: SecretStr | None = Field(default=None, alias="SMTP_PASSWORD", repr=False)
    smtp_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        le=120,
        alias="SMTP_TIMEOUT_SECONDS",
    )
    smtp_envelope_from: str | None = Field(default=None, alias="SMTP_ENVELOPE_FROM")
    smtp_header_from_email: str | None = Field(default=None, alias="SMTP_HEADER_FROM_EMAIL")
    smtp_header_from_name: str | None = Field(default=None, alias="SMTP_HEADER_FROM_NAME")
    smtp_reply_to: str | None = Field(default=None, alias="SMTP_REPLY_TO")
    smtp_message_id_domain: str | None = Field(default=None, alias="SMTP_MESSAGE_ID_DOMAIN")
    smtp_transport_name: str = Field(default="stdlib-smtp", alias="SMTP_TRANSPORT_NAME")

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

    @field_validator("smtp_port", mode="before")
    @classmethod
    def reject_non_integer_smtp_port(cls, value: object) -> object:
        if type(value) not in {int, str}:
            raise ValueError("SMTP port must be an integer.")
        return value

    @field_validator("smtp_timeout_seconds", mode="before")
    @classmethod
    def reject_boolean_smtp_timeout(cls, value: object) -> object:
        if type(value) is bool:
            raise ValueError("SMTP timeout must be numeric.")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
