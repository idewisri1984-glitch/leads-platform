from collections.abc import Callable, Iterator
from typing import cast
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import ValidationError

from app.providers.serpapi import SerpApiClient, SerpApiSearchResponse
from app.providers.serpapi.exceptions import (
    SerpApiAuthenticationError,
    SerpApiConfigurationError,
    SerpApiProviderError,
    SerpApiQuotaExceededError,
    SerpApiRateLimitError,
    SerpApiRequestError,
    SerpApiResponseError,
    SerpApiResponseTooLargeError,
)

API_KEY = "test-serpapi-key"
BASE_URL = "https://serpapi.test/search.json"
_RAW_PAYLOAD_MARKER = "raw payload marker"


def make_client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    api_key: str | None = API_KEY,
    max_response_bytes: int = 2_000_000,
) -> SerpApiClient:
    return SerpApiClient(
        api_key=api_key,
        base_url=BASE_URL,
        timeout_seconds=5.0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_response_bytes=max_response_bytes,
    )


def make_default_client(handler: Callable[[httpx.Request], httpx.Response]) -> SerpApiClient:
    return SerpApiClient(
        api_key=API_KEY,
        base_url=BASE_URL,
        timeout_seconds=5.0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_default_client_constructor_uses_default_max_response_bound_for_requests() -> None:
    client = make_default_client(
        lambda request: httpx.Response(
            200,
            json={"search_metadata": {"status": "Success"}, "organic_results": []},
        )
    )
    response = client.search_companies(
        query="companies", country=None, city=None, industry=None, limit=10
    )
    assert response.results == []

    oversized_request_count = 0

    def oversized_handler(request: httpx.Request) -> httpx.Response:
        nonlocal oversized_request_count
        oversized_request_count += 1
        body = b"x" * (2_000_001)
        return httpx.Response(200, content=body)

    oversized_client = SerpApiClient(
        api_key=API_KEY,
        base_url=BASE_URL,
        timeout_seconds=5.0,
        http_client=httpx.Client(transport=httpx.MockTransport(oversized_handler)),
    )
    with pytest.raises(SerpApiResponseTooLargeError):
        oversized_client.search_companies(
            query="companies",
            country=None,
            city=None,
            industry=None,
            limit=10,
        )
    assert oversized_request_count == 1


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


def test_request_without_iso_country_code_has_no_gl_param() -> None:
    captured_params: dict[str, str] = {}

    def recording_handler(request: httpx.Request) -> httpx.Response:
        params = parse_qs(request.url.query.decode())
        captured_params.update({key: value[0] for key, value in params.items()})
        return httpx.Response(200, json={"organic_results": []})

    client = make_client(recording_handler)
    client.search_companies(query="companies", country=None, city=None, industry=None, limit=10)

    assert "gl" not in captured_params


def test_request_with_iso_country_code_emits_serpapi_gl() -> None:
    captured_params: dict[str, str] = {}

    def recording_handler(request: httpx.Request) -> httpx.Response:
        params = parse_qs(request.url.query.decode())
        captured = {key: value[0] for key, value in params.items()}
        captured_params.update(captured)
        return httpx.Response(200, json={"organic_results": []})

    make_client(recording_handler).search_companies(
        query="companies",
        country=None,
        city=None,
        industry=None,
        limit=10,
        iso_country_code="GB",
    )

    assert captured_params["gl"] == "uk"


def test_invalid_iso_country_code_rejected_before_request() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"organic_results": []})

    with pytest.raises(SerpApiRequestError, match="SerpAPI country code was invalid."):
        make_client(handler).search_companies(
            query="companies",
            country=None,
            city=None,
            industry=None,
            limit=10,
            iso_country_code=cast(str | None, 123),
        )

    assert calls == 0


def test_iso_country_code_can_be_omitted_without_side_effect() -> None:
    make_client(lambda request: httpx.Response(200, json={"organic_results": []})).search_companies(
        query="companies",
        country=None,
        city=None,
        industry=None,
        limit=10,
    )


def test_iso_country_code_can_be_lowercase() -> None:
    captured_params: dict[str, str] = {}

    def recording_handler(request: httpx.Request) -> httpx.Response:
        params = parse_qs(request.url.query.decode())
        captured_params.update({key: value[0] for key, value in params.items()})
        return httpx.Response(200, json={"organic_results": []})

    make_client(recording_handler).search_companies(
        query="companies",
        country=None,
        city=None,
        industry=None,
        limit=10,
        iso_country_code="gb",
    )

    assert captured_params["gl"] == "uk"


def test_iso_country_code_emits_us_gl() -> None:
    captured_params: dict[str, str] = {}

    def recording_handler(request: httpx.Request) -> httpx.Response:
        params = parse_qs(request.url.query.decode())
        captured_params.update({key: value[0] for key, value in params.items()})
        return httpx.Response(200, json={"organic_results": []})

    make_client(recording_handler).search_companies(
        query="companies",
        country=None,
        city=None,
        industry=None,
        limit=10,
        iso_country_code="US",
    )

    assert captured_params["gl"] == "us"


@pytest.mark.parametrize("value", ["UK", "ZZ", "SU", "", " ", True, 123])
def test_invalid_iso_country_code_values_are_rejected_without_requests(value: object) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"organic_results": []})

    with pytest.raises(SerpApiRequestError, match="SerpAPI country code was invalid."):
        make_client(handler).search_companies(
            query="companies",
            country=None,
            city=None,
            industry=None,
            limit=10,
            iso_country_code=cast(str | None, value),
        )

    assert calls == 0


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


def test_success_status_with_non_empty_organic_results_is_parsed() -> None:
    client = make_client(
        lambda request: httpx.Response(
            200,
            json={
                "search_metadata": {"status": "Success"},
                "search_information": {"total_results": 3},
                "organic_results": [
                    {
                        "position": 7,
                        "title": "Acme Bali",
                        "link": "https://acme.example",
                        "snippet": "Software company in Bali.",
                        "source": "Acme",
                    }
                ],
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

    assert response.query == "software"
    assert response.total_results == 3
    assert len(response.results) == 1
    assert response.results[0].position == 7


def test_success_status_with_explicit_empty_organic_results_is_parsed() -> None:
    client = make_client(
        lambda request: httpx.Response(
            200,
            json={
                "search_metadata": {"status": "Success"},
                "search_information": {"total_results": 0, "organic_results_state": "No Results"},
                "organic_results": [],
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

    assert response.total_results == 0
    assert response.results == []


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


def test_http_500_raises_controlled_provider_error() -> None:
    client = make_client(lambda request: httpx.Response(500, json={"error": "server error"}))

    with pytest.raises(SerpApiProviderError, match="provider request failed"):
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
    assert exc_info.value.__cause__ is None


def test_timeout_error_does_not_chain_unsafe_httpx_exception() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timeout with test-serpapi-key", request=request)

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
    assert exc_info.value.__cause__ is None


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

    with pytest.raises(SerpApiProviderError) as exc_info:
        client.search_companies(
            query="companies",
            country=None,
            city=None,
            industry=None,
            limit=10,
        )

    assert API_KEY not in str(exc_info.value)


def _raise_configuration_error() -> None:
    client = make_client(
        lambda request: httpx.Response(200, json={"organic_results": []}), api_key=None
    )
    client.search_companies(query="companies", country=None, city=None, industry=None, limit=10)


def _raise_auth_error() -> None:
    client = make_client(lambda request: httpx.Response(401, content=API_KEY))
    client.search_companies(query="companies", country=None, city=None, industry=None, limit=10)


def _raise_quota_error() -> None:
    client = make_client(
        lambda request: httpx.Response(
            429, json={"error": "You have reached your searches per month limit."}
        )
    )
    client.search_companies(query="companies", country=None, city=None, industry=None, limit=10)


def _raise_rate_limit_error() -> None:
    client = make_client(lambda request: httpx.Response(429, json={"error": "limited quickly"}))
    client.search_companies(query="companies", country=None, city=None, industry=None, limit=10)


def _raise_request_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"network failed with {API_KEY}", request=request)

    client = make_client(handler)
    client.search_companies(query="companies", country=None, city=None, industry=None, limit=10)


def _raise_response_too_large_error() -> None:
    stream = TrackingStream([API_KEY.encode() * 200_000])
    client = make_client(
        lambda request: httpx.Response(
            200,
            headers={"content-length": "2000001"},
            stream=stream,
            request=request,
        )
    )
    client.search_companies(query="companies", country=None, city=None, industry=None, limit=10)


def _raise_response_error() -> None:
    client = make_client(lambda request: httpx.Response(200, content=b"{not-json"))
    client.search_companies(query="companies", country=None, city=None, industry=None, limit=10)


def _raise_provider_error() -> None:
    client = make_client(lambda request: httpx.Response(500, json={"error": API_KEY}))
    client.search_companies(query="companies", country=None, city=None, industry=None, limit=10)


@pytest.mark.parametrize(
    (
        "trigger",
        "expected_type",
        "expected_message",
    ),
    [
        (
            _raise_configuration_error,
            SerpApiConfigurationError,
            "SERPAPI_API_KEY is required to use SerpAPI.",
        ),
        (_raise_auth_error, SerpApiAuthenticationError, "SerpAPI authentication failed."),
        (_raise_quota_error, SerpApiQuotaExceededError, "SerpAPI quota was exceeded."),
        (_raise_rate_limit_error, SerpApiRateLimitError, "SerpAPI rate limit exceeded."),
        (_raise_request_error, SerpApiRequestError, "SerpAPI request failed."),
        (
            _raise_response_too_large_error,
            SerpApiResponseTooLargeError,
            "SerpAPI response exceeded the allowed size.",
        ),
        (_raise_response_error, SerpApiResponseError, "SerpAPI response was not valid JSON."),
        (_raise_provider_error, SerpApiProviderError, "SerpAPI provider request failed."),
    ],
)
def test_controlled_errors_are_sanitized(
    trigger: Callable[[], None],
    expected_type: type[Exception],
    expected_message: str,
) -> None:
    with pytest.raises(expected_type) as error:
        trigger()

    assert str(error.value) == expected_message
    assert API_KEY not in str(error.value)
    assert _RAW_PAYLOAD_MARKER not in str(error.value)
    assert _RAW_PAYLOAD_MARKER not in repr(error.value)
    assert API_KEY not in repr(error.value)
    assert BASE_URL not in str(error.value)
    assert BASE_URL not in repr(error.value)
    assert "q=" not in str(error.value)
    assert "api_key=" not in str(error.value)
    assert "headers=" not in repr(error.value)
    assert error.value.__cause__ is None


class TrackingStream(httpx.SyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.closed = False

    def __iter__(self) -> Iterator[bytes]:
        yield from self.chunks

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize("value", [True, False, 0, -1, 20_000_001, 1.5, "100"])
def test_invalid_max_response_bytes_fails_before_request(value: object) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"organic_results": []})

    with pytest.raises(SerpApiConfigurationError, match="byte limit"):
        make_client(handler, max_response_bytes=value)  # type: ignore[arg-type]
    assert calls == 0


@pytest.mark.parametrize("limit", [0, 101, True])
def test_invalid_result_limits_are_rejected_without_request(limit: int) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"organic_results": []})

    with pytest.raises(SerpApiRequestError, match="between 1 and 100"):
        make_client(handler).search_companies(
            query="companies", country=None, city=None, industry=None, limit=limit
        )
    assert calls == 0


def test_request_sends_restrictor_timeout_and_exactly_once() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        params = parse_qs(request.url.query.decode())
        restrictor = params["json_restrictor"][0]
        assert "search_metadata[status]" in restrictor
        assert "search_information[total_results,organic_results_state]" in restrictor
        assert "organic_results[position,title,link,snippet,source]" in restrictor
        assert request.extensions["timeout"] == {
            "connect": 5.0,
            "read": 5.0,
            "write": 5.0,
            "pool": 5.0,
        }
        return httpx.Response(200, json={"organic_results": []})

    make_client(handler).search_companies(
        query="companies", country=None, city=None, industry=None, limit=10
    )
    assert calls == 1


def test_json_restrictor_contains_only_expected_paths() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        params = parse_qs(request.url.query.decode())
        restrictor = params["json_restrictor"][0]
        parts: list[str] = []
        current: list[str] = []
        bracket_depth = 0
        for character in restrictor:
            if character == "[":
                bracket_depth += 1
            elif character == "]":
                bracket_depth -= 1
            elif character == "," and bracket_depth == 0:
                parts.append("".join(current))
                current = []
                continue
            current.append(character)
        parts.append("".join(current))
        expected_paths = {
            "search_metadata[status]",
            "search_information[total_results,organic_results_state]",
            "error",
            "organic_results[position,title,link,snippet,source]",
        }
        assert set(parts) == expected_paths
        assert "search_parameters" not in restrictor
        assert "account api" not in restrictor.casefold()
        assert API_KEY not in restrictor
        return httpx.Response(200, json={"organic_results": []})

    make_client(handler).search_companies(
        query="companies",
        country=None,
        city=None,
        industry=None,
        limit=10,
    )

    assert calls == 1


def test_content_length_over_limit_is_rejected_and_stream_closed() -> None:
    stream = TrackingStream([b"not-read"])
    client = make_client(
        lambda request: httpx.Response(
            200, headers={"content-length": "11"}, stream=stream, request=request
        ),
        max_response_bytes=10,
    )
    with pytest.raises(SerpApiResponseTooLargeError, match="allowed size") as error:
        client.search_companies(query="companies", country=None, city=None, industry=None, limit=10)
    assert stream.closed is True
    assert error.value.__cause__ is None


def test_streamed_body_over_limit_is_rejected_and_stream_closed() -> None:
    stream = TrackingStream([b"12345", b"678901"])
    client = make_client(
        lambda request: httpx.Response(200, stream=stream, request=request),
        max_response_bytes=10,
    )
    with pytest.raises(SerpApiResponseTooLargeError):
        client.search_companies(query="companies", country=None, city=None, industry=None, limit=10)
    assert stream.closed is True


def test_body_exactly_at_limit_is_allowed_and_stream_closed() -> None:
    body = b'{"organic_results":[]}'
    stream = TrackingStream([body[:5], body[5:]])
    client = make_client(
        lambda request: httpx.Response(200, stream=stream, request=request),
        max_response_bytes=len(body),
    )
    response = client.search_companies(
        query="companies", country=None, city=None, industry=None, limit=10
    )
    assert response.results == []
    assert stream.closed is True


def test_invalid_content_length_still_uses_stream_bound() -> None:
    stream = TrackingStream([b"123456"])
    client = make_client(
        lambda request: httpx.Response(
            200, headers={"content-length": "invalid"}, stream=stream, request=request
        ),
        max_response_bytes=5,
    )
    with pytest.raises(SerpApiResponseTooLargeError):
        client.search_companies(query="companies", country=None, city=None, industry=None, limit=10)


def test_error_status_response_exceeding_limit_is_rejected_as_too_large() -> None:
    calls = 0
    stream = TrackingStream([_RAW_PAYLOAD_MARKER.encode() * 300_000])

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            500,
            headers={"content-length": "2000001"},
            stream=stream,
            request=request,
        )

    client = make_default_client(handler)
    with pytest.raises(SerpApiResponseTooLargeError) as error:
        client.search_companies(query="companies", country=None, city=None, industry=None, limit=10)

    assert API_KEY not in str(error.value)
    assert _RAW_PAYLOAD_MARKER not in repr(error.value)
    assert BASE_URL not in repr(error.value)
    assert stream.closed is True
    assert calls == 1
    assert error.value.__cause__ is None


def test_non_object_json_is_rejected() -> None:
    client = make_client(lambda request: httpx.Response(200, json=[]))
    with pytest.raises(SerpApiResponseError, match="must be an object"):
        client.search_companies(query="companies", country=None, city=None, industry=None, limit=10)


@pytest.mark.parametrize("status", [401, 403])
def test_authentication_statuses_are_classified_safely(status: int) -> None:
    client = make_client(lambda request: httpx.Response(status, content=API_KEY))
    with pytest.raises(SerpApiAuthenticationError) as error:
        client.search_companies(query="companies", country=None, city=None, industry=None, limit=10)
    assert str(error.value) == "SerpAPI authentication failed."
    assert API_KEY not in str(error.value)


def test_known_quota_message_is_distinct_from_unknown_rate_limit() -> None:
    quota = make_client(
        lambda request: httpx.Response(
            429, json={"error": "  You have reached your searches per month limit.  "}
        )
    )
    with pytest.raises(SerpApiQuotaExceededError, match="quota was exceeded"):
        quota.search_companies(query="companies", country=None, city=None, industry=None, limit=10)
    for body in ({"error": "unknown quota detail"}, b"not-json"):
        if isinstance(body, dict):
            response = httpx.Response(429, json=body)
        else:
            response = httpx.Response(429, content=body)

        def handler(
            request: httpx.Request, response_value: httpx.Response = response
        ) -> httpx.Response:
            return response_value

        client = make_client(handler)
        with pytest.raises(SerpApiRateLimitError):
            client.search_companies(
                query="companies", country=None, city=None, industry=None, limit=10
            )


@pytest.mark.parametrize("status", [400, 404, 410, 418])
def test_non_authentication_4xx_is_request_error(status: int) -> None:
    client = make_client(lambda request: httpx.Response(status, json={"error": API_KEY}))
    with pytest.raises(SerpApiRequestError) as error:
        client.search_companies(query="companies", country=None, city=None, industry=None, limit=10)
    assert API_KEY not in str(error.value)


@pytest.mark.parametrize("status", [500, 503])
def test_5xx_is_provider_error(status: int) -> None:
    client = make_client(lambda request: httpx.Response(status, json={"error": API_KEY}))
    with pytest.raises(SerpApiProviderError) as error:
        client.search_companies(query="companies", country=None, city=None, industry=None, limit=10)
    assert str(error.value) == "SerpAPI provider request failed."
    assert API_KEY not in str(error.value)


@pytest.mark.parametrize("status", ["Error", "Processing", "Queued"])
def test_nonterminal_or_error_search_status_is_provider_error(status: str) -> None:
    client = make_client(
        lambda request: httpx.Response(
            200, json={"search_metadata": {"status": status}, "error": API_KEY}
        )
    )
    with pytest.raises(SerpApiProviderError) as error:
        client.search_companies(query="companies", country=None, city=None, industry=None, limit=10)
    assert API_KEY not in str(error.value)


def test_unknown_search_status_is_response_error() -> None:
    client = make_client(
        lambda request: httpx.Response(
            200, json={"search_metadata": {"status": "Unknown"}, "organic_results": []}
        )
    )
    with pytest.raises(SerpApiResponseError, match="status was invalid"):
        client.search_companies(query="companies", country=None, city=None, industry=None, limit=10)


@pytest.mark.parametrize(
    "evidence",
    [
        {"search_information": {"total_results": 0}},
        {"search_information": {"organic_results_state": "Fully Empty"}},
        {"error": "Google hasn't returned any results for this query."},
    ],
)
def test_success_with_explicit_no_results_evidence(evidence: dict[str, object]) -> None:
    payload: dict[str, object] = {"search_metadata": {"status": "Success"}, **evidence}
    response = make_client(lambda request: httpx.Response(200, json=payload)).search_companies(
        query="companies", country=None, city=None, industry=None, limit=10
    )
    assert response.results == []


def test_success_without_results_or_empty_evidence_is_invalid() -> None:
    client = make_client(
        lambda request: httpx.Response(200, json={"search_metadata": {"status": "Success"}})
    )
    with pytest.raises(SerpApiResponseError, match="organic results"):
        client.search_companies(query="companies", country=None, city=None, industry=None, limit=10)


def test_wrong_organic_results_type_is_invalid() -> None:
    client = make_client(lambda request: httpx.Response(200, json={"organic_results": {}}))
    with pytest.raises(SerpApiResponseError, match="must be a list"):
        client.search_companies(query="companies", country=None, city=None, industry=None, limit=10)


@pytest.mark.parametrize("position", [True, False, 0, -1, 1.5, "1", ""])
def test_invalid_position_values_are_sanitized_to_none(position: object) -> None:
    response = make_client(
        lambda request: httpx.Response(
            200,
            json={
                "organic_results": [
                    {"title": "Valid Company", "position": position, "link": "https://example.com"}
                ]
            },
        )
    ).search_companies(query="companies", country=None, city=None, industry=None, limit=10)

    assert len(response.results) == 1
    assert response.results[0].position is None
    assert response.results[0].title == "Valid Company"
    assert response.results[0].link == "https://example.com"


def test_none_position_is_sanitized_to_none() -> None:
    response = make_client(
        lambda request: httpx.Response(
            200,
            json={"organic_results": [{"title": "Valid Company", "position": None}]},
        )
    ).search_companies(query="companies", country=None, city=None, industry=None, limit=10)

    assert response.results[0].position is None


def test_valid_positive_position_is_preserved() -> None:
    response = make_client(
        lambda request: httpx.Response(
            200,
            json={"organic_results": [{"title": "Valid Company", "position": 12}]},
        )
    ).search_companies(query="companies", country=None, city=None, industry=None, limit=10)
    assert response.results[0].position == 12


def test_malformed_items_and_fields_are_bounded_and_skipped_safely() -> None:
    payload = {
        "organic_results": [
            "raw-secret",
            {"title": " "},
            {"title": "x" * 501, "snippet": API_KEY},
            {
                "position": True,
                "title": " Valid Company ",
                "link": "x" * 2049,
                "snippet": "x" * 2001,
                "source": "x" * 256,
                "unknown": API_KEY,
            },
            {"position": 2, "title": "Second"},
        ]
    }
    response = make_client(lambda request: httpx.Response(200, json=payload)).search_companies(
        query="companies", country=None, city=None, industry=None, limit=2
    )
    assert [item.title for item in response.results] == ["Valid Company", "Second"]
    assert response.results[0].position is None
    assert response.results[0].link is None
    assert response.results[0].snippet is None
    assert response.results[0].source is None
    assert "unknown" not in response.results[0].model_dump()


def test_raw_payload_is_not_retained_in_parsed_response_models() -> None:
    sensitive_marker = "sensitive fake marker"
    payload: dict[str, object] = {
        "search_metadata": {"status": "Success"},
        "search_information": {"total_results": 1},
        "error": {"raw": sensitive_marker},
        "organic_results": [
            {
                "position": 1,
                "title": "Acme",
                "link": "https://acme.example",
                "snippet": "Snippet",
                "source": "Directory",
                "secret": sensitive_marker,
            }
        ],
        "raw_payload_marker": sensitive_marker,
    }
    response = make_client(lambda request: httpx.Response(200, json=payload)).search_companies(
        query="companies", country=None, city=None, industry=None, limit=10
    )

    dump = response.model_dump()
    assert "raw_payload_marker" not in dump
    assert "error" not in dump["results"][0]
    assert "secret" not in dump["results"][0]
    assert sensitive_marker not in response.model_dump_json()


@pytest.mark.parametrize("value", [True, -1, "10", 1.5])
def test_malformed_total_results_is_ignored(value: object) -> None:
    response = make_client(
        lambda request: httpx.Response(
            200,
            json={
                "search_information": {"total_results": value},
                "organic_results": [],
            },
        )
    ).search_companies(query="companies", country=None, city=None, industry=None, limit=10)
    assert response.total_results is None


@pytest.mark.parametrize("value", [None, 0, 1, 10])
def test_serpapi_search_response_accepts_valid_total_results(value: object) -> None:
    response = SerpApiSearchResponse(query="companies", results=[], total_results=value)
    assert response.total_results == value


@pytest.mark.parametrize("value", [True, -1, "10", 1.5])
def test_serpapi_search_response_rejects_invalid_total_results(value: object) -> None:
    with pytest.raises(ValidationError):
        SerpApiSearchResponse(query="companies", results=[], total_results=value)


def test_safe_total_results_is_parsed() -> None:
    response = make_client(
        lambda request: httpx.Response(
            200,
            json={"search_information": {"total_results": 42}, "organic_results": []},
        )
    ).search_companies(query="companies", country=None, city=None, industry=None, limit=10)
    assert response.total_results == 42
