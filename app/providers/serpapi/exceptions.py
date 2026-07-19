class SerpApiError(Exception):
    """Base controlled exception for SerpAPI provider failures."""


class SerpApiConfigurationError(SerpApiError):
    """SerpAPI provider is not configured for use."""


class SerpApiRequestError(SerpApiError):
    """SerpAPI request failed before a usable response was parsed."""


class SerpApiRateLimitError(SerpApiRequestError):
    """SerpAPI rejected the request due to rate limiting."""


class SerpApiAuthenticationError(SerpApiError):
    """SerpAPI rejected the configured credentials."""


class SerpApiQuotaExceededError(SerpApiRateLimitError):
    """SerpAPI account search quota was exhausted."""


class SerpApiResponseError(SerpApiError):
    """SerpAPI returned an unexpected or malformed response body."""


class SerpApiResponseTooLargeError(SerpApiResponseError):
    """SerpAPI returned a response beyond the configured byte bound."""


class SerpApiProviderError(SerpApiError):
    """SerpAPI reported a controlled provider-side failure."""
