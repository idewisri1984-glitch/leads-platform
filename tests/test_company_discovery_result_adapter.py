import inspect
import socket

import pytest

import app.modules.company_discovery.result_adapter as result_adapter_module
from app.modules.company_discovery import (
    DiscoveryProviderResult,
    DiscoveryResultAdapterError,
    provider_result_to_ingestion_item,
)
from app.modules.company_import.schemas import CompanyIngestionItem
from app.modules.search_profile.schemas import SearchQuery

_FAKE_API_KEY = "fake-secret-api-key"
_RAW_PAYLOAD_MARKER = "raw payload marker"


def make_result(
    *,
    title: str = "Example Company",
    link: str | None = "https://example.com",
    source: str | None = "Example Directory",
    snippet: str | None = "Business software provider",
    position: int | None = 3,
) -> DiscoveryProviderResult:
    return DiscoveryProviderResult(
        title=title,
        link=link,
        source=source,
        snippet=snippet,
        position=position,
    )


def make_query(
    *,
    text: str = "accounting firms Berlin Germany",
    country: str | None = "Germany",
    city: str | None = "Berlin",
) -> SearchQuery:
    return SearchQuery(
        text=text,
        profile_id=1,
        profile_name="Accounting buyers",
        country=country,
        city=city,
        source_template="{target_customer_type} {city} {country}",
        limit=10,
    )


def adapt(
    result: DiscoveryProviderResult | None = None,
    *,
    query: SearchQuery | None = None,
    provider_name: str = "serpapi",
) -> CompanyIngestionItem:
    return provider_result_to_ingestion_item(
        result or make_result(),
        query=query or make_query(),
        provider_name=provider_name,
    )


def test_maps_provider_result_and_query_context_to_ingestion_item() -> None:
    item = adapt()

    assert item.name == "Example Company"
    assert item.website == "https://example.com"
    assert item.country == "Germany"
    assert item.city == "Berlin"
    assert item.source_row_number == 3
    assert item.industry is None
    assert item.status == "NEW"


def test_notes_include_safe_provider_query_source_and_snippet() -> None:
    item = adapt()

    assert item.notes is not None
    assert "Provider: serpapi" in item.notes
    assert "Query: accounting firms Berlin Germany" in item.notes
    assert "Source: Example Directory" in item.notes
    assert "Snippet: Business software provider" in item.notes


def test_notes_normalize_excessive_whitespace() -> None:
    result = make_result(
        source="  Example    Directory  ",
        snippet="  Business\n\tsoftware   provider  ",
    )
    query = make_query(text="  accounting    firms\nGermany  ")

    item = adapt(result, query=query)

    assert item.notes == (
        "Provider: serpapi; Query: accounting firms Germany; "
        "Source: Example Directory; Snippet: Business software provider"
    )


def test_notes_are_bounded() -> None:
    item = adapt(make_result(snippet="word " * 1000))

    assert item.notes is not None
    assert len(item.notes) <= 1000
    assert item.notes.endswith("...")


def test_blank_optional_source_and_snippet_are_omitted_cleanly() -> None:
    result = DiscoveryProviderResult.model_construct(
        title="Example Company",
        link=None,
        snippet="   ",
        source="\t",
        position=None,
        provider_reference=None,
    )

    item = adapt(result)

    assert item.notes is not None
    assert "Source:" not in item.notes
    assert "Snippet:" not in item.notes
    assert not item.notes.endswith("; ")


def test_sensitive_note_content_is_omitted() -> None:
    result = make_result(
        source="raw JSON source",
        snippet=f"{_RAW_PAYLOAD_MARKER}: {_FAKE_API_KEY}",
    )

    item = adapt(result)

    assert item.notes is not None
    assert "raw json" not in item.notes.casefold()
    assert _RAW_PAYLOAD_MARKER not in item.notes
    assert _FAKE_API_KEY not in item.notes


def test_credentialed_url_is_not_included_in_notes() -> None:
    query = make_query(text="companies https://user:password@example.com/search")

    item = adapt(query=query)

    assert item.notes is not None
    assert "user:password" not in item.notes
    assert "Query:" not in item.notes


def test_blank_provider_name_raises_controlled_error() -> None:
    with pytest.raises(DiscoveryResultAdapterError) as error:
        adapt(provider_name="   ")

    assert str(error.value) == "Discovery provider name is required."


def test_blank_bypassed_title_raises_controlled_error() -> None:
    result = DiscoveryProviderResult.model_construct(
        title="   ",
        link=None,
        snippet=None,
        source=None,
        position=None,
        provider_reference=None,
    )

    with pytest.raises(DiscoveryResultAdapterError) as error:
        adapt(result)

    assert str(error.value) == "Discovery result title is required."


def test_duplicate_results_are_not_deduplicated() -> None:
    result = make_result()

    first = adapt(result)
    second = adapt(result.model_copy())

    assert first == second
    assert first is not second


def test_adapter_does_not_mutate_inputs() -> None:
    result = make_result()
    query = make_query()
    original_result = result.model_dump()
    original_query = query.model_dump()

    adapt(result, query=query)

    assert result.model_dump() == original_result
    assert query.model_dump() == original_query


def test_provider_name_is_generic() -> None:
    item = adapt(provider_name="business-registry")

    assert item.notes is not None
    assert "Provider: business-registry" in item.notes
    assert "serpapi" not in item.notes.casefold()


def test_adapter_requires_no_serpapi_specific_fields() -> None:
    result = DiscoveryProviderResult(title="Registry Company")

    item = adapt(result, provider_name="registry")

    assert item.name == "Registry Company"
    assert item.website is None
    assert item.source_row_number is None


def test_adapter_does_not_guess_industry_from_query_text() -> None:
    query = make_query(text="manufacturing companies Germany")

    item = adapt(query=query)

    assert item.industry is None


@pytest.mark.parametrize(
    "forbidden_dependency",
    [
        "sqlalchemy",
        "SessionLocal",
        "company.models",
        "CompanyIngestionService",
        "serpapi",
    ],
)
def test_adapter_has_no_forbidden_dependencies(forbidden_dependency: str) -> None:
    source = inspect.getsource(result_adapter_module)

    assert forbidden_dependency.casefold() not in source.casefold()


def test_adapter_performs_no_network_or_database_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Adapter attempted an external side effect.")

    monkeypatch.setattr(socket.socket, "connect", forbidden)

    item = adapt()

    assert item.name == "Example Company"
