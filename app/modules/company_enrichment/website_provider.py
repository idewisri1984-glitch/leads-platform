import ipaddress
import socket
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from app.modules.company_enrichment.normalization import normalize_public_url
from app.modules.company_enrichment.schemas import (
    CompanyEnrichmentProviderResult,
    CompanyEnrichmentTarget,
)
from app.modules.company_enrichment.website_extraction import (
    extract_company_enrichment_from_html,
)

HostnameResolver = Callable[[str], Sequence[str]]

_RESULT_FIELDS = (
    "email",
    "phone",
    "instagram_url",
    "linkedin_url",
    "contact_page_url",
    "about_page_url",
)
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_HTML_CONTENT_TYPES = {"text/html", "application/xhtml+xml"}


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
        client: httpx.Client | None = None,
        resolver: HostnameResolver | None = None,
        max_pages: int = 3,
        max_response_bytes: int = 250_000,
        timeout_seconds: float = 5.0,
        max_redirects: int = 3,
    ) -> None:
        if max_pages < 1 or max_response_bytes < 1 or timeout_seconds <= 0 or max_redirects < 0:
            raise ValueError("Website provider bounds must be positive.")
        self._client = client
        self._resolver = resolver or _resolve_hostname
        self._max_pages = max_pages
        self._max_response_bytes = max_response_bytes
        self._timeout_seconds = timeout_seconds
        self._max_redirects = max_redirects

    def enrich(self, target: CompanyEnrichmentTarget) -> CompanyEnrichmentProviderResult:
        if target.website is None or not target.website.strip():
            return CompanyEnrichmentProviderResult(
                provider=self.provider_name,
                notes="No website available.",
            )

        try:
            homepage_url = normalize_public_url(target.website)
        except ValueError:
            homepage_url = None
        if homepage_url is None:
            return self._error_result("Website URL is invalid.")
        if not self._is_public_url(homepage_url):
            return self._error_result("Website host is not public.", source_url=homepage_url)

        owns_client = self._client is None
        client = self._client or httpx.Client(follow_redirects=False)
        try:
            homepage = self._fetch(client, homepage_url)
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
            fetched_urls = {homepage.url}
            errors: list[str] = []
            for page_url in page_candidates:
                if len(fetched_urls) >= self._max_pages or page_url is None:
                    continue
                try:
                    normalized_page = normalize_public_url(page_url)
                except ValueError:
                    continue
                if (
                    normalized_page is None
                    or urlsplit(normalized_page).hostname != homepage_host
                    or normalized_page in fetched_urls
                    or not self._is_public_url(normalized_page)
                ):
                    continue
                fetched_urls.add(normalized_page)
                page = self._fetch(client, normalized_page, allowed_hostname=homepage_host)
                if page.error is not None:
                    _append_unique(errors, page.error)
                    continue
                if page.url is None or page.html is None:
                    continue
                if urlsplit(page.url).hostname != homepage_host:
                    _append_unique(errors, "Website redirect was unsafe.")
                    continue
                parsed_results.append(
                    extract_company_enrichment_from_html(html=page.html, source_url=page.url)
                )
            return self._merge_results(parsed_results, homepage.url, errors)
        finally:
            if owns_client:
                client.close()

    def _fetch(
        self,
        client: httpx.Client,
        initial_url: str,
        *,
        allowed_hostname: str | None = None,
    ) -> _FetchedPage:
        current_url = initial_url
        redirects = 0
        while True:
            if not self._is_public_url(current_url):
                message = (
                    "Website redirect was unsafe." if redirects else "Website host is not public."
                )
                return _FetchedPage(url=current_url, error=message)
            try:
                with client.stream(
                    "GET",
                    current_url,
                    timeout=self._timeout_seconds,
                    headers={"Accept": "text/html, application/xhtml+xml"},
                    follow_redirects=False,
                ) as response:
                    if response.status_code in _REDIRECT_STATUSES:
                        location = response.headers.get("location")
                        if not location:
                            return _FetchedPage(url=current_url, error="Website request failed.")
                        if redirects >= self._max_redirects:
                            return _FetchedPage(
                                url=current_url,
                                error="Website redirect limit exceeded.",
                            )
                        try:
                            redirect_url = normalize_public_url(urljoin(current_url, location))
                        except ValueError:
                            redirect_url = None
                        if (
                            redirect_url is None
                            or not self._is_public_url(redirect_url)
                            or (
                                allowed_hostname is not None
                                and urlsplit(redirect_url).hostname != allowed_hostname
                            )
                        ):
                            return _FetchedPage(
                                url=current_url,
                                error="Website redirect was unsafe.",
                            )
                        current_url = redirect_url
                        redirects += 1
                        continue
                    if response.status_code < 200 or response.status_code >= 300:
                        return _FetchedPage(url=current_url, error="Website request failed.")

                    content_type = response.headers.get("content-type", "").split(";", 1)[0]
                    content_type = content_type.strip().casefold()
                    if content_type and content_type not in _HTML_CONTENT_TYPES:
                        return _FetchedPage(url=current_url, error="Website response was not HTML.")
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > self._max_response_bytes:
                            return _FetchedPage(
                                url=current_url,
                                error="Website response was too large.",
                            )
                    if not content_type and not _looks_like_html(bytes(body)):
                        return _FetchedPage(url=current_url, error="Website response was not HTML.")
                    return _FetchedPage(
                        url=current_url,
                        html=bytes(body).decode(response.encoding or "utf-8", errors="replace"),
                    )
            except httpx.HTTPError:
                return _FetchedPage(url=current_url, error="Website request failed.")

    def _is_public_url(self, url: str) -> bool:
        try:
            normalized = normalize_public_url(url)
        except ValueError:
            return False
        if normalized is None:
            return False
        hostname = urlsplit(normalized).hostname
        if hostname is None or hostname.casefold() == "localhost":
            return False
        try:
            direct_address = ipaddress.ip_address(hostname)
        except ValueError:
            direct_address = None
        if direct_address is not None:
            return _is_public_address(direct_address)
        try:
            addresses = self._resolver(hostname)
        except (OSError, ValueError):
            return False
        if not addresses:
            return False
        try:
            return all(_is_public_address(ipaddress.ip_address(address)) for address in addresses)
        except ValueError:
            return False

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


def _resolve_hostname(hostname: str) -> Sequence[str]:
    return tuple(
        {str(item[4][0]) for item in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)}
    )


def _is_public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _looks_like_html(body: bytes) -> bool:
    beginning = bytes(body[:512]).lstrip().lower()
    return beginning.startswith((b"<!doctype html", b"<html", b"<head", b"<body"))


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)
