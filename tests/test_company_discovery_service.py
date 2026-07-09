from typing import cast

import pytest

from app.modules.company_discovery import CompanyDiscoveryRequest, CompanyDiscoveryService
from app.modules.company_import.ingestion import CompanyIngestionService
from app.providers.serpapi import SerpApiClient, SerpApiCompanyResult, SerpApiSearchResponse
from app.providers.serpapi.exceptions import (
    SerpApiConfigurationError,
    SerpApiRateLimitError,
    SerpApiRequestError,
)


class FakeSerpApiClient:
    def __init__(
        self,
        response: SerpApiSearchResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, object]] = []

    def search_companies(
        self,
        *,
        query: str | None,
        country: str | None,
        city: str | None,
        industry: str | None,
        limit: int,
    ) -> SerpApiSearchResponse:
        self.calls.append(
            {
                "query": query,
                "country": country,
                "city": city,
                "industry": industry,
                "limit": limit,
            }
        )

        if self.error is not None:
            raise self.error

        if self.response is None:
            raise AssertionError("FakeSerpApiClient response is required.")

        return self.response


def make_service(fake_client: FakeSerpApiClient) -> CompanyDiscoveryService:
    return CompanyDiscoveryService(cast(SerpApiClient, fake_client))


def make_request() -> CompanyDiscoveryRequest:
    return CompanyDiscoveryRequest(
        query="software companies",
        country="Indonesia",
        city="Bali",
        industry="SaaS",
        limit=10,
    )


def test_successful_discovery_with_multiple_results() -> None:
    fake_client = FakeSerpApiClient(
        SerpApiSearchResponse(
            query="software companies SaaS Bali Indonesia",
            results=[
                SerpApiCompanyResult(
                    position=1,
                    title="First",
                    link="https://first.example",
                    snippet="First snippet",
                    source=None,
                ),
                SerpApiCompanyResult(
                    position=2,
                    title="Second",
                    link="https://second.example",
                    snippet="Second snippet",
                    source=None,
                ),
            ],
        )
    )

    result = make_service(fake_client).discover_from_serpapi(make_request())

    assert result.provider == "serpapi"
    assert result.query == "software companies SaaS Bali Indonesia"
    assert result.total_results == 2
    assert result.errors == []
    assert [item.name for item in result.items] == ["First", "Second"]


def test_result_order_is_preserved() -> None:
    fake_client = FakeSerpApiClient(
        SerpApiSearchResponse(
            query="ordered query",
            results=[
                SerpApiCompanyResult(
                    position=3,
                    title="Third",
                    link=None,
                    snippet=None,
                    source=None,
                ),
                SerpApiCompanyResult(
                    position=1,
                    title="First",
                    link=None,
                    snippet=None,
                    source=None,
                ),
            ],
        )
    )

    result = make_service(fake_client).discover_from_serpapi(make_request())

    assert [item.name for item in result.items] == ["Third", "First"]
    assert [item.source_row_number for item in result.items] == [3, 1]


def test_provider_query_is_preserved_in_final_result() -> None:
    fake_client = FakeSerpApiClient(
        SerpApiSearchResponse(
            query="provider-built-query",
            results=[],
        )
    )

    result = make_service(fake_client).discover_from_serpapi(make_request())

    assert result.query == "provider-built-query"


def test_empty_provider_results() -> None:
    fake_client = FakeSerpApiClient(SerpApiSearchResponse(query="empty query", results=[]))

    result = make_service(fake_client).discover_from_serpapi(make_request())

    assert result.total_results == 0
    assert result.items == []
    assert result.errors == []


def test_one_invalid_result_creates_error_while_valid_results_remain() -> None:
    fake_client = FakeSerpApiClient(
        SerpApiSearchResponse(
            query="mixed query",
            results=[
                SerpApiCompanyResult(
                    position=1,
                    title="Valid",
                    link=None,
                    snippet=None,
                    source=None,
                ),
                SerpApiCompanyResult(
                    position=2,
                    title=" ",
                    link=None,
                    snippet=None,
                    source=None,
                ),
            ],
        )
    )

    result = make_service(fake_client).discover_from_serpapi(make_request())

    assert [item.name for item in result.items] == ["Valid"]
    assert len(result.errors) == 1
    assert result.errors[0].source_row_number == 2
    assert result.errors[0].code == "invalid_discovery_result"


def test_multiple_invalid_results_create_multiple_errors() -> None:
    fake_client = FakeSerpApiClient(
        SerpApiSearchResponse(
            query="invalid query",
            results=[
                SerpApiCompanyResult(
                    position=1,
                    title=" ",
                    link=None,
                    snippet=None,
                    source=None,
                ),
                SerpApiCompanyResult(
                    position=2,
                    title="\t",
                    link=None,
                    snippet=None,
                    source=None,
                ),
            ],
        )
    )

    result = make_service(fake_client).discover_from_serpapi(make_request())

    assert result.items == []
    assert len(result.errors) == 2
    assert [error.source_row_number for error in result.errors] == [1, 2]


def test_total_results_invariant() -> None:
    fake_client = FakeSerpApiClient(
        SerpApiSearchResponse(
            query="invariant query",
            results=[
                SerpApiCompanyResult(
                    position=1,
                    title="Valid",
                    link=None,
                    snippet=None,
                    source=None,
                ),
                SerpApiCompanyResult(
                    position=2,
                    title=" ",
                    link=None,
                    snippet=None,
                    source=None,
                ),
            ],
        )
    )

    result = make_service(fake_client).discover_from_serpapi(make_request())

    assert result.total_results == len(result.items) + len(result.errors)


def test_request_parameters_are_passed_to_serpapi_client() -> None:
    fake_client = FakeSerpApiClient(SerpApiSearchResponse(query="query", results=[]))
    request = CompanyDiscoveryRequest(
        query="software",
        country="Indonesia",
        city="Bali",
        industry="SaaS",
        limit=7,
    )

    make_service(fake_client).discover_from_serpapi(request)

    assert fake_client.calls == [
        {
            "query": "software",
            "country": "Indonesia",
            "city": "Bali",
            "industry": "SaaS",
            "limit": 7,
        }
    ]


def test_controlled_serpapi_configuration_error_propagates() -> None:
    fake_client = FakeSerpApiClient(error=SerpApiConfigurationError("missing key"))

    with pytest.raises(SerpApiConfigurationError, match="missing key"):
        make_service(fake_client).discover_from_serpapi(make_request())


def test_controlled_serpapi_rate_limit_error_propagates() -> None:
    fake_client = FakeSerpApiClient(error=SerpApiRateLimitError("rate limit"))

    with pytest.raises(SerpApiRateLimitError, match="rate limit"):
        make_service(fake_client).discover_from_serpapi(make_request())


def test_controlled_serpapi_request_error_propagates() -> None:
    fake_client = FakeSerpApiClient(error=SerpApiRequestError("request failed"))

    with pytest.raises(SerpApiRequestError, match="request failed"):
        make_service(fake_client).discover_from_serpapi(make_request())


def test_no_database_interaction() -> None:
    fake_client = FakeSerpApiClient(SerpApiSearchResponse(query="query", results=[]))

    result = make_service(fake_client).discover_from_serpapi(make_request())

    assert result.total_results == 0


def test_company_ingestion_service_is_not_used(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = FakeSerpApiClient(SerpApiSearchResponse(query="query", results=[]))

    def unexpected_ingest(
        self: CompanyIngestionService, project_id: int, items: list[object]
    ) -> None:
        raise AssertionError("CompanyIngestionService must not be used in discovery dry-run.")

    monkeypatch.setattr(CompanyIngestionService, "ingest", unexpected_ingest)

    result = make_service(fake_client).discover_from_serpapi(make_request())

    assert result.total_results == 0


def test_request_normalizes_blank_values_and_rejects_empty_search() -> None:
    request = CompanyDiscoveryRequest(
        query="  software  ",
        country=" ",
        city=None,
        industry="\t",
    )

    assert request.query == "software"
    assert request.country is None
    assert request.industry is None

    with pytest.raises(ValueError, match="At least one discovery search field"):
        CompanyDiscoveryRequest(query=" ", country=None, city="", industry="\t")
