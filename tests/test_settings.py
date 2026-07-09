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
