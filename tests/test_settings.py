import pytest
from pydantic import ValidationError

from app.core.config.settings import Settings, settings


def test_settings() -> None:
    assert settings.app_name == "Bali Leads Platform"
    assert settings.database_url.startswith("sqlite")


def test_settings_load_without_serpapi_api_key() -> None:
    test_settings = Settings(SERPAPI_API_KEY=None)

    assert test_settings.serpapi_api_key is None
    assert test_settings.serpapi_base_url == "https://serpapi.com/search.json"
    assert test_settings.serpapi_timeout_seconds == 10.0


def test_settings_load_with_serpapi_api_key() -> None:
    test_settings = Settings(SERPAPI_API_KEY="test-key")

    assert test_settings.serpapi_api_key == "test-key"


def test_settings_load_without_openai_credentials() -> None:
    test_settings = Settings(_env_file=None, OPENAI_API_KEY=None, OPENAI_MODEL=None)

    assert test_settings.openai_api_key is None
    assert test_settings.openai_model is None
    assert test_settings.openai_timeout_seconds == 30.0
    assert test_settings.openai_max_output_tokens == 600


def test_settings_load_explicit_openai_values_without_changing_serpapi() -> None:
    test_settings = Settings(
        _env_file=None,
        OPENAI_API_KEY="test-openai-key",
        OPENAI_MODEL="test-model",
        OPENAI_TIMEOUT_SECONDS=12.5,
        OPENAI_MAX_OUTPUT_TOKENS=900,
        SERPAPI_API_KEY="test-serpapi-key",
    )

    assert test_settings.openai_api_key == "test-openai-key"
    assert test_settings.openai_model == "test-model"
    assert test_settings.openai_timeout_seconds == 12.5
    assert test_settings.openai_max_output_tokens == 900
    assert test_settings.serpapi_api_key == "test-serpapi-key"
    assert test_settings.serpapi_base_url == "https://serpapi.com/search.json"
    assert test_settings.serpapi_timeout_seconds == 10.0


def test_settings_repr_does_not_expose_openai_key() -> None:
    rendered = repr(Settings(_env_file=None, OPENAI_API_KEY="never-render-this"))

    assert "never-render-this" not in rendered
    assert "openai_api_key" not in rendered


@pytest.mark.parametrize("value", [True, False, 0, -1, 120.1, None])
def test_settings_reject_invalid_openai_timeout(value: object) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, OPENAI_TIMEOUT_SECONDS=value)


@pytest.mark.parametrize("value", [True, False, 99, 2001, 100.0, None])
def test_settings_reject_invalid_openai_max_output_tokens(value: object) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, OPENAI_MAX_OUTPUT_TOKENS=value)
