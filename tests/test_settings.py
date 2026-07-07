from app.core.config.settings import settings


def test_settings() -> None:
    assert settings.app_name == "Bali Leads Platform"
    assert settings.database_url.startswith("sqlite")
