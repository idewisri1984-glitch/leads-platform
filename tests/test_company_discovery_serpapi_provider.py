import inspect
import socket
from typing import cast

import pytest

import app.modules.company_discovery.serpapi_provider as serpapi_provider_module
from app.modules.company_discovery import (
    DiscoveryProvider,
    DiscoveryProviderConfigurationError,
    DiscoveryProviderError,
    DiscoveryProviderRateLimitError,
    DiscoveryProviderRequestError,
    DiscoveryProviderResponseError,
    SerpApiDiscoveryProvider,
)
from app.modules.search_profile.schemas import SearchQuery
from app.providers.serpapi import (
    SerpApiClient,
    SerpApiCompanyResult,
    SerpApiConfigurationError,
    SerpApiError,
    SerpApiRateLimitError,
    SerpApiRequestError,
    SerpApiResponseError,
    SerpApiSearchResponse,
)

_FAKE_API_KEY = "fake-secret-api-key"


class StubSerpApiClient:
    def __init__(
        self,
        *,
        response: SerpApiSearchResponse | None = None,
        error: SerpApiError | None = None,
    ) -> None:
        self.response = response or SerpApiSearchResponse(query="example", results=[])
        self.error = error
        self.query: str | None = None
        self.country: str | None = "not-called"
        self.city: str | None = "not-called"
        self.industry: str | None = "not-called"
        self.limit: int | None = None
        self.call_count = 0

    def search_companies(
        self,
        *,
        query: str | None,
        country: str | None,
        city: str | None,
        industry: str | None,
        limit: int,
    ) -> SerpApiSearchResponse:
        self.call_count += 1
        self.query = query
        self.country = country
        self.city = city
        self.industry = industry
        self.limit = limit

        if self.error is not None:
            raise self.error

        return self.response


def make_query() -> SearchQuery:
    return SearchQuery(
        text="accounting firms Berlin Germany",
        profile_id=1,
        profile_name="Accounting buyers",
        country="Germany",
        city="Berlin",
        source_template="{target_customer_type} {city} {country}",
        limit=7,
    )


def make_provider(client: StubSerpApiClient) -> SerpApiDiscoveryProvider:
    return SerpApiDiscoveryProvider(cast(SerpApiClient, client))


def test_provider_name_is_serpapi() -> None:
    provider = make_provider(StubSerpApiClient())

    assert provider.provider_name == "serpapi"


def test_provider_structurally_satisfies_discovery_provider_protocol() -> None:
    provider: DiscoveryProvider = make_provider(StubSerpApiClient())

    assert isinstance(provider, DiscoveryProvider)


def test_search_passes_query_text_limit_and_no_separate_geography() -> None:
    client = StubSerpApiClient()
    query = make_query()

    make_provider(client).search(query)

    assert client.call_count == 1
    assert client.query == query.text
    assert client.limit == query.limit
    assert client.country is None
    assert client.city is None
    assert client.industry is None


def test_vendor_result_maps_to_generic_result() -> None:
    vendor_result = SerpApiCompanyResult(
        title="Example Company",
        link="https://example.com",
        snippet="Accounting software",
        source="Example Directory",
        position=3,
    )
    client = StubSerpApiClient(
        response=SerpApiSearchResponse(
            query="accounting firms Berlin Germany",
            results=[vendor_result],
        )
    )

    response = make_provider(client).search(make_query())

    assert response.provider == "serpapi"
    assert response.query == "accounting firms Berlin Germany"
    assert response.total_results is None
    assert response.results[0].model_dump() == {
        "title": "Example Company",
        "link": "https://example.com",
        "snippet": "Accounting software",
        "source": "Example Directory",
        "position": 3,
        "provider_reference": None,
    }


def test_blank_vendor_response_query_falls_back_to_search_query_text() -> None:
    client = StubSerpApiClient(response=SerpApiSearchResponse(query=" ", results=[]))
    query = make_query()

    response = make_provider(client).search(query)

    assert response.query == query.text


def test_empty_vendor_results_return_empty_generic_results() -> None:
    client = StubSerpApiClient(response=SerpApiSearchResponse(query="accounting firms", results=[]))

    response = make_provider(client).search(make_query())

    assert response.results == []
    assert response.total_results is None


def test_search_does_not_mutate_search_query() -> None:
    query = make_query()
    original = query.model_dump()

    make_provider(StubSerpApiClient()).search(query)

    assert query.model_dump() == original


def test_duplicate_vendor_results_are_preserved() -> None:
    duplicate = SerpApiCompanyResult(
        title="Duplicate Company",
        link="https://example.com",
        snippet=None,
        source=None,
        position=1,
    )
    client = StubSerpApiClient(
        response=SerpApiSearchResponse(
            query="duplicate companies",
            results=[duplicate, duplicate.model_copy()],
        )
    )

    response = make_provider(client).search(make_query())

    assert len(response.results) == 2
    assert response.results[0] == response.results[1]


@pytest.mark.parametrize(
    ("vendor_error", "expected_error", "expected_message"),
    [
        (
            SerpApiConfigurationError(_FAKE_API_KEY),
            DiscoveryProviderConfigurationError,
            "Discovery provider is not configured.",
        ),
        (
            SerpApiRateLimitError(_FAKE_API_KEY),
            DiscoveryProviderRateLimitError,
            "Discovery provider rate limit exceeded.",
        ),
        (
            SerpApiRequestError(_FAKE_API_KEY),
            DiscoveryProviderRequestError,
            "Discovery provider request failed.",
        ),
        (
            SerpApiResponseError(_FAKE_API_KEY),
            DiscoveryProviderResponseError,
            "Discovery provider response was invalid.",
        ),
        (
            SerpApiError(_FAKE_API_KEY),
            DiscoveryProviderError,
            "Discovery provider failed.",
        ),
    ],
)
def test_vendor_errors_map_to_safe_generic_errors(
    vendor_error: SerpApiError,
    expected_error: type[DiscoveryProviderError],
    expected_message: str,
) -> None:
    provider = make_provider(StubSerpApiClient(error=vendor_error))

    with pytest.raises(expected_error) as error:
        provider.search(make_query())

    assert str(error.value) == expected_message
    assert _FAKE_API_KEY not in str(error.value)
    assert error.value.__cause__ is None


@pytest.mark.parametrize(
    "forbidden_dependency",
    [
        "sqlalchemy",
        "SessionLocal",
        "project.models",
        "search_profile.models",
        "company.models",
        "CompanyIngestionService",
        "CompanyDiscoveryService",
        "SearchProfileRepository",
        "SearchProfileService",
        "SearchProfileQueryGenerator",
    ],
)
def test_provider_module_has_no_forbidden_dependencies(forbidden_dependency: str) -> None:
    source = inspect.getsource(serpapi_provider_module)

    assert forbidden_dependency not in source


def test_generic_response_exposes_no_raw_provider_json() -> None:
    response = make_provider(StubSerpApiClient()).search(make_query())
    response_fields = response.model_dump()

    assert "raw_json" not in response_fields
    assert "raw_payload" not in response_fields


def test_search_performs_no_network_db_or_ingestion_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Provider wrapper attempted an external side effect.")

    monkeypatch.setattr(socket.socket, "connect", forbidden)

    response = make_provider(StubSerpApiClient()).search(make_query())

    assert response.provider == "serpapi"
