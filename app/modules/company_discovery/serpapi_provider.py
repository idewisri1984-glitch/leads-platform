from pydantic import ValidationError

from app.core.country_targets import get_country_target
from app.modules.company_discovery.provider_interfaces import (
    DiscoveryProviderAuthenticationError,
    DiscoveryProviderConfigurationError,
    DiscoveryProviderError,
    DiscoveryProviderQuotaExceededError,
    DiscoveryProviderRateLimitError,
    DiscoveryProviderRequestError,
    DiscoveryProviderResponseError,
    DiscoveryProviderResponseTooLargeError,
)
from app.modules.company_discovery.schemas import (
    DiscoveryProviderResponse,
    DiscoveryProviderResult,
)
from app.modules.search_profile.schemas import SearchQuery
from app.providers.serpapi import (
    SerpApiAuthenticationError,
    SerpApiClient,
    SerpApiConfigurationError,
    SerpApiError,
    SerpApiProviderError,
    SerpApiQuotaExceededError,
    SerpApiRateLimitError,
    SerpApiRequestError,
    SerpApiResponseError,
    SerpApiResponseTooLargeError,
)


class SerpApiDiscoveryProvider:
    """
    Provider-independent discovery wrapper around the low-level SerpAPI client.
    """

    def __init__(self, client: SerpApiClient) -> None:
        self.client = client

    @property
    def provider_name(self) -> str:
        return "serpapi"

    def search(self, query: SearchQuery) -> DiscoveryProviderResponse:
        try:
            if query.country_code is None:
                google_country_code = None
            else:
                google_country_code = get_country_target(query.country_code).serpapi_gl

            response = self.client.search_companies(
                query=query.text,
                country=None,
                city=None,
                industry=None,
                google_country_code=google_country_code,
                limit=query.limit,
            )
        except SerpApiConfigurationError:
            raise DiscoveryProviderConfigurationError(
                "Discovery provider is not configured."
            ) from None
        except SerpApiAuthenticationError:
            raise DiscoveryProviderAuthenticationError(
                "Discovery provider authentication failed."
            ) from None
        except SerpApiQuotaExceededError:
            raise DiscoveryProviderQuotaExceededError(
                "Discovery provider quota was exceeded."
            ) from None
        except SerpApiRateLimitError:
            raise DiscoveryProviderRateLimitError(
                "Discovery provider rate limit exceeded."
            ) from None
        except SerpApiResponseTooLargeError:
            raise DiscoveryProviderResponseTooLargeError(
                "Discovery provider response exceeded the allowed size."
            ) from None
        except SerpApiResponseError:
            raise DiscoveryProviderResponseError(
                "Discovery provider response was invalid."
            ) from None
        except SerpApiRequestError:
            raise DiscoveryProviderRequestError("Discovery provider request failed.") from None
        except SerpApiProviderError:
            raise DiscoveryProviderError("Discovery provider failed.") from None
        except SerpApiError:
            raise DiscoveryProviderError("Discovery provider failed.") from None

        response_query = response.query.strip() or query.text

        try:
            return DiscoveryProviderResponse(
                provider=self.provider_name,
                query=response_query,
                results=[
                    DiscoveryProviderResult(
                        title=result.title,
                        link=result.link,
                        snippet=result.snippet,
                        source=result.source,
                        position=result.position,
                        provider_reference=None,
                    )
                    for result in response.results
                ],
                total_results=response.total_results,
            )
        except ValidationError:
            raise DiscoveryProviderResponseError(
                "Discovery provider response was invalid."
            ) from None
