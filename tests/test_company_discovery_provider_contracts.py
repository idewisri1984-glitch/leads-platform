import inspect

import pytest
from pydantic import ValidationError

import app.modules.company_discovery.provider_interfaces as provider_interfaces
import app.modules.search_profile.query_generation as query_generation
from app.modules.company_discovery import (
    DiscoveryProvider,
    DiscoveryProviderConfigurationError,
    DiscoveryProviderError,
    DiscoveryProviderRateLimitError,
    DiscoveryProviderRequestError,
    DiscoveryProviderResponse,
    DiscoveryProviderResponseError,
    DiscoveryProviderResult,
)
from app.modules.search_profile.schemas import SearchQuery


class FakeDiscoveryProvider:
    def __init__(self) -> None:
        self.received_query: SearchQuery | None = None

    @property
    def provider_name(self) -> str:
        return "fake"

    def search(self, query: SearchQuery) -> DiscoveryProviderResponse:
        self.received_query = query
        return DiscoveryProviderResponse(
            provider=self.provider_name,
            query=query.text,
            results=[DiscoveryProviderResult(title="Example Company")],
            total_results=1,
        )


def make_search_query() -> SearchQuery:
    return SearchQuery(
        text="accounting firms Germany",
        profile_id=1,
        profile_name="Accounting buyers",
        country="Germany",
        source_template="{target_customer_type} {country}",
        limit=10,
    )


def test_valid_discovery_provider_result() -> None:
    result = DiscoveryProviderResult(
        title="Example Company",
        link="https://example.com",
        snippet="Business services",
        source="Example Directory",
        position=1,
        provider_reference="company-1",
    )

    assert result.model_dump() == {
        "title": "Example Company",
        "link": "https://example.com",
        "snippet": "Business services",
        "source": "Example Directory",
        "position": 1,
        "provider_reference": "company-1",
    }


def test_result_title_is_stripped() -> None:
    assert DiscoveryProviderResult(title="  Example Company  ").title == "Example Company"


def test_blank_result_title_is_rejected() -> None:
    with pytest.raises(ValidationError, match="title is required"):
        DiscoveryProviderResult(title="   ")


def test_optional_result_strings_are_stripped_and_blank_values_become_none() -> None:
    result = DiscoveryProviderResult(
        title="Example",
        link="  https://example.com  ",
        snippet="   ",
        source="\t",
        provider_reference="  ref-1  ",
    )

    assert result.link == "https://example.com"
    assert result.snippet is None
    assert result.source is None
    assert result.provider_reference == "ref-1"


def test_result_position_none_is_allowed() -> None:
    assert DiscoveryProviderResult(title="Example", position=None).position is None


def test_result_position_at_least_one_is_accepted() -> None:
    assert DiscoveryProviderResult(title="Example", position=1).position == 1


@pytest.mark.parametrize("position", [0, -1])
def test_result_position_below_one_is_rejected(position: int) -> None:
    with pytest.raises(ValidationError):
        DiscoveryProviderResult(title="Example", position=position)


def test_valid_discovery_provider_response() -> None:
    result = DiscoveryProviderResult(title="Example")
    response = DiscoveryProviderResponse(
        provider="fake",
        query="example companies",
        results=[result],
        total_results=25,
    )

    assert response.provider == "fake"
    assert response.query == "example companies"
    assert response.results == [result]
    assert response.total_results == 25


def test_response_provider_and_query_are_stripped() -> None:
    response = DiscoveryProviderResponse(provider="  fake  ", query="  example companies  ")

    assert response.provider == "fake"
    assert response.query == "example companies"


@pytest.mark.parametrize(
    ("field", "values"),
    [
        ("provider", {"provider": " ", "query": "example"}),
        ("query", {"provider": "fake", "query": "\t"}),
    ],
)
def test_blank_required_response_string_is_rejected(
    field: str,
    values: dict[str, str],
) -> None:
    with pytest.raises(ValidationError) as error:
        DiscoveryProviderResponse(**values)

    assert error.value.errors()[0]["loc"] == (field,)


def test_empty_response_results_are_allowed() -> None:
    assert DiscoveryProviderResponse(provider="fake", query="example").results == []


def test_response_result_defaults_are_independent() -> None:
    first = DiscoveryProviderResponse(provider="fake", query="first")
    second = DiscoveryProviderResponse(provider="fake", query="second")

    first.results.append(DiscoveryProviderResult(title="Example"))

    assert len(first.results) == 1
    assert second.results == []


def test_response_total_results_none_is_allowed() -> None:
    assert (
        DiscoveryProviderResponse(
            provider="fake", query="example", total_results=None
        ).total_results
        is None
    )


@pytest.mark.parametrize("total_results", [0, 1, 100])
def test_nonnegative_response_total_results_are_accepted(total_results: int) -> None:
    response = DiscoveryProviderResponse(
        provider="fake",
        query="example",
        total_results=total_results,
    )

    assert response.total_results == total_results


def test_negative_response_total_results_are_rejected() -> None:
    with pytest.raises(ValidationError):
        DiscoveryProviderResponse(provider="fake", query="example", total_results=-1)


def test_total_results_does_not_need_to_equal_returned_result_count() -> None:
    response = DiscoveryProviderResponse(
        provider="fake",
        query="example",
        results=[DiscoveryProviderResult(title="Example")],
        total_results=500,
    )

    assert response.total_results == 500
    assert len(response.results) == 1


def test_fake_provider_structurally_satisfies_protocol() -> None:
    assert isinstance(FakeDiscoveryProvider(), DiscoveryProvider)


def test_fake_provider_accepts_search_query_and_returns_response() -> None:
    provider = FakeDiscoveryProvider()
    query = make_search_query()

    response = provider.search(query)

    assert provider.received_query is query
    assert isinstance(response, DiscoveryProviderResponse)
    assert response.query == query.text
    assert response.results[0].title == "Example Company"


def test_provider_name_property() -> None:
    assert FakeDiscoveryProvider().provider_name == "fake"


def test_configuration_error_is_a_provider_error() -> None:
    assert issubclass(DiscoveryProviderConfigurationError, DiscoveryProviderError)


def test_request_error_is_a_provider_error() -> None:
    assert issubclass(DiscoveryProviderRequestError, DiscoveryProviderError)


def test_rate_limit_error_has_request_and_provider_error_bases() -> None:
    assert issubclass(DiscoveryProviderRateLimitError, DiscoveryProviderRequestError)
    assert issubclass(DiscoveryProviderRateLimitError, DiscoveryProviderError)


def test_response_error_is_a_provider_error() -> None:
    assert issubclass(DiscoveryProviderResponseError, DiscoveryProviderError)


@pytest.mark.parametrize(
    "forbidden_import",
    [
        "serpapi",
        "sqlalchemy",
        "CompanyIngestionService",
        "SearchProfile",
        "SessionLocal",
        "settings",
    ],
)
def test_provider_interfaces_have_no_forbidden_imports(forbidden_import: str) -> None:
    source = inspect.getsource(provider_interfaces)

    if forbidden_import == "SearchProfile":
        assert "SearchProfile ORM" not in source
        assert "search_profile.models" not in source
        return

    assert forbidden_import not in source


def test_query_generator_has_no_provider_or_serpapi_imports() -> None:
    source = inspect.getsource(query_generation)

    assert "serpapi" not in source.casefold()
    assert "company_discovery.provider_interfaces" not in source
