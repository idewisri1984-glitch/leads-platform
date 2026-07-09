import pytest

from app.modules.company_discovery import (
    CompanyDiscoveryAdapterError,
    CompanyDiscoveryRequest,
    serpapi_result_to_ingestion_item,
)
from app.providers.serpapi import SerpApiCompanyResult


def make_request() -> CompanyDiscoveryRequest:
    return CompanyDiscoveryRequest(
        query="software companies",
        country="Indonesia",
        city="Bali",
        industry="SaaS",
    )


def test_complete_result_maps_correctly() -> None:
    result = SerpApiCompanyResult(
        position=3,
        title="Acme Bali",
        link="https://acme.example/about",
        snippet="Software company in Bali.",
        source="Acme",
    )

    item = serpapi_result_to_ingestion_item(result, make_request())

    assert item.source_row_number == 3
    assert item.name == "Acme Bali"
    assert item.website == "https://acme.example/about"
    assert item.country == "Indonesia"
    assert item.city == "Bali"
    assert item.industry == "SaaS"
    assert item.status == "NEW"
    assert item.notes == "Discovered via SerpAPI: Software company in Bali."


def test_missing_optional_link_and_snippet_maps_correctly() -> None:
    result = SerpApiCompanyResult(
        position=1,
        title="Acme",
        link=None,
        snippet=None,
        source=None,
    )

    item = serpapi_result_to_ingestion_item(result, make_request())

    assert item.website is None
    assert item.notes is None


def test_request_country_city_and_industry_are_copied() -> None:
    request = CompanyDiscoveryRequest(
        query="hotels",
        country="Thailand",
        city="Bangkok",
        industry="Hospitality",
    )
    result = SerpApiCompanyResult(
        position=None,
        title="Hotel Group",
        link=None,
        snippet=None,
        source=None,
    )

    item = serpapi_result_to_ingestion_item(result, request)

    assert item.country == "Thailand"
    assert item.city == "Bangkok"
    assert item.industry == "Hospitality"


def test_status_defaults_to_new() -> None:
    result = SerpApiCompanyResult(
        position=1,
        title="Acme",
        link=None,
        snippet=None,
        source=None,
    )

    item = serpapi_result_to_ingestion_item(result, make_request())

    assert item.status == "NEW"


def test_source_row_number_uses_position() -> None:
    result = SerpApiCompanyResult(
        position=8,
        title="Acme",
        link=None,
        snippet=None,
        source=None,
    )

    item = serpapi_result_to_ingestion_item(result, make_request())

    assert item.source_row_number == 8


def test_blank_title_raises_controlled_adapter_error() -> None:
    result = SerpApiCompanyResult(
        position=1,
        title="   ",
        link=None,
        snippet=None,
        source=None,
    )

    with pytest.raises(CompanyDiscoveryAdapterError, match="title is required"):
        serpapi_result_to_ingestion_item(result, make_request())


def test_title_is_trimmed() -> None:
    result = SerpApiCompanyResult(
        position=1,
        title="  Acme Bali  ",
        link=None,
        snippet=None,
        source=None,
    )

    item = serpapi_result_to_ingestion_item(result, make_request())

    assert item.name == "Acme Bali"


def test_notes_are_deterministic_and_safe() -> None:
    result = SerpApiCompanyResult(
        position=1,
        title="Acme",
        link=None,
        snippet="  First line\nsecond\tline  ",
        source=None,
    )

    item = serpapi_result_to_ingestion_item(result, make_request())

    assert item.notes == "Discovered via SerpAPI: First line second line"
    assert "api_key" not in item.notes.casefold()


def test_long_notes_are_truncated_deterministically() -> None:
    result = SerpApiCompanyResult(
        position=1,
        title="Acme",
        link=None,
        snippet="A" * 400,
        source=None,
    )

    item = serpapi_result_to_ingestion_item(result, make_request())

    assert item.notes is not None
    assert len(item.notes) == 240
    assert item.notes.endswith("...")


def test_adapter_does_not_normalize_or_deduplicate_website() -> None:
    website = "HTTPS://WWW.Example.COM:443/About"
    result = SerpApiCompanyResult(
        position=1,
        title="Acme",
        link=website,
        snippet=None,
        source=None,
    )

    item = serpapi_result_to_ingestion_item(result, make_request())

    assert item.website == website
