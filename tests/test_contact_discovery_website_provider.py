from collections.abc import Callable
from typing import Any

import pytest

from app.modules.contact_discovery import website_provider
from app.modules.contact_discovery.models import ContactDiscoverySourceType
from app.modules.contact_discovery.schemas import ContactDiscoveryCandidateCreate
from app.modules.contact_discovery.website_contact_parser import (
    parse_contact_discovery_candidates_from_html,
)
from app.modules.contact_discovery.website_provider import (
    WebsiteContactDiscoveryProvider,
    WebsiteContactDiscoveryProviderResult,
)
from app.providers.public_web_fetcher import PublicWebFetchErrorCode, PublicWebFetchResult

HOME = "https://example.com"


class FakeFetcher:
    def __init__(self, handler: Callable[[str, str | None], PublicWebFetchResult]) -> None:
        self.handler = handler
        self.calls: list[tuple[str, str | None]] = []

    def fetch(self, url: str, *, allowed_hostname: str | None = None) -> PublicWebFetchResult:
        self.calls.append((url, allowed_hostname))
        return self.handler(url, allowed_hostname)


def fetched(url: str, html: str = "<html></html>") -> PublicWebFetchResult:
    return PublicWebFetchResult(final_url=url, text=html, content_type="text/html")


def failed(url: str = HOME) -> PublicWebFetchResult:
    return PublicWebFetchResult(
        final_url=url,
        error_code=PublicWebFetchErrorCode.REQUEST_FAILED,
    )


def person_card(
    name: str = "Ada Lovelace",
    title: str = "Founder",
    *,
    email: str | None = None,
    phone: str | None = None,
) -> str:
    details = "".join(
        value
        for value in (
            f'<a href="mailto:{email}">{email}</a>' if email else "",
            f"<p>{phone}</p>" if phone else "",
        )
    )
    return f'<div class="person"><h3>{name}</h3><p class="role">{title}</p>{details}</div>'


def provider_for(
    pages: dict[str, PublicWebFetchResult],
) -> tuple[WebsiteContactDiscoveryProvider, FakeFetcher]:
    fetcher = FakeFetcher(lambda url, _allowed: pages.get(url, failed(url)))
    return WebsiteContactDiscoveryProvider(fetcher=fetcher), fetcher


def test_provider_returns_typed_result_and_parses_homepage_first() -> None:
    provider, fetcher = provider_for({HOME: fetched(HOME, person_card())})
    result = provider.discover(company_id=7, website_url=HOME)
    assert isinstance(result, WebsiteContactDiscoveryProviderResult)
    assert fetcher.calls == [(HOME, None)]
    assert result.attempted_pages == result.successful_pages == 1
    assert len(result.candidates) == 1
    assert result.candidates[0].company_id == 7
    assert result.candidates[0].source_url == HOME
    assert result.candidates[0].source_type == ContactDiscoverySourceType.OTHER_PUBLIC_PAGE


def test_redirected_homepage_defines_source_and_site_identity() -> None:
    final_url = "https://www.example.com/about"
    html = person_card() + '<a href="/team">Team</a>'
    pages = {
        HOME: fetched(final_url, html),
        "https://www.example.com/team": fetched(
            "https://www.example.com/team", person_card("Grace Hopper")
        ),
    }
    provider, fetcher = provider_for(pages)
    result = provider.discover(company_id=1, website_url=HOME)
    assert result.candidates[0].source_url == final_url
    assert result.candidates[0].source_type == ContactDiscoverySourceType.ABOUT_PAGE
    assert fetcher.calls[1] == ("https://www.example.com/team", "www.example.com")


@pytest.mark.parametrize("url", ["not a url", "ftp://example.com", "https://u:p@example.com"])
def test_invalid_website_is_sanitized_without_fetch(url: str) -> None:
    fetcher = FakeFetcher(lambda _url, _allowed: pytest.fail("fetch not expected"))
    result = WebsiteContactDiscoveryProvider(fetcher=fetcher).discover(
        company_id=1, website_url=url
    )
    assert result.errors == ("invalid_website_url",)
    assert result.attempted_pages == 0
    assert repr(result).find(url) == -1
    assert fetcher.calls == []


def test_homepage_failure_stops_and_is_sanitized() -> None:
    fetcher = FakeFetcher(lambda _url, _allowed: failed("https://secret.example/private"))
    result = WebsiteContactDiscoveryProvider(fetcher=fetcher).discover(
        company_id=1, website_url=HOME
    )
    assert result.candidates == ()
    assert result.attempted_pages == 1
    assert result.successful_pages == 0
    assert result.errors == ("homepage_fetch_failed",)
    assert "secret" not in repr(result)
    assert len(fetcher.calls) == 1


@pytest.mark.parametrize(
    ("href", "label", "expected"),
    [
        ("/leadership", "Leadership", ContactDiscoverySourceType.LEADERSHIP_PAGE),
        ("/team", "Team", ContactDiscoverySourceType.TEAM_PAGE),
        ("/staff", "Staff", ContactDiscoverySourceType.STAFF_PAGE),
        ("/about", "About", ContactDiscoverySourceType.ABOUT_PAGE),
        ("/contact", "Contact", ContactDiscoverySourceType.CONTACT_PAGE),
        ("/directory", "Directory", ContactDiscoverySourceType.OTHER_PUBLIC_PAGE),
    ],
)
def test_page_classification_uses_exact_structural_tokens(
    href: str, label: str, expected: ContactDiscoverySourceType
) -> None:
    secondary = f"{HOME}{href}"
    pages = {
        HOME: fetched(HOME, f'<a href="{href}">{label}</a>'),
        secondary: fetched(secondary, person_card()),
    }
    provider, _ = provider_for(pages)
    result = provider.discover(company_id=1, website_url=HOME)
    assert result.candidates[0].source_type == expected


def test_incidental_prose_and_news_path_are_not_selected() -> None:
    html = (
        '<a href="/news/our-team-won-award">Our team won an award</a>'
        '<a href="/updates">Our team won an award</a>'
    )
    provider, fetcher = provider_for({HOME: fetched(HOME, html)})
    result = provider.discover(company_id=1, website_url=HOME)
    assert result.selected_urls == 0
    assert fetcher.calls == [(HOME, None)]


def test_news_homepage_with_team_prose_is_other_public_page() -> None:
    news_url = f"{HOME}/news/our-team-won-award"
    provider, _ = provider_for({news_url: fetched(news_url, person_card())})
    result = provider.discover(company_id=1, website_url=news_url)
    assert result.candidates[0].source_type == ContactDiscoverySourceType.OTHER_PUBLIC_PAGE


@pytest.mark.parametrize(
    "href",
    [
        "https://other.example/team",
        "https://sub.example.com/team",
        "https://linkedin.com/company/example",
        "https://instagram.com/example",
        "https://facebook.com/example",
        "https://x.com/example",
        "mailto:a@example.com",
        "tel:+12125550199",
        "javascript:alert(1)",
        "data:text/plain,x",
        "file:///tmp/team",
        "blob:https://example.com/id",
        "#team",
        "https://u:p@example.com/team",
        "https://example.com:8443/team",
        "/privacy/team",
        "/team.pdf",
    ],
)
def test_unsafe_or_irrelevant_links_are_ignored(href: str) -> None:
    provider, fetcher = provider_for({HOME: fetched(HOME, f'<a href="{href}">Team</a>')})
    result = provider.discover(company_id=1, website_url=HOME)
    assert result.selected_urls == 0
    assert len(fetcher.calls) == 1


def test_fragment_trailing_slash_and_homepage_duplicates_are_not_refetched() -> None:
    html = '<a href="/team#one">Team</a><a href="/team/">Team</a><a href="/#home">Team</a>'
    team = f"{HOME}/team"
    provider, fetcher = provider_for({HOME: fetched(HOME, html), team: fetched(team)})
    result = provider.discover(company_id=1, website_url=HOME)
    assert result.selected_urls == 1
    assert fetcher.calls == [(HOME, None), (team, "example.com")]


def test_https_default_port_forms_deduplicate_to_one_secondary_fetch() -> None:
    html = '<a href="/team">Team</a><a href="https://example.com:443/team">Team</a>'
    team = f"{HOME}/team"
    provider, fetcher = provider_for({HOME: fetched(HOME, html), team: fetched(team)})
    result = provider.discover(company_id=1, website_url=HOME)
    assert result.selected_urls == 1
    assert result.attempted_pages == 2
    assert fetcher.calls == [(HOME, None), (team, "example.com")]


def test_http_default_port_forms_deduplicate_to_one_secondary_fetch() -> None:
    homepage = "http://example.com"
    team = f"{homepage}/team"
    html = '<a href="http://example.com/team">Team</a><a href="http://example.com:80/team">Team</a>'
    provider, fetcher = provider_for({homepage: fetched(homepage, html), team: fetched(team)})
    result = provider.discover(company_id=1, website_url=homepage)
    assert result.selected_urls == 1
    assert result.attempted_pages == 2
    assert fetcher.calls == [(homepage, None), (team, "example.com")]


@pytest.mark.parametrize(
    ("homepage", "links"),
    [
        (
            HOME,
            '<a href="https://example.com:443/">Team</a>'
            '<a href="https://example.com:443">Team</a><a href="/">Team</a>',
        ),
        (
            "https://example.com:443",
            '<a href="https://example.com/">Team</a><a href="https://example.com">Team</a>',
        ),
    ],
)
def test_homepage_default_port_forms_are_never_refetched(homepage: str, links: str) -> None:
    provider, fetcher = provider_for({homepage: fetched(homepage, links)})
    result = provider.discover(company_id=1, website_url=homepage)
    assert result.selected_urls == 0
    assert result.attempted_pages == 1
    assert fetcher.calls == [(homepage, None)]


def test_non_default_port_link_remains_a_different_site_and_is_rejected() -> None:
    html = '<a href="/team">Team</a><a href="https://example.com:8443/team">Team</a>'
    team = f"{HOME}/team"
    provider, fetcher = provider_for({HOME: fetched(HOME, html), team: fetched(team)})
    result = provider.discover(company_id=1, website_url=HOME)
    assert result.selected_urls == 1
    assert fetcher.calls == [(HOME, None), (team, "example.com")]


def test_ipv6_website_is_rejected_by_existing_url_policy_without_fetch() -> None:
    url = "https://[2606:4700:4700::1111]/"
    fetcher = FakeFetcher(lambda _url, _allowed: pytest.fail("fetch not expected"))
    result = WebsiteContactDiscoveryProvider(fetcher=fetcher).discover(
        company_id=1, website_url=url
    )
    assert result.errors == ("invalid_website_url",)
    assert fetcher.calls == []


def test_selection_priority_and_same_priority_document_order() -> None:
    html = (
        '<a href="/about">About</a><a href="/team-two">Team</a>'
        '<a href="/leadership">Leadership</a><a href="/team-one">Team</a>'
    )
    pages = {HOME: fetched(HOME, html)}
    for path in ("/about", "/team-two", "/leadership", "/team-one"):
        pages[f"{HOME}{path}"] = fetched(f"{HOME}{path}")
    provider, fetcher = provider_for(pages)
    result = provider.discover(company_id=1, website_url=HOME)
    assert result.attempted_pages == 3
    assert fetcher.calls[1:] == [
        (f"{HOME}/leadership", "example.com"),
        (f"{HOME}/team-two", "example.com"),
    ]


def test_secondary_links_are_not_crawled_recursively() -> None:
    team = f"{HOME}/team"
    staff = f"{HOME}/staff"
    pages = {
        HOME: fetched(HOME, '<a href="/team">Team</a>'),
        team: fetched(team, '<a href="/staff">Staff</a>'),
        staff: fetched(staff, person_card()),
    }
    provider, fetcher = provider_for(pages)
    provider.discover(company_id=1, website_url=HOME)
    assert fetcher.calls == [(HOME, None), (team, "example.com")]


def test_anchor_href_and_retained_link_limits_are_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(website_provider, "MAX_ANCHORS_INSPECTED", 2)
    monkeypatch.setattr(website_provider, "MAX_CANDIDATE_LINKS", 1)
    html = '<a href="/leadership">Leadership</a><a href="/team">Team</a><a href="/staff">Staff</a>'
    provider, fetcher = provider_for(
        {HOME: fetched(HOME, html), f"{HOME}/leadership": fetched(f"{HOME}/leadership")}
    )
    result = provider.discover(company_id=1, website_url=HOME)
    assert result.limited_link_scan is True
    assert result.selected_urls == 1
    assert len(fetcher.calls) == 2
    assert "href" not in repr(result)


def test_overlong_href_is_ignored() -> None:
    href = "/team?x=" + ("a" * website_provider.MAX_HREF_LENGTH)
    provider, fetcher = provider_for({HOME: fetched(HOME, f'<a href="{href}">Team</a>')})
    assert provider.discover(company_id=1, website_url=HOME).selected_urls == 0
    assert len(fetcher.calls) == 1


def test_secondary_failure_keeps_other_candidates_and_continues() -> None:
    team = f"{HOME}/team"
    about = f"{HOME}/about"
    pages = {
        HOME: fetched(
            HOME, person_card("Ada Lovelace") + '<a href="/team">Team</a><a href="/about">About</a>'
        ),
        team: failed("https://secret.example/team"),
        about: fetched(about, person_card("Grace Hopper")),
    }
    provider, fetcher = provider_for(pages)
    result = provider.discover(company_id=1, website_url=HOME)
    assert result.attempted_pages == 3
    assert result.successful_pages == 2
    assert result.errors == ("secondary_page_fetch_failed",)
    assert {candidate.name for candidate in result.candidates} == {"Ada Lovelace", "Grace Hopper"}
    assert "secret" not in repr(result)
    assert len(fetcher.calls) == 3


def test_secondary_redirect_to_different_port_is_rejected_before_parsing() -> None:
    team = f"{HOME}/team"
    pages = {
        HOME: fetched(HOME, person_card() + '<a href="/team">Team</a>'),
        team: fetched("https://example.com:8443/team", person_card("Grace Hopper")),
    }
    provider, _ = provider_for(pages)
    result = provider.discover(company_id=1, website_url=HOME)
    assert result.errors == ("secondary_page_fetch_failed",)
    assert result.successful_pages == 1
    assert [candidate.name for candidate in result.candidates] == ["Ada Lovelace"]


def test_secondary_redirect_to_another_host_is_rejected_before_parsing() -> None:
    team = f"{HOME}/team"
    pages = {
        HOME: fetched(HOME, person_card() + '<a href="/team">Team</a>'),
        team: fetched("https://attacker.example/team", person_card("Injected Person")),
    }
    provider, _ = provider_for(pages)
    result = provider.discover(company_id=1, website_url=HOME)
    assert result.errors == ("secondary_page_fetch_failed",)
    assert result.attempted_pages == 2
    assert result.successful_pages == 1
    assert [candidate.name for candidate in result.candidates] == ["Ada Lovelace"]
    assert "attacker" not in repr(result)
    assert "Injected Person" not in repr(result)


def test_parser_exception_is_sanitized_and_other_pages_remain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = parse_contact_discovery_candidates_from_html

    def parser(**kwargs: Any) -> list[ContactDiscoveryCandidateCreate]:
        if kwargs["source_url"].endswith("/team"):
            raise RuntimeError("raw HTML secret traceback")
        return original(**kwargs)

    monkeypatch.setattr(website_provider, "parse_contact_discovery_candidates_from_html", parser)
    team = f"{HOME}/team"
    pages = {
        HOME: fetched(HOME, person_card() + '<a href="/team">Team</a>'),
        team: fetched(team, person_card("Grace Hopper")),
    }
    provider, _ = provider_for(pages)
    result = provider.discover(company_id=1, website_url=HOME)
    assert result.errors == ("page_parse_failed",)
    assert [candidate.name for candidate in result.candidates] == ["Ada Lovelace"]
    assert "secret" not in repr(result)
    assert "traceback" not in repr(result).casefold()


def test_parser_base_exception_is_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupt(**_kwargs: Any) -> list[ContactDiscoveryCandidateCreate]:
        raise KeyboardInterrupt

    monkeypatch.setattr(website_provider, "parse_contact_discovery_candidates_from_html", interrupt)
    provider, _ = provider_for({HOME: fetched(HOME, person_card())})
    with pytest.raises(KeyboardInterrupt):
        provider.discover(company_id=1, website_url=HOME)


def test_successful_pages_counts_same_site_fetch_even_when_parser_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def parser(**_kwargs: Any) -> list[ContactDiscoveryCandidateCreate]:
        raise RuntimeError("secret parser detail")

    monkeypatch.setattr(website_provider, "parse_contact_discovery_candidates_from_html", parser)
    provider, _ = provider_for({HOME: fetched(HOME, person_card())})
    result = provider.discover(company_id=1, website_url=HOME)
    assert result.attempted_pages == 1
    assert result.successful_pages == 1
    assert result.errors == ("page_parse_failed",)
    assert "secret" not in repr(result)


def test_same_email_across_pages_merges_conservatively() -> None:
    team = f"{HOME}/team"
    pages = {
        HOME: fetched(HOME, person_card(email="ada@example.com")),
        team: fetched(
            team,
            person_card("Ada Byron", "CTO", email="ADA@example.com", phone="+1 212 555 0199"),
        ),
    }
    pages[HOME] = fetched(HOME, pages[HOME].text + '<a href="/team">Team</a>')  # type: ignore[operator]
    provider, _ = provider_for(pages)
    result = provider.discover(company_id=9, website_url=HOME)
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.company_id == 9
    assert candidate.name == "Ada Lovelace"
    assert candidate.title == "Founder"
    assert candidate.phone == "+1 212 555 0199"
    assert candidate.confidence >= 0


def test_duplicate_merge_fills_empty_fields_and_keeps_maximum_confidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    team = f"{HOME}/team"

    def parser(**kwargs: Any) -> list[ContactDiscoveryCandidateCreate]:
        homepage = kwargs["source_url"] == HOME
        return [
            ContactDiscoveryCandidateCreate(
                company_id=kwargs["company_id"],
                name="Ada Lovelace" if homepage else "Changed Name",
                title=None if homepage else "CTO",
                email="ada@example.com",
                phone=None if homepage else "+1 212 555 0199",
                source_url=kwargs["source_url"],
                source_type=kwargs["source_type"],
                confidence=60 if homepage else 90,
            )
        ]

    monkeypatch.setattr(website_provider, "parse_contact_discovery_candidates_from_html", parser)
    pages = {
        HOME: fetched(HOME, '<a href="/team">Team</a>'),
        team: fetched(team),
    }
    provider, _ = provider_for(pages)
    result = provider.discover(company_id=1, website_url=HOME)
    assert len(result.candidates) == 1
    assert result.candidates[0].name == "Ada Lovelace"
    assert result.candidates[0].title == "CTO"
    assert result.candidates[0].phone == "+1 212 555 0199"
    assert result.candidates[0].confidence == 90
    assert result.candidates[0].source_url == HOME


def test_fallback_identity_remains_source_scoped() -> None:
    team = f"{HOME}/team"
    pages = {
        HOME: fetched(HOME, person_card() + '<a href="/team">Team</a>'),
        team: fetched(team, person_card()),
    }
    provider, _ = provider_for(pages)
    result = provider.discover(company_id=1, website_url=HOME)
    assert len(result.candidates) == 2
    assert {candidate.source_url for candidate in result.candidates} == {HOME, team}


def test_company_identity_is_not_global() -> None:
    provider, _ = provider_for({HOME: fetched(HOME, person_card(email="ada@example.com"))})
    first = provider.discover(company_id=1, website_url=HOME)
    second = provider.discover(company_id=2, website_url=HOME)
    assert first.candidates[0].company_id == 1
    assert second.candidates[0].company_id == 2


def test_unknown_charset_fetch_error_is_mapped_to_fixed_homepage_error() -> None:
    fetcher = FakeFetcher(
        lambda _url, _allowed: PublicWebFetchResult(
            final_url="https://secret.example",
            error_code=PublicWebFetchErrorCode.RESPONSE_DECODE_FAILED,
        )
    )
    result = WebsiteContactDiscoveryProvider(fetcher=fetcher).discover(
        company_id=1, website_url=HOME
    )
    assert result.errors == ("homepage_fetch_failed",)
    assert "secret" not in repr(result)


def test_max_pages_is_bounded() -> None:
    with pytest.raises(ValueError):
        WebsiteContactDiscoveryProvider(fetcher=FakeFetcher(lambda _u, _a: failed()), max_pages=4)
    with pytest.raises(ValueError):
        WebsiteContactDiscoveryProvider(fetcher=FakeFetcher(lambda _u, _a: failed()), max_pages=0)


def test_configured_single_page_never_fetches_secondary_links() -> None:
    fetcher = FakeFetcher(lambda url, _allowed: fetched(url, '<a href="/team">Team</a>'))
    result = WebsiteContactDiscoveryProvider(fetcher=fetcher, max_pages=1).discover(
        company_id=1, website_url=HOME
    )
    assert result.attempted_pages == 1
    assert result.selected_urls == 0
    assert fetcher.calls == [(HOME, None)]
