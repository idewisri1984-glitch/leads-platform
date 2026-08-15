from dataclasses import dataclass
from enum import StrEnum


class SerpApiError(Exception):
    """Base controlled exception for SerpAPI provider failures."""


class SerpApiConfigurationError(SerpApiError):
    """SerpAPI provider is not configured for use."""


class SerpApiRequestFailureSubtype(StrEnum):
    """Bounded request failure classes safe for operator diagnostics."""

    TRANSPORT = "TRANSPORT"
    HTTP_CLIENT = "HTTP_CLIENT"
    VALIDATION = "VALIDATION"


@dataclass(frozen=True, slots=True)
class SerpApiDiagnostic:
    """Allowlisted SerpAPI request metadata without raw provider data."""

    category: str
    subtype: SerpApiRequestFailureSubtype
    http_status: int | None = None
    provider_code: str = "request_error"

    def __post_init__(self) -> None:
        if self.category != "request_error" or self.provider_code != "request_error":
            raise ValueError("SerpAPI diagnostic category is invalid.")
        if self.http_status is not None and not 400 <= self.http_status <= 499:
            raise ValueError("SerpAPI diagnostic HTTP status is invalid.")


class SerpApiRequestError(SerpApiError):
    """SerpAPI request failed before a usable response was parsed."""

    def __init__(
        self,
        message: str,
        *,
        subtype: SerpApiRequestFailureSubtype | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.diagnostic = (
            SerpApiDiagnostic(
                category="request_error",
                subtype=subtype,
                http_status=http_status,
            )
            if subtype is not None
            else None
        )


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
