class SerpApiError(Exception):
    """
    Base controlled exception for SerpAPI provider failures.
    """


class SerpApiConfigurationError(SerpApiError):
    """
    SerpAPI provider is not configured for use.
    """


class SerpApiRequestError(SerpApiError):
    """
    SerpAPI request failed before a usable response was parsed.
    """


class SerpApiRateLimitError(SerpApiRequestError):
    """
    SerpAPI rejected the request due to rate limiting.
    """


class SerpApiResponseError(SerpApiError):
    """
    SerpAPI returned an unexpected or malformed response body.
    """
