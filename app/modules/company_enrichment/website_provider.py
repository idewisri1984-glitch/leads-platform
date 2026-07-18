from collections.abc import Callable, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.modules.company_enrichment.schemas import (
    CompanyEnrichmentProviderResult,
    CompanyEnrichmentTarget,
)
from app.modules.company_enrichment.website_extraction import (
    extract_company_enrichment_from_html,
)
from app.providers.public_web_fetcher import (
    BoundedPublicWebFetcher,
    FetchResponse,
    PublicWebFetchErrorCode,
    ResponseTooLargeError,
    normalize_public_web_request_url,
)
from app.providers.public_web_fetcher import (
    PublicWebTransport as _SafeFetcher,
)

HostnameResolver = Callable[[str], Sequence[str]]
_FetchResponse = FetchResponse
_ResponseTooLarge = ResponseTooLargeError

_RESULT_FIELDS = (
    "email",
    "phone",
    "instagram_url",
    "linkedin_url",
    "contact_page_url",
    "about_page_url",
)
_FETCH_ERROR_MESSAGES = {
    PublicWebFetchErrorCode.HOST_NOT_PUBLIC: "Website host is not public.",
    PublicWebFetchErrorCode.REDIRECT_UNSAFE: "Website redirect was unsafe.",
    PublicWebFetchErrorCode.REDIRECT_LIMIT: "Website redirect limit exceeded.",
    PublicWebFetchErrorCode.REQUEST_FAILED: "Website request failed.",
    PublicWebFetchErrorCode.RESPONSE_TOO_LARGE: "Website response was too large.",
    PublicWebFetchErrorCode.RESPONSE_NOT_HTML: "Website response was not HTML.",
    PublicWebFetchErrorCode.RESPONSE_DECODE_FAILED: "Website request failed.",
}


@dataclass(frozen=True)
class _FetchedPage:
    url: str | None = None
    html: str | None = None
    error: str | None = None


class WebsiteEnrichmentProvider:
    provider_name = "website"

    def __init__(
        self,
        *,
        fetcher: _SafeFetcher | None = None,
        resolver: HostnameResolver | None = None,
        max_pages: int = 3,
        max_response_bytes: int = 250_000,
        timeout_seconds: float = 5.0,
        max_redirects: int = 3,
    ) -> None:
        if max_pages < 1 or max_response_bytes < 1 or timeout_seconds <= 0 or max_redirects < 0:
            raise ValueError("Website provider bounds must be positive.")
        self._web_fetcher = BoundedPublicWebFetcher(
            transport=fetcher,
            resolver=resolver,
            max_response_bytes=max_response_bytes,
            timeout_seconds=timeout_seconds,
            max_redirects=max_redirects,
        )
        self._max_pages = max_pages

    def enrich(self, target: CompanyEnrichmentTarget) -> CompanyEnrichmentProviderResult:
        if target.website is None or not target.website.strip():
            return CompanyEnrichmentProviderResult(
                provider=self.provider_name,
                notes="No website available.",
            )

        try:
            homepage_url = normalize_public_web_request_url(target.website)
        except ValueError:
            homepage_url = None
        if homepage_url is None:
            return self._error_result("Website URL is invalid.")

        homepage = self._fetch(homepage_url)
        if homepage.error is not None or homepage.html is None or homepage.url is None:
            return self._error_result(homepage.error or "Website request failed.", homepage.url)

        parsed_results = [
            extract_company_enrichment_from_html(html=homepage.html, source_url=homepage.url)
        ]
        homepage_host = urlsplit(homepage.url).hostname
        page_candidates = (
            parsed_results[0].contact_page_url,
            parsed_results[0].about_page_url,
        )
        fetched_urls = {homepage_url, homepage.url}
        pages_fetched = 1
        errors: list[str] = []
        for page_url in page_candidates:
            if pages_fetched >= self._max_pages or page_url is None:
                continue
            try:
                normalized_page = normalize_public_web_request_url(page_url)
            except ValueError:
                continue
            if (
                normalized_page is None
                or urlsplit(normalized_page).hostname != homepage_host
                or normalized_page in fetched_urls
            ):
                continue
            fetched_urls.add(normalized_page)
            pages_fetched += 1
            page = self._fetch(normalized_page, allowed_hostname=homepage_host)
            if page.error is not None:
                _append_unique(errors, page.error)
                continue
            if page.url is None or page.html is None:
                continue
            fetched_urls.add(page.url)
            if urlsplit(page.url).hostname != homepage_host:
                _append_unique(errors, "Website redirect was unsafe.")
                continue
            parsed_results.append(
                extract_company_enrichment_from_html(html=page.html, source_url=page.url)
            )
        return self._merge_results(parsed_results, homepage.url, errors)

    def _fetch(self, initial_url: str, *, allowed_hostname: str | None = None) -> _FetchedPage:
        result = self._web_fetcher.fetch(initial_url, allowed_hostname=allowed_hostname)
        if result.error_code is not None:
            return _FetchedPage(
                url=result.final_url,
                error=_FETCH_ERROR_MESSAGES[result.error_code],
            )
        return _FetchedPage(url=result.final_url, html=result.text)

    def _merge_results(
        self,
        results: list[CompanyEnrichmentProviderResult],
        source_url: str,
        fetch_errors: list[str],
    ) -> CompanyEnrichmentProviderResult:
        values: dict[str, str | None] = {}
        errors = list(fetch_errors)
        notes: list[str] = []
        for result in results:
            for field_name in _RESULT_FIELDS:
                if field_name not in values or values[field_name] is None:
                    values[field_name] = getattr(result, field_name)
            for error in result.errors:
                _append_unique(errors, error)
            if result.notes:
                _append_unique(notes, result.notes)
        return CompanyEnrichmentProviderResult(
            provider=self.provider_name,
            source_url=source_url,
            notes=" ".join(notes) or None,
            errors=errors,
            **values,
        )

    def _error_result(
        self,
        message: str,
        source_url: str | None = None,
    ) -> CompanyEnrichmentProviderResult:
        return CompanyEnrichmentProviderResult(
            provider=self.provider_name,
            source_url=source_url,
            errors=[message],
        )


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)
