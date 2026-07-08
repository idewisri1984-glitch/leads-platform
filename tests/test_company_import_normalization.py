import pytest

from app.modules.company_import import normalize_text_identity, normalize_website_hostname


@pytest.mark.parametrize(
    ("website", "expected"),
    [
        ("https://example.com", "example.com"),
        ("http://example.com/", "example.com"),
        ("https://www.example.com/about", "example.com"),
        ("EXAMPLE.COM", "example.com"),
        ("example.com?utm_source=test", "example.com"),
        ("subdomain.example.com", "subdomain.example.com"),
    ],
)
def test_normalize_website_examples(website: str, expected: str) -> None:
    assert normalize_website_hostname(website) == expected


@pytest.mark.parametrize("website", [None, "", "   "])
def test_blank_website_returns_none(website: str | None) -> None:
    assert normalize_website_hostname(website) is None


def test_invalid_scheme_raises_value_error() -> None:
    with pytest.raises(ValueError, match="scheme must be http or https"):
        normalize_website_hostname("ftp://example.com")


@pytest.mark.parametrize(
    "website",
    [
        "https://",
        "https://exa mple.com",
        "https://-example.com",
    ],
)
def test_invalid_hostname_raises_value_error(website: str) -> None:
    with pytest.raises(ValueError, match="valid hostname"):
        normalize_website_hostname(website)


def test_exact_www_prefix_is_removed() -> None:
    assert normalize_website_hostname("https://www.example.com") == "example.com"
    assert normalize_website_hostname("https://www2.example.com") == "www2.example.com"


def test_subdomain_is_preserved() -> None:
    assert normalize_website_hostname("https://shop.example.com/products") == "shop.example.com"


def test_port_path_query_and_fragment_are_ignored() -> None:
    website = "https://example.com:8443/about?utm_source=test#team"

    assert normalize_website_hostname(website) == "example.com"


def test_unicode_domain_is_converted_to_idna_ascii() -> None:
    assert normalize_website_hostname("https://münich.example") == "xn--mnich-kva.example"


def test_normalize_text_identity_applies_all_rules() -> None:
    value = "  ＡＣＭＥ\t  Straße  "

    assert normalize_text_identity(value) == "acme strasse"


@pytest.mark.parametrize("value", [None, "", " \t\n "])
def test_blank_text_returns_none(value: str | None) -> None:
    assert normalize_text_identity(value) is None
