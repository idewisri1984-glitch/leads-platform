from collections.abc import Callable
from urllib.parse import parse_qs

import httpx
import pytest

from app.providers.serpapi import SerpApiClient
from app.providers.serpapi.exceptions import (
    SerpApiConfigurationError,
    SerpApiRateLimitError,
    SerpApiRequestError,
    SerpApiResponseError,
)

API_KEY = "test-serpapi-key"
BASE_URL = "https://serpapi.test/search.json"


def make_client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    api_key: str | None = API_KEY,
) -> SerpApiClient:
    return SerpApiClient(
        api_key=api_key,
        base_url=BASE_URL,
        timeout_seconds=5.0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_missing_api_key_raises_controlled_configuration_error() -> None:
    client = make_client(
        lambda request: httpx.Response(200, json={"organic_results": []}),
        api_key=None,
    )

    with pytest.raises(SerpApiConfigurationError, match="SERPAPI_API_KEY"):
        client.search_companies(
            query="hotels",
            country="Indonesia",
            city="Bali",
            industry=None,
            limit=10,
        )


def test_request_query_is_constructed_correctly() -> None:
    captured_query: str | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_query
        params = parse_qs(request.url.query.decode())
        captured_query = params["q"][0]
        assert params["engine"] == ["google"]
        assert params["num"] == ["10"]
        assert params["api_key"] == [API_KEY]
        return httpx.Response(200, json={"organic_results": []})

    client = make_client(handler)

    response = client.search_companies(
        query="software companies",
        country="Indonesia",
        city="Bali",
        industry="SaaS",
        limit=10,
    )

    assert captured_query == "software companies SaaS Bali Indonesia"
    assert response.query == "software companies SaaS Bali Indonesia"


def test_empty_optional_query_parts_are_omitted() -> None:
    captured_query: str | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_query
        captured_query = parse_qs(request.url.query.decode())["q"][0]
        return httpx.Response(200, json={"organic_results": []})

    client = make_client(handler)

    client.search_companies(
        query="  ",
        country="Indonesia",
        city=None,
        industry="",
        limit=5,
    )

    assert captured_query == "Indonesia"


def test_organic_results_parse_correctly() -> None:
    client = make_client(
        lambda request: httpx.Response(
            200,
            json={
                "organic_results": [
                    {
                        "position": 1,
                        "title": "Acme Bali",
                        "link": "https://acme.example",
                        "snippet": "Software company in Bali.",
                        "source": "Acme",
                    }
                ]
            },
        )
    )

    response = client.search_companies(
        query="software",
        country=None,
        city=None,
        industry=None,
        limit=10,
    )

    assert len(response.results) == 1
    assert response.results[0].position == 1
    assert response.results[0].title == "Acme Bali"
    assert response.results[0].link == "https://acme.example"
    assert response.results[0].snippet == "Software company in Bali."
    assert response.results[0].source == "Acme"


def test_result_limit_is_respected() -> None:
    client = make_client(
        lambda request: httpx.Response(
            200,
            json={
                "organic_results": [
                    {"position": 1, "title": "First"},
                    {"position": 2, "title": "Second"},
                    {"position": 3, "title": "Third"},
                ]
            },
        )
    )

    response = client.search_companies(
        query="companies",
        country=None,
        city=None,
        industry=None,
        limit=2,
    )

    assert [result.title for result in response.results] == ["First", "Second"]


def test_empty_organic_results_returns_empty_successful_response() -> None:
    client = make_client(lambda request: httpx.Response(200, json={"organic_results": []}))

    response = client.search_companies(
        query="companies",
        country=None,
        city=None,
        industry=None,
        limit=10,
    )

    assert response.results == []


def test_missing_organic_results_raises_response_error() -> None:
    client = make_client(lambda request: httpx.Response(200, json={"search_metadata": {}}))

    with pytest.raises(SerpApiResponseError, match="organic results"):
        client.search_companies(
            query="companies",
            country=None,
            city=None,
            industry=None,
            limit=10,
        )


def test_http_429_raises_controlled_rate_limit_error() -> None:
    client = make_client(lambda request: httpx.Response(429, json={"error": "rate limited"}))

    with pytest.raises(SerpApiRateLimitError, match="rate limit"):
        client.search_companies(
            query="companies",
            country=None,
            city=None,
            industry=None,
            limit=10,
        )


def test_http_500_raises_controlled_request_error() -> None:
    client = make_client(lambda request: httpx.Response(500, json={"error": "server error"}))

    with pytest.raises(SerpApiRequestError, match="unsuccessful status"):
        client.search_companies(
            query="companies",
            country=None,
            city=None,
            industry=None,
            limit=10,
        )


def test_timeout_or_network_error_raises_controlled_request_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network failed with test-serpapi-key", request=request)

    client = make_client(handler)

    with pytest.raises(SerpApiRequestError, match="request failed") as exc_info:
        client.search_companies(
            query="companies",
            country=None,
            city=None,
            industry=None,
            limit=10,
        )

    assert API_KEY not in str(exc_info.value)


def test_malformed_json_raises_controlled_response_error() -> None:
    client = make_client(lambda request: httpx.Response(200, content=b"{not-json"))

    with pytest.raises(SerpApiResponseError, match="valid JSON"):
        client.search_companies(
            query="companies",
            country=None,
            city=None,
            industry=None,
            limit=10,
        )


def test_api_key_does_not_appear_in_controlled_exception_messages() -> None:
    client = make_client(lambda request: httpx.Response(500, json={"error": API_KEY}))

    with pytest.raises(SerpApiRequestError) as exc_info:
        client.search_companies(
            query="companies",
            country=None,
            city=None,
            industry=None,
            limit=10,
        )

    assert API_KEY not in str(exc_info.value)
