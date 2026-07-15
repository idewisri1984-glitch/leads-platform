from collections.abc import Callable, Sequence
from pathlib import Path

import httpx
import pytest

from app.modules.company_enrichment.provider_interfaces import EnrichmentProvider
from app.modules.company_enrichment.schemas import (
    CompanyEnrichmentProviderResult,
    CompanyEnrichmentTarget,
)
from app.modules.company_enrichment.website_provider import (
    WebsiteEnrichmentProvider,
    _FetchResponse,
    _ResponseTooLarge,
)

PUBLIC_IP = "93.184.216.34"


def target(website: str | None = "https://example.com") -> CompanyEnrichmentTarget:
    return CompanyEnrichmentTarget(company_id=1, company_name="Example", website=website)


def provider(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    resolver: Callable[[str], Sequence[str]] | None = None,
    **kwargs: object,
) -> WebsiteEnrichmentProvider:
    class MockFetcher:
        def fetch(
            self,
            *,
            url: str,
            hostname: str,
            verified_ip: str,
            timeout: float,
            max_response_bytes: int,
        ) -> _FetchResponse:
            del hostname, verified_ip, timeout
            request = httpx.Request("GET", url)
            try:
                response = handler(request)
            except httpx.HTTPError as exc:
                raise OSError from exc
            body = response.read()
            if len(body) > max_response_bytes:
                raise _ResponseTooLarge
            return _FetchResponse(
                response.status_code,
                {key.casefold(): value for key, value in response.headers.items()},
                body,
            )

    return WebsiteEnrichmentProvider(
        fetcher=MockFetcher(),
        resolver=resolver or (lambda _hostname: [PUBLIC_IP]),
        **kwargs,  # type: ignore[arg-type]
    )


def html_response(request: httpx.Request, html: str, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        headers={"content-type": "text/html; charset=utf-8"},
        text=html,
        request=request,
    )


def test_missing_website_returns_safe_noop_without_request() -> None:
    result = provider(lambda _request: pytest.fail("request not expected")).enrich(target(None))
    assert result == CompanyEnrichmentProviderResult(
        provider="website", notes="No website available."
    )


@pytest.mark.parametrize(
    "website",
    ["not-a-url", "ftp://example.com", "https://user:pass@example.com"],
)
def test_invalid_website_returns_safe_error_without_request(website: str) -> None:
    result = provider(lambda _request: pytest.fail("request not expected")).enrich(target(website))
    assert result.errors == ["Website URL is invalid."]


def test_homepage_is_fetched_parsed_and_normalized() -> None:
    result = provider(
        lambda request: html_response(
            request,
            """
            Sales@EXAMPLE.COM +1 212 555 0199
            <a href="https://instagram.com/example">Instagram</a>
            <a href="https://linkedin.com/company/example">LinkedIn</a>
            <a href="/contact-us">Contact</a><a href="/about-us">About</a>
            """,
        ),
        max_pages=1,
    ).enrich(target("https://EXAMPLE.com/"))
    assert result.provider == "website"
    assert result.source_url == "https://example.com"
    assert result.email == "Sales@example.com"
    assert result.phone == "+1 212 555 0199"
    assert result.instagram_url == "https://instagram.com/example"
    assert result.linkedin_url == "https://linkedin.com/company/example"
    assert result.contact_page_url == "https://example.com/contact-us"
    assert result.about_page_url == "https://example.com/about-us"


def test_contact_and_about_pages_fill_missing_fields() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.path == "/contact":
            return html_response(request, '<a href="mailto:hello@example.com">Email</a>')
        if request.url.path == "/about":
            return html_response(request, "Call +1 646 555 0100")
        return html_response(request, '<a href="/contact">Contact</a><a href="/about">About</a>')

    result = provider(handler).enrich(target())
    assert requested == [
        "https://example.com",
        "https://example.com/contact",
        "https://example.com/about",
    ]
    assert result.email == "hello@example.com"
    assert result.phone == "+1 646 555 0100"


def test_first_valid_values_across_pages_win() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/contact":
            return html_response(request, "second@example.com +1 646 555 0100")
        return html_response(
            request,
            'first@example.com +1 212 555 0199 <a href="/contact">Contact</a>',
        )

    result = provider(handler).enrich(target())
    assert result.email == "first@example.com"
    assert result.phone == "+1 212 555 0199"


def test_max_pages_is_respected() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return html_response(request, '<a href="/contact">Contact</a><a href="/about">About</a>')

    provider(handler, max_pages=2).enrich(target())
    assert requested == ["https://example.com", "https://example.com/contact"]


def test_duplicate_contact_and_about_url_is_fetched_once() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return html_response(request, '<a href="/company">Contact company about</a>')

    provider(handler).enrich(target())
    assert requested == ["https://example.com", "https://example.com/company"]


@pytest.mark.parametrize(
    "website",
    [
        "http://localhost",
        "http://127.0.0.1",
        "http://10.0.0.1",
        "http://169.254.1.1",
        "http://224.0.0.1",
        "http://0.0.0.0",
        "http://240.0.0.1",
        "http://[::1]",
        "http://[fe80::1]",
        "http://[fc00::1]",
    ],
)
def test_non_public_hosts_are_rejected_without_request(website: str) -> None:
    result = provider(lambda _request: pytest.fail("request not expected")).enrich(target(website))
    assert result.errors in (["Website host is not public."], ["Website URL is invalid."])


def test_dns_resolution_to_private_address_is_rejected() -> None:
    result = provider(
        lambda _request: pytest.fail("request not expected"),
        resolver=lambda _hostname: ["192.168.1.2"],
    ).enrich(target())
    assert result.errors == ["Website host is not public."]


def test_any_private_dns_answer_rejects_host() -> None:
    result = provider(
        lambda _request: pytest.fail("request not expected"),
        resolver=lambda _hostname: [PUBLIC_IP, "127.0.0.1"],
    ).enrich(target())
    assert result.errors == ["Website host is not public."]


def test_public_dns_answer_is_accepted() -> None:
    result = provider(lambda request: html_response(request, "hello@example.com")).enrich(target())
    assert result.email == "hello@example.com"


@pytest.mark.parametrize(
    "address",
    [
        "100.64.0.1",
        "192.0.2.1",
        "198.51.100.1",
        "203.0.113.1",
        "fc00::1",
        "::1",
        "fe80::1",
    ],
)
def test_non_global_dns_addresses_are_rejected(address: str) -> None:
    result = provider(
        lambda _request: pytest.fail("request not expected"),
        resolver=lambda _hostname: [address],
    ).enrich(target())
    assert result.errors == ["Website host is not public."]


@pytest.mark.parametrize("address", [PUBLIC_IP, "2606:2800:220:1:248:1893:25c8:1946"])
def test_global_dns_addresses_are_accepted(address: str) -> None:
    result = provider(
        lambda request: html_response(request, "global@example.com"),
        resolver=lambda _hostname: [address],
    ).enrich(target())
    assert result.email == "global@example.com"


def test_fetcher_receives_verified_ip_and_original_hostname() -> None:
    calls: list[tuple[str, str, str]] = []

    class RecordingFetcher:
        def fetch(self, **kwargs: object) -> _FetchResponse:
            calls.append((str(kwargs["url"]), str(kwargs["hostname"]), str(kwargs["verified_ip"])))
            return _FetchResponse(200, {"content-type": "text/html"}, b"<html></html>")

    website_provider = WebsiteEnrichmentProvider(
        fetcher=RecordingFetcher(),
        resolver=lambda _hostname: [PUBLIC_IP],
    )
    website_provider.enrich(target())
    assert calls == [("https://example.com", "example.com", PUBLIC_IP)]


def test_redirect_target_is_resolved_and_pinned_separately() -> None:
    resolutions: list[str] = []
    calls: list[tuple[str, str]] = []

    def resolver(hostname: str) -> Sequence[str]:
        resolutions.append(hostname)
        return {"example.com": [PUBLIC_IP], "www.example.com": ["8.8.8.8"]}[hostname]

    class RedirectFetcher:
        def fetch(self, **kwargs: object) -> _FetchResponse:
            url = str(kwargs["url"])
            calls.append((url, str(kwargs["verified_ip"])))
            if url == "https://example.com":
                return _FetchResponse(302, {"location": "https://www.example.com/home"}, b"")
            return _FetchResponse(200, {"content-type": "text/html"}, b"<html></html>")

    WebsiteEnrichmentProvider(
        fetcher=RedirectFetcher(),
        resolver=resolver,
    ).enrich(target())
    assert resolutions == ["example.com", "www.example.com"]
    assert calls == [
        ("https://example.com", PUBLIC_IP),
        ("https://www.example.com/home", "8.8.8.8"),
    ]


def test_unsafe_redirect_target_never_reaches_fetcher() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1"}, request=request)

    provider(handler).enrich(target())
    assert requested == ["https://example.com"]


@pytest.mark.parametrize(
    "location",
    ["http://127.0.0.1/private", "https://user:pass@example.com/private"],
)
def test_unsafe_homepage_redirect_is_rejected(location: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": location}, request=request)

    result = provider(handler).enrich(target())
    assert result.errors == ["Website redirect was unsafe."]


def test_redirect_limit_is_enforced() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        number = int(request.url.params.get("n", "0"))
        return httpx.Response(302, headers={"location": f"/?n={number + 1}"}, request=request)

    result = provider(handler, max_redirects=2).enrich(target())
    assert result.errors == ["Website redirect limit exceeded."]


def test_safe_homepage_redirect_is_followed_and_revalidated() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.host == "example.com":
            return httpx.Response(
                302, headers={"location": "https://www.example.com/home"}, request=request
            )
        return html_response(request, "redirected@example.com")

    result = provider(handler).enrich(target())
    assert requested == ["https://example.com", "https://www.example.com/home"]
    assert result.source_url == "https://www.example.com/home"
    assert result.email == "redirected@example.com"


def test_request_error_returns_safe_message_without_exception_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("API_KEY=secret database-url traceback", request=request)

    result = provider(handler).enrich(target())
    rendered = result.model_dump_json()
    assert result.errors == ["Website request failed."]
    assert "secret" not in rendered
    assert "traceback" not in rendered.casefold()


@pytest.mark.parametrize(
    "content_type",
    ["application/pdf", "image/png", "text/css", "application/zip", "video/mp4"],
)
def test_non_html_content_is_rejected(content_type: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": content_type},
            content=b"<html>ignored</html>",
            request=request,
        )

    result = provider(handler).enrich(target())
    assert result.errors == ["Website response was not HTML."]


def test_missing_content_type_accepts_html_looking_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"<!doctype html><p>info@example.com</p>", request=request
        )

    assert provider(handler).enrich(target()).email == "info@example.com"


def test_missing_content_type_rejects_non_html_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"plain data", request=request)

    assert provider(handler).enrich(target()).errors == ["Website response was not HTML."]


def test_response_size_limit_is_enforced_without_returning_body() -> None:
    marker = "API_KEY=secret"

    def handler(request: httpx.Request) -> httpx.Response:
        return html_response(request, f"<html>{marker * 20}</html>")

    result = provider(handler, max_response_bytes=32).enrich(target())
    assert result.errors == ["Website response was too large."]
    assert marker not in result.model_dump_json()


@pytest.mark.parametrize("status", [400, 404, 500, 503])
def test_http_error_status_returns_safe_message(status: int) -> None:
    result = provider(lambda request: html_response(request, "secret body", status)).enrich(
        target()
    )
    assert result.errors == ["Website request failed."]


def test_offsite_contact_page_and_social_links_are_never_fetched() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return html_response(
            request,
            """
            <a href="https://other.example/contact">Contact</a>
            <a href="https://instagram.com/example">Instagram</a>
            <a href="https://linkedin.com/company/example">LinkedIn</a>
            """,
        )

    result = provider(handler).enrich(target())
    assert requested == ["https://example.com"]
    assert result.instagram_url == "https://instagram.com/example"
    assert result.linkedin_url == "https://linkedin.com/company/example"


@pytest.mark.parametrize("href", ["javascript:alert(1)", "mailto:x@y.com", "tel:1234567", "data:x"])
def test_unsafe_page_links_are_not_fetched(href: str) -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return html_response(request, f'<a href="{href}">Contact</a>')

    provider(handler).enrich(target())
    assert requested == ["https://example.com"]


def test_offsite_redirect_from_same_site_subpage_is_not_fetched() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.path == "/contact":
            return httpx.Response(
                302, headers={"location": "https://other.example/contact"}, request=request
            )
        return html_response(request, '<a href="/contact">Contact</a>')

    result = provider(handler).enrich(target())
    assert requested == ["https://example.com", "https://example.com/contact"]
    assert "Website redirect was unsafe." in result.errors


def test_redirect_final_url_is_not_fetched_again_as_page_candidate() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.path == "/contact":
            return httpx.Response(302, headers={"location": "/about"}, request=request)
        if request.url.path == "/about":
            return html_response(request, "about@example.com")
        return html_response(request, '<a href="/contact">Contact</a><a href="/about">About</a>')

    result = provider(handler).enrich(target())
    assert requested == [
        "https://example.com",
        "https://example.com/contact",
        "https://example.com/about",
    ]
    assert result.email == "about@example.com"


def test_parser_errors_and_safe_notes_are_preserved() -> None:
    result = provider(
        lambda request: html_response(request, "<html>hello@example.com</html>"),
        max_response_bytes=250_000,
    ).enrich(target())
    assert result.notes == "Static website enrichment parsed."
    assert result.errors == []


def test_result_contains_no_raw_html_headers_or_settings() -> None:
    raw_html = "<html><script>API_KEY=secret</script>hello@example.com</html>"
    result = provider(lambda request: html_response(request, raw_html)).enrich(target())
    rendered = result.model_dump_json()
    assert isinstance(result, CompanyEnrichmentProviderResult)
    assert raw_html not in rendered
    assert "API_KEY" not in rendered
    assert "DATABASE_URL" not in rendered
    assert "Settings(" not in rendered


def test_provider_implements_protocol_and_receives_target_only() -> None:
    website_provider = provider(lambda request: html_response(request, "<html></html>"))
    assert isinstance(website_provider, EnrichmentProvider)
    assert website_provider.provider_name == "website"
    assert WebsiteEnrichmentProvider.enrich.__annotations__["target"] is CompanyEnrichmentTarget


def test_provider_module_has_no_forbidden_imports_or_cli_or_orm_boundary() -> None:
    source = Path("app/modules/company_enrichment/website_provider.py").read_text(encoding="utf-8")
    forbidden = (
        "serpapi",
        "selenium",
        "playwright",
        "instagram_api",
        "linkedin_api",
        "app.cli",
        "company_enrichment.models",
        "CompanyIngestionService",
        "openai",
    )
    assert all(marker.casefold() not in source.casefold() for marker in forbidden)
