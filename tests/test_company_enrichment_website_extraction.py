from pathlib import Path

import pytest

import app.modules.company_enrichment.website_extraction as extraction
from app.modules.company_enrichment.schemas import CompanyEnrichmentProviderResult
from app.modules.company_enrichment.website_extraction import (
    extract_company_enrichment_from_html,
)

SOURCE_URL = "https://Example.COM/"


def extract(html: str, source_url: str = SOURCE_URL) -> CompanyEnrichmentProviderResult:
    return extract_company_enrichment_from_html(html=html, source_url=source_url)


def test_extracts_and_normalizes_mailto_email() -> None:
    result = extract('<a href="mailto:Sales@EXAMPLE.COM?subject=Hello">Email us</a>')
    assert result.email == "Sales@example.com"


def test_extracts_visible_email() -> None:
    assert extract("Contact info@EXAMPLE.COM for details.").email == "info@example.com"


def test_malformed_email_is_ignored() -> None:
    result = extract('<a href="mailto:not-an-email">bad@example</a>')
    assert result.email is None


def test_duplicate_emails_return_first_canonical_value() -> None:
    result = extract(
        '<a href="mailto:first@EXAMPLE.COM">first@example.com</a> '
        '<a href="mailto:first@example.com">duplicate</a> second@example.com'
    )
    assert result.email == "first@example.com"


def test_extracts_tel_phone() -> None:
    assert extract('<a href="tel:+1 (212) 555-0199">Call</a>').phone == "+1 (212) 555-0199"


def test_extracts_visible_phone() -> None:
    assert extract("Call us at +1 212 555 0199 today.").phone == "+1 212 555 0199"


def test_short_phone_is_ignored() -> None:
    assert extract('<a href="tel:123">123</a>').phone is None


def test_duplicate_phones_return_first_canonical_value() -> None:
    result = extract(
        '<a href="tel:+1 212 555 0199">Call</a> '
        '<a href="tel:+1 212 555 0199">Again</a> 646 555 0100'
    )
    assert result.phone == "+1 212 555 0199"


def test_extracts_instagram_public_profile() -> None:
    result = extract('<a href="https://www.instagram.com/Example/">Instagram</a>')
    assert result.instagram_url == "https://instagram.com/Example"


@pytest.mark.parametrize(
    "href",
    [
        "/instagram/profile",
        "https://example.com/instagram/profile",
        "https://instagram.com/p/abc",
        "https://instagram.com/reel/abc",
        "https://instagram.com/stories/user",
        "https://instagram.com/share",
        "https://instagram.com/explore",
        "https://instagram.com/accounts",
        "https://instagram.com/login",
    ],
)
def test_rejects_non_profile_instagram_links(href: str) -> None:
    assert extract(f'<a href="{href}">Instagram</a>').instagram_url is None


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("company", "https://linkedin.com/company/Example"),
        ("school", "https://linkedin.com/school/Example"),
        ("showcase", "https://linkedin.com/showcase/Example"),
    ],
)
def test_extracts_linkedin_organization_pages(kind: str, expected: str) -> None:
    result = extract(f'<a href="https://www.linkedin.com/{kind}/Example/">LinkedIn</a>')
    assert result.linkedin_url == expected


def test_rejects_linkedin_personal_profile() -> None:
    result = extract('<a href="https://linkedin.com/in/person">LinkedIn</a>')
    assert result.linkedin_url is None


@pytest.mark.parametrize(
    ("href", "text", "expected"),
    [
        ("/contact/", "Contact", "https://example.com/contact"),
        ("https://example.com/contact-us/#team", "Reach us", "https://example.com/contact-us"),
        ("/support", "Get in touch", "https://example.com/support"),
    ],
)
def test_extracts_same_site_contact_page(href: str, text: str, expected: str) -> None:
    result = extract(f'<a href="{href}">{text}</a>')
    assert result.contact_page_url == expected


def test_rejects_off_site_contact_page() -> None:
    result = extract('<a href="https://other.example/contact">Contact</a>')
    assert result.contact_page_url is None


@pytest.mark.parametrize(
    "href",
    [
        "javascript:alert(1)",
        "mailto:info@example.com",
        "tel:+12125550199",
        "data:text/plain,contact",
        "https://user:password@example.com/contact",
    ],
)
def test_rejects_unsafe_contact_page_schemes(href: str) -> None:
    assert extract(f'<a href="{href}">Contact us</a>').contact_page_url is None


@pytest.mark.parametrize(
    ("href", "text", "expected"),
    [
        ("/about/", "About", "https://example.com/about"),
        ("/about-us/#team", "Team", "https://example.com/about-us"),
        ("/our-story/", "Story", "https://example.com/our-story"),
        ("/organization", "Company", "https://example.com/organization"),
    ],
)
def test_extracts_about_page_keywords(href: str, text: str, expected: str) -> None:
    assert extract(f'<a href="{href}">{text}</a>').about_page_url == expected


def test_invalid_source_url_returns_safe_error() -> None:
    raw_html = "<html><body>secret@example.com<script>API_KEY=secret</script></body></html>"
    result = extract(raw_html, "javascript:alert(1)")
    assert result.provider == "website_static"
    assert result.source_url is None
    assert result.email is None
    assert result.errors == ["Source URL is invalid."]
    assert raw_html not in repr(result)
    assert "API_KEY" not in repr(result)


def test_valid_source_url_is_normalized() -> None:
    result = extract("", " HTTPS://Exämple.COM/path/#fragment ")
    assert result.source_url == "https://xn--exmple-cua.com/path"


def test_ignored_elements_do_not_contribute_candidates() -> None:
    result = extract(
        """
        <script>script@example.com +1 212 555 0101</script>
        <style>.x { content: 'style@example.com +1 212 555 0102'; }</style>
        <noscript>noscript@example.com +1 212 555 0103</noscript>
        <p>No contact data here.</p>
        """
    )
    assert result.email is None
    assert result.phone is None


def test_long_html_is_capped_and_reported_safely() -> None:
    hidden_tail = "tail@example.com"
    raw_html = "x" * 250_000 + hidden_tail
    result = extract(raw_html)
    assert result.email is None
    assert result.errors == ["HTML input was truncated safely."]
    assert hidden_tail not in repr(result)
    assert raw_html not in repr(result)


def test_returns_only_first_valid_candidates_and_safe_note() -> None:
    result = extract(
        """
        <a href="mailto:first@example.com">First</a>
        <a href="mailto:second@example.com">Second</a>
        <a href="tel:+1 212 555 0199">First phone</a>
        <a href="tel:+1 646 555 0100">Second phone</a>
        <a href="https://instagram.com/first">Instagram first</a>
        <a href="https://instagram.com/second">Instagram second</a>
        <a href="https://linkedin.com/company/first">LinkedIn first</a>
        <a href="https://linkedin.com/company/second">LinkedIn second</a>
        <a href="/contact">Contact</a>
        <a href="/contact-us">Contact again</a>
        """
    )
    assert isinstance(result, CompanyEnrichmentProviderResult)
    assert result.provider == "website_static"
    assert result.email == "first@example.com"
    assert result.phone == "+1 212 555 0199"
    assert result.instagram_url == "https://instagram.com/first"
    assert result.linkedin_url == "https://linkedin.com/company/first"
    assert result.contact_page_url == "https://example.com/contact"
    assert result.notes == "Static website enrichment parsed."


def test_result_error_lists_are_independent() -> None:
    first = extract("x" * 250_001)
    second = extract("")
    first.errors.append("local mutation")
    assert second.errors == []


def test_parser_has_no_network_or_automation_imports() -> None:
    source = Path(extraction.__file__).read_text(encoding="utf-8").casefold()
    for forbidden in [
        "httpx",
        "requests",
        "urllib.request",
        "serpapi",
        "selenium",
        "playwright",
        "browser",
        "openai",
    ]:
        assert forbidden not in source
