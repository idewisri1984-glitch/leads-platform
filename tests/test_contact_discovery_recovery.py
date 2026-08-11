from collections.abc import Callable, Sequence
from typing import Any

import pytest

from app.modules.contact_discovery.website_provider import (
    CONTACT_DISCOVERY_MAX_RESPONSE_BYTES,
    MAX_SEARCH_PAGES,
    MAX_SEARCH_QUERIES,
    MAX_SEARCH_RESULTS_PER_QUERY,
    ContactSearchResult,
    WebsiteContactDiscoveryProvider,
)
from app.providers.public_web_fetcher import (
    BoundedPublicWebFetcher,
    FetchResponse,
    PublicWebFetchErrorCode,
    PublicWebFetchResult,
    ResponseTooLargeError,
)

PUBLIC_IP = "93.184.216.34"


def person_html(*, email: str | None = "ada@example.com") -> str:
    mailbox = f'<a href="mailto:{email}">{email}</a>' if email else ""
    return f'<div class="person"><h3>Ada Lovelace</h3><p class="role">Founder</p>{mailbox}</div>'


def fetched(url: str, html: str) -> PublicWebFetchResult:
    return PublicWebFetchResult(final_url=url, text=html, content_type="text/html")


class FakeFetcher:
    def __init__(self, handler: Callable[[str], PublicWebFetchResult]) -> None:
        self.handler = handler
        self.calls: list[str] = []

    def fetch(self, url: str, *, allowed_hostname: str | None = None) -> PublicWebFetchResult:
        self.calls.append(url)
        return self.handler(url)


class FakeSearchProvider:
    def __init__(self, results: Sequence[ContactSearchResult]) -> None:
        self.results = results
        self.calls: list[tuple[str, int]] = []

    def search(self, *, query: str, limit: int) -> Sequence[ContactSearchResult]:
        self.calls.append((query, limit))
        return self.results


def failure(url: str, code: PublicWebFetchErrorCode) -> PublicWebFetchResult:
    return PublicWebFetchResult(final_url=url, error_code=code)


def test_deep_url_failure_retries_canonical_root_once_and_recovers_candidate() -> None:
    deep = "https://example.com/projects/all/?view=large"
    root = "https://example.com/"
    fetcher = FakeFetcher(
        lambda url: (
            failure(url, PublicWebFetchErrorCode.RESPONSE_TOO_LARGE)
            if url == deep
            else fetched(root, person_html())
        )
    )
    result = WebsiteContactDiscoveryProvider(fetcher=fetcher).discover(
        company_id=7, website_url=deep
    )
    assert fetcher.calls == [deep, root]
    assert result.attempted_pages == 2
    assert result.successful_pages == 1
    assert result.candidates[0].email == "ada@example.com"
    assert result.diagnostics == ("configured_url_response_too_large",)


def test_root_failure_does_not_retry_same_root() -> None:
    root = "https://example.com/"
    fetcher = FakeFetcher(lambda url: failure(url, PublicWebFetchErrorCode.REQUEST_FAILED))
    result = WebsiteContactDiscoveryProvider(fetcher=fetcher).discover(
        company_id=1, website_url=root
    )
    assert fetcher.calls == [root]
    assert result.errors == ("homepage_fetch_failed",)
    assert result.diagnostics == (
        "configured_url_request_failed",
        "search_fallback_unavailable",
    )


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (PublicWebFetchErrorCode.RESPONSE_TOO_LARGE, "configured_url_response_too_large"),
        (PublicWebFetchErrorCode.REQUEST_FAILED, "configured_url_request_failed"),
        (PublicWebFetchErrorCode.HOST_NOT_PUBLIC, "configured_url_host_not_public"),
    ],
)
def test_fetch_failure_category_is_preserved(code: PublicWebFetchErrorCode, expected: str) -> None:
    root = "https://example.com/"
    result = WebsiteContactDiscoveryProvider(
        fetcher=FakeFetcher(lambda url: failure(url, code))
    ).discover(company_id=1, website_url=root)
    assert result.diagnostics[0] == expected


def test_contact_specific_large_response_limit_is_bounded_and_larger_than_baseline() -> None:
    body = ("<html>" + person_html() + " " * 300_000 + "</html>").encode()

    class BoundedTransport:
        def fetch(self, **kwargs: Any) -> FetchResponse:
            if len(body) > kwargs["max_response_bytes"]:
                raise ResponseTooLargeError
            return FetchResponse(200, {"content-type": "text/html"}, body)

    baseline = BoundedPublicWebFetcher(
        transport=BoundedTransport(),
        resolver=lambda _hostname: [PUBLIC_IP],
        max_response_bytes=250_000,
    )
    recovery = BoundedPublicWebFetcher(
        transport=BoundedTransport(),
        resolver=lambda _hostname: [PUBLIC_IP],
        max_response_bytes=CONTACT_DISCOVERY_MAX_RESPONSE_BYTES,
    )
    assert baseline.fetch("https://example.com/").error_code == (
        PublicWebFetchErrorCode.RESPONSE_TOO_LARGE
    )
    assert recovery.fetch("https://example.com/").error_code is None
    assert CONTACT_DISCOVERY_MAX_RESPONSE_BYTES == 750_000


def test_response_beyond_contact_hard_limit_still_fails_without_partial_body() -> None:
    class OversizedTransport:
        def fetch(self, **kwargs: Any) -> FetchResponse:
            assert kwargs["max_response_bytes"] == CONTACT_DISCOVERY_MAX_RESPONSE_BYTES
            raise ResponseTooLargeError

    fetcher = BoundedPublicWebFetcher(
        transport=OversizedTransport(),
        resolver=lambda _hostname: [PUBLIC_IP],
        max_response_bytes=CONTACT_DISCOVERY_MAX_RESPONSE_BYTES,
    )
    result = fetcher.fetch("https://example.com/")
    assert result.error_code == PublicWebFetchErrorCode.RESPONSE_TOO_LARGE
    assert result.text is None


def test_failed_website_uses_bounded_search_and_returns_sourced_email() -> None:
    root = "https://example.com/"
    team = "https://example.com/team"
    fetcher = FakeFetcher(
        lambda url: (
            fetched(team, person_html())
            if url == team
            else failure(url, PublicWebFetchErrorCode.REQUEST_FAILED)
        )
    )
    search = FakeSearchProvider([ContactSearchResult(url=team)])
    result = WebsiteContactDiscoveryProvider(fetcher=fetcher, search_provider=search).discover(
        company_id=4, website_url=root
    )
    assert len(search.calls) == 1
    assert all(limit == MAX_SEARCH_RESULTS_PER_QUERY for _, limit in search.calls)
    assert result.candidates[0].email == "ada@example.com"
    assert result.candidates[0].source_url == team
    assert result.search_queries == 1


def test_search_source_with_person_but_no_email_never_fabricates_email() -> None:
    root = "https://example.com/"
    team = "https://example.com/team"
    fetcher = FakeFetcher(
        lambda url: (
            fetched(team, person_html(email=None))
            if url == team
            else failure(url, PublicWebFetchErrorCode.REQUEST_FAILED)
        )
    )
    result = WebsiteContactDiscoveryProvider(
        fetcher=fetcher,
        search_provider=FakeSearchProvider([ContactSearchResult(url=team)]),
    ).discover(company_id=4, website_url=root)
    assert result.candidates
    assert all(candidate.email is None for candidate in result.candidates)


def test_unsafe_and_foreign_search_urls_are_rejected_before_fetch() -> None:
    root = "https://example.com/"
    fetcher = FakeFetcher(lambda url: failure(url, PublicWebFetchErrorCode.REQUEST_FAILED))
    search = FakeSearchProvider(
        [
            ContactSearchResult(url="http://127.0.0.1/private"),
            ContactSearchResult(url="http://localhost/private"),
            ContactSearchResult(url="https://other.example/team"),
        ]
    )
    result = WebsiteContactDiscoveryProvider(fetcher=fetcher, search_provider=search).discover(
        company_id=4, website_url=root
    )
    assert fetcher.calls == [root]
    assert result.candidates == ()
    assert "search_url_rejected" in result.diagnostics


def test_search_queries_results_and_page_fetches_have_strict_ceilings() -> None:
    root = "https://example.com/"
    results = [ContactSearchResult(url=f"https://example.com/team/{index}") for index in range(20)]
    fetcher = FakeFetcher(lambda url: failure(url, PublicWebFetchErrorCode.REQUEST_FAILED))
    search = FakeSearchProvider(results)
    result = WebsiteContactDiscoveryProvider(fetcher=fetcher, search_provider=search).discover(
        company_id=4, website_url=root
    )
    assert len(search.calls) <= MAX_SEARCH_QUERIES
    assert all(limit == MAX_SEARCH_RESULTS_PER_QUERY for _, limit in search.calls)
    assert result.search_results <= MAX_SEARCH_QUERIES * MAX_SEARCH_RESULTS_PER_QUERY
    assert len(fetcher.calls) - 1 == MAX_SEARCH_PAGES


def test_normal_website_success_does_not_invoke_search() -> None:
    root = "https://example.com/"
    search = FakeSearchProvider([ContactSearchResult(url="https://example.com/team")])
    result = WebsiteContactDiscoveryProvider(
        fetcher=FakeFetcher(lambda url: fetched(url, person_html())),
        search_provider=search,
    ).discover(company_id=1, website_url=root)
    assert result.candidates
    assert search.calls == []
    assert result.search_queries == 0


def test_missing_search_provider_preserves_safe_website_only_not_found() -> None:
    root = "https://example.com/"
    result = WebsiteContactDiscoveryProvider(
        fetcher=FakeFetcher(lambda url: fetched(url, "<html></html>"))
    ).discover(company_id=1, website_url=root)
    assert result.candidates == ()
    assert result.errors == ()
    assert result.search_queries == 0
    assert result.diagnostics == ("search_fallback_unavailable",)
