from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.modules.contact_discovery.website_provider import WebsiteContactDiscoveryProvider


def build_website_contact_discovery_provider() -> "WebsiteContactDiscoveryProvider":
    from app.core.config.settings import get_settings
    from app.modules.contact_discovery.website_provider import WebsiteContactDiscoveryProvider
    from app.providers.contact_search import SerpApiContactSearchProvider
    from app.providers.serpapi.client import SerpApiClient

    settings = get_settings()
    search_provider = None
    if settings.serpapi_api_key:
        search_provider = SerpApiContactSearchProvider(
            SerpApiClient(
                api_key=settings.serpapi_api_key,
                base_url=settings.serpapi_base_url,
                timeout_seconds=settings.serpapi_timeout_seconds,
            )
        )
    return WebsiteContactDiscoveryProvider(search_provider=search_provider)
