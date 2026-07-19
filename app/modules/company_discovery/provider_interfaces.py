from typing import Protocol, runtime_checkable

from app.modules.company_discovery.schemas import DiscoveryProviderResponse
from app.modules.search_profile.schemas import SearchQuery


class DiscoveryProviderError(Exception):
    """Base controlled discovery provider failure."""


class DiscoveryProviderConfigurationError(DiscoveryProviderError):
    pass


class DiscoveryProviderRequestError(DiscoveryProviderError):
    pass


class DiscoveryProviderRateLimitError(DiscoveryProviderRequestError):
    pass


class DiscoveryProviderAuthenticationError(DiscoveryProviderError):
    pass


class DiscoveryProviderQuotaExceededError(DiscoveryProviderRateLimitError):
    pass


class DiscoveryProviderResponseError(DiscoveryProviderError):
    pass


class DiscoveryProviderResponseTooLargeError(DiscoveryProviderResponseError):
    pass


@runtime_checkable
class DiscoveryProvider(Protocol):
    @property
    def provider_name(self) -> str: ...

    def search(self, query: SearchQuery) -> DiscoveryProviderResponse: ...
