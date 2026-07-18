import pytest

from app.modules.company_discovery.staging_normalization import (
    normalize_candidate_identity,
    normalize_display_name,
    normalize_staging_website,
)


def test_display_and_normalized_name_are_deterministic() -> None:
    assert normalize_display_name("  Acme\t  COMPANY ") == "Acme COMPANY"
    candidate = normalize_candidate_identity(
        name="  Acme\t COMPANY ", website=None, country_code="us"
    )
    assert candidate.name == "Acme COMPANY"
    assert candidate.normalized_name == "acme company"
    assert candidate.identity_key == "name_country:acme company|US"


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_http_and_https_websites_are_accepted(scheme: str) -> None:
    website, identity = normalize_staging_website(f"{scheme}://example.com/about")
    assert website == f"{scheme}://example.com/about"
    assert identity == "example.com"


@pytest.mark.parametrize(
    "url",
    [
        "https://user:secret@example.com",
        "ftp://example.com",
        "https://bad_host.example",
        "https://localhost",
        "https://127.0.0.1",
        "https://10.0.0.1",
        "https://[::1]",
    ],
)
def test_unsafe_or_invalid_websites_are_rejected(url: str) -> None:
    with pytest.raises(ValueError):
        normalize_staging_website(url)


def test_idna_terminal_dot_and_fragment_are_normalized() -> None:
    website, identity = normalize_staging_website("https://BÜCHER.example./path#secret")
    assert website == "https://xn--bcher-kva.example/path"
    assert identity == "xn--bcher-kva.example"


@pytest.mark.parametrize(
    ("url", "identity"),
    [
        ("https://www.example.com/about", "example.com"),
        ("https://sub.example.com/about", "sub.example.com"),
        ("http://example.com:80/path", "example.com"),
        ("https://example.com:443/path", "example.com"),
        ("https://example.com:8443/path", "example.com:8443"),
        ("https://example.com/path?q=one#fragment", "example.com"),
    ],
)
def test_website_identity_rules(url: str, identity: str) -> None:
    assert normalize_staging_website(url)[1] == identity


def test_website_identity_takes_precedence_over_fallback() -> None:
    candidate = normalize_candidate_identity(
        name="Acme", website="https://example.com/about", country_code="US"
    )
    assert candidate.identity_key == "website:example.com"


def test_fallback_requires_name_and_country() -> None:
    for name, country in ((None, "US"), ("Acme", None), ("  ", "US")):
        with pytest.raises(ValueError):
            normalize_candidate_identity(name=name, website=None, country_code=country)


def test_equivalent_website_forms_have_stable_identity() -> None:
    urls = (
        "http://www.example.com",
        "https://example.com/",
        "https://example.com/path?q=1#x",
    )
    identities = {
        normalize_candidate_identity(name=None, website=url, country_code=None).identity_key
        for url in urls
    }
    assert identities == {"website:example.com"}
    assert (
        normalize_staging_website("https://example.com:8443")[1]
        != normalize_staging_website("https://example.com:9443")[1]
    )
