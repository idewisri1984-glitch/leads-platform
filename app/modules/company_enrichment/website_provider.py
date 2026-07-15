import http.client
import ipaddress
import socket
import ssl
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urljoin, urlsplit

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
class _FetchResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes


class _SafeFetcher(Protocol):
    def fetch(
        self,
        *,
        url: str,
        hostname: str,
        verified_ip: str,
        timeout: float,
        max_response_bytes: int,
    ) -> _FetchResponse: ...


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, hostname: str, verified_ip: str, port: int, timeout: float) -> None:
        super().__init__(hostname, port=port, timeout=timeout)
        self._verified_ip = verified_ip

    def connect(self) -> None:
        self.sock = socket.create_connection((self._verified_ip, self.port), self.timeout)


class _PinnedHTTPSConnection(_PinnedHTTPConnection):
    def __init__(self, hostname: str, verified_ip: str, port: int, timeout: float) -> None:
        super().__init__(hostname, verified_ip, port, timeout)
        self._context = ssl.create_default_context()

    def connect(self) -> None:
        super().connect()
        assert self.sock is not None
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


class _PinnedFetcher:
    def fetch(
        self,
        *,
        url: str,
        hostname: str,
        verified_ip: str,
        timeout: float,
        max_response_bytes: int,
    ) -> _FetchResponse:
        parsed = urlsplit(url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        connection_type = (
            _PinnedHTTPSConnection if parsed.scheme == "https" else _PinnedHTTPConnection
        )
        connection = connection_type(hostname, verified_ip, port, timeout)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        host_header = f"[{hostname}]" if ":" in hostname else hostname
        if parsed.port is not None and parsed.port != (443 if parsed.scheme == "https" else 80):
            host_header = f"{hostname}:{parsed.port}"
        try:
            connection.request(
                "GET",
                path,
                headers={
                    "Accept": "text/html, application/xhtml+xml",
                    "Host": host_header,
                },
            )
            response = connection.getresponse()
            body = response.read(max_response_bytes + 1)
            if len(body) > max_response_bytes:
                raise _ResponseTooLarge
            return _FetchResponse(
                status_code=response.status,
                headers={key.casefold(): value for key, value in response.getheaders()},
                body=body,
            )
        finally:
            connection.close()


class _ResponseTooLarge(Exception):
    pass


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
        self._fetcher = fetcher or _PinnedFetcher()
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
                normalized_page = normalize_public_url(page_url)
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
        current_url = initial_url
        redirects = 0
        while True:
            verified = self._verified_address(current_url)
            if verified is None:
                message = (
                    "Website redirect was unsafe." if redirects else "Website host is not public."
                )
                return _FetchedPage(url=current_url, error=message)
            hostname, verified_ip = verified
            try:
                response = self._fetcher.fetch(
                    url=current_url,
                    hostname=hostname,
                    verified_ip=verified_ip,
                    timeout=self._timeout_seconds,
                    max_response_bytes=self._max_response_bytes,
                )
            except _ResponseTooLarge:
                return _FetchedPage(url=current_url, error="Website response was too large.")
            except (OSError, ssl.SSLError, http.client.HTTPException):
                return _FetchedPage(url=current_url, error="Website request failed.")

            if response.status_code in _REDIRECT_STATUSES:
                location = response.headers.get("location")
                if not location:
                    return _FetchedPage(url=current_url, error="Website request failed.")
                if redirects >= self._max_redirects:
                    return _FetchedPage(url=current_url, error="Website redirect limit exceeded.")
                try:
                    redirect_url = normalize_public_url(urljoin(current_url, location))
                except ValueError:
                    redirect_url = None
                if redirect_url is None or (
                    allowed_hostname is not None
                    and urlsplit(redirect_url).hostname != allowed_hostname
                ):
                    return _FetchedPage(url=current_url, error="Website redirect was unsafe.")
                current_url = redirect_url
                redirects += 1
                continue
            if response.status_code < 200 or response.status_code >= 300:
                return _FetchedPage(url=current_url, error="Website request failed.")

            content_type = response.headers.get("content-type", "").split(";", 1)[0]
            content_type = content_type.strip().casefold()
            if content_type and content_type not in _HTML_CONTENT_TYPES:
                return _FetchedPage(url=current_url, error="Website response was not HTML.")
            if not content_type and not _looks_like_html(response.body):
                return _FetchedPage(url=current_url, error="Website response was not HTML.")
            charset = _response_charset(response.headers.get("content-type", ""))
            return _FetchedPage(
                url=current_url,
                html=response.body.decode(charset, errors="replace"),
            )

    def _verified_address(self, url: str) -> tuple[str, str] | None:
        try:
            normalized = normalize_public_url(url)
        except ValueError:
            return None
        if normalized is None:
            return None
        hostname = urlsplit(normalized).hostname
        if hostname is None or hostname.casefold() == "localhost":
            return None
        try:
            direct_address = ipaddress.ip_address(hostname)
        except ValueError:
            direct_address = None
        if direct_address is not None:
            return (hostname, str(direct_address)) if _is_public_address(direct_address) else None
        try:
            addresses = tuple(ipaddress.ip_address(value) for value in self._resolver(hostname))
        except (OSError, ValueError):
            return None
        if not addresses or not all(_is_public_address(address) for address in addresses):
            return None
        return hostname, str(addresses[0])

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
    return address.is_global and not address.is_multicast


def _response_charset(content_type: str) -> str:
    for part in content_type.split(";")[1:]:
        key, separator, value = part.strip().partition("=")
        if separator and key.casefold() == "charset":
            return value.strip("\"'") or "utf-8"
    return "utf-8"


def _looks_like_html(body: bytes) -> bool:
    beginning = bytes(body[:512]).lstrip().lower()
    return beginning.startswith((b"<!doctype html", b"<html", b"<head", b"<body"))


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)
