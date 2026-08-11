from app.modules.contact_discovery.website_provider import ContactSearchResult
from app.providers.serpapi.client import SerpApiClient


class SerpApiContactSearchProvider:
    def __init__(self, client: SerpApiClient) -> None:
        self._client = client

    def search(self, *, query: str, limit: int) -> tuple[ContactSearchResult, ...]:
        response = self._client.search_companies(
            query=query,
            country=None,
            city=None,
            industry=None,
            limit=limit,
        )
        return tuple(
            ContactSearchResult(url=result.link)
            for result in response.results
            if result.link is not None
        )
