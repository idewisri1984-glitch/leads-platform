import json
from typing import Any

import httpx

from app.core.country_targets import get_country_target
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
from app.providers.serpapi.schemas import (
    MAX_LINK_LENGTH,
    MAX_SNIPPET_LENGTH,
    MAX_SOURCE_LENGTH,
    MAX_TITLE_LENGTH,
    SerpApiCompanyResult,
    SerpApiSearchResponse,
)

DEFAULT_MAX_RESPONSE_BYTES = 2_000_000
_MAX_CONFIGURED_RESPONSE_BYTES = 20_000_000
_JSON_RESTRICTOR = (
    "search_metadata[status],search_information[total_results,organic_results_state],"
    "error,organic_results[position,title,link,snippet,source]"
)
_QUOTA_MESSAGES = frozenset(
    {
        "you have reached your searches per month limit.",
        "your account has run out of searches.",
        "you have no searches left.",
    }
)
_EMPTY_ORGANIC_STATES = frozenset({"empty", "fully empty", "no results"})
_NO_RESULTS_MESSAGES = frozenset({"google hasn't returned any results for this query."})


class SerpApiClient:
    """
    Minimal SerpAPI Google organic search client.
    """

    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str,
        timeout_seconds: float,
        http_client: httpx.Client | None = None,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        if (
            isinstance(max_response_bytes, bool)
            or not isinstance(max_response_bytes, int)
            or not 1 <= max_response_bytes <= _MAX_CONFIGURED_RESPONSE_BYTES
        ):
            raise SerpApiConfigurationError("SerpAPI response byte limit is invalid.")
        self._api_key = api_key.strip() if api_key is not None else None
        self._base_url = base_url
        self._timeout_seconds = timeout_seconds
        self._http_client = http_client or httpx.Client()
        self._max_response_bytes = max_response_bytes

    def search_companies(
        self,
        *,
        query: str | None,
        country: str | None,
        city: str | None,
        industry: str | None,
        limit: int,
        iso_country_code: str | None = None,
    ) -> SerpApiSearchResponse:
        if not self._api_key:
            raise SerpApiConfigurationError("SERPAPI_API_KEY is required to use SerpAPI.")

        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise SerpApiRequestError("SerpAPI result limit must be between 1 and 100.")

        request_google_country_code = self._normalize_iso_country_code(iso_country_code)

        search_query = self._build_search_query(
            query=query,
            country=country,
            city=city,
            industry=industry,
        )

        params: dict[str, str | int] = {
            "engine": "google",
            "q": search_query,
            "api_key": self._api_key,
            "num": limit,
            "json_restrictor": _JSON_RESTRICTOR,
        }
        if request_google_country_code is not None:
            params["gl"] = request_google_country_code

        try:
            with self._http_client.stream(
                "GET", self._base_url, params=params, timeout=self._timeout_seconds
            ) as response:
                status_code = response.status_code
                body = self._read_bounded_body(response)
        except httpx.HTTPError:
            raise SerpApiRequestError("SerpAPI request failed.") from None

        if status_code in {401, 403}:
            raise SerpApiAuthenticationError("SerpAPI authentication failed.")

        if status_code == 429:
            payload = self._try_json_object(body)
            if payload is not None and self._is_quota_error(payload):
                raise SerpApiQuotaExceededError("SerpAPI quota was exceeded.")
            raise SerpApiRateLimitError("SerpAPI rate limit exceeded.")

        if 400 <= status_code < 500:
            raise SerpApiRequestError("SerpAPI request returned an unsuccessful status.")

        if status_code >= 500:
            raise SerpApiProviderError("SerpAPI provider request failed.")

        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise SerpApiResponseError("SerpAPI response was not valid JSON.") from None

        if not isinstance(payload, dict):
            raise SerpApiResponseError("SerpAPI response JSON must be an object.")

        self._validate_search_status(payload)
        total_results = self._safe_total_results(payload)
        organic_results = payload.get("organic_results")

        if organic_results is None:
            if self._is_explicit_no_results(payload, total_results):
                return SerpApiSearchResponse(
                    query=search_query, results=[], total_results=total_results
                )
            raise SerpApiResponseError("SerpAPI response did not include organic results.")

        if not isinstance(organic_results, list):
            raise SerpApiResponseError("SerpAPI organic results must be a list.")

        results: list[SerpApiCompanyResult] = []
        for raw_result in organic_results:
            result = self._parse_company_result(raw_result)
            if result is not None:
                results.append(result)
            if len(results) == limit:
                break

        return SerpApiSearchResponse(
            query=search_query, results=results, total_results=total_results
        )

    def _read_bounded_body(self, response: httpx.Response) -> bytes:
        content_length = response.headers.get("content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length, 10)
            except ValueError:
                declared_length = -1
            if declared_length > self._max_response_bytes:
                raise SerpApiResponseTooLargeError("SerpAPI response exceeded the allowed size.")
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > self._max_response_bytes:
                raise SerpApiResponseTooLargeError("SerpAPI response exceeded the allowed size.")
            chunks.append(chunk)
        return b"".join(chunks)

    def _try_json_object(self, body: bytes) -> dict[str, Any] | None:
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _is_quota_error(self, payload: dict[str, Any]) -> bool:
        error = payload.get("error")
        return isinstance(error, str) and " ".join(error.split()).casefold() in _QUOTA_MESSAGES

    def _validate_search_status(self, payload: dict[str, Any]) -> None:
        metadata = payload.get("search_metadata")
        if not isinstance(metadata, dict):
            return
        raw_status = metadata.get("status")
        if raw_status is None:
            return
        if not isinstance(raw_status, str):
            raise SerpApiResponseError("SerpAPI search status was invalid.")
        status = raw_status.strip().casefold()
        if not status:
            return
        if status == "success":
            return
        if status in {"error", "processing", "queued"}:
            raise SerpApiProviderError("SerpAPI search did not complete successfully.")
        raise SerpApiResponseError("SerpAPI search status was invalid.")

    def _safe_total_results(self, payload: dict[str, Any]) -> int | None:
        information = payload.get("search_information")
        if not isinstance(information, dict):
            return None
        value = information.get("total_results")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        return value

    def _is_explicit_no_results(self, payload: dict[str, Any], total_results: int | None) -> bool:
        metadata = payload.get("search_metadata")
        if not isinstance(metadata, dict):
            return False
        status = metadata.get("status")
        if not isinstance(status, str) or status.strip().casefold() != "success":
            return False
        if total_results == 0:
            return True
        information = payload.get("search_information")
        if isinstance(information, dict):
            state = information.get("organic_results_state")
            if (
                isinstance(state, str)
                and " ".join(state.split()).casefold() in _EMPTY_ORGANIC_STATES
            ):
                return True
        error = payload.get("error")
        return isinstance(error, str) and " ".join(error.split()).casefold() in _NO_RESULTS_MESSAGES

    def _build_search_query(
        self,
        *,
        query: str | None,
        country: str | None,
        city: str | None,
        industry: str | None,
    ) -> str:
        parts = [
            part.strip()
            for part in [query, industry, city, country]
            if part is not None and part.strip()
        ]

        if not parts:
            raise SerpApiRequestError("At least one SerpAPI search query part is required.")

        return " ".join(parts)

    def _normalize_iso_country_code(self, iso_country_code: str | None) -> str | None:
        if iso_country_code is None:
            return None

        if isinstance(iso_country_code, bool) or not isinstance(iso_country_code, str):
            raise SerpApiRequestError("SerpAPI country code was invalid.") from None

        normalized = iso_country_code.strip()
        if not normalized:
            raise SerpApiRequestError("SerpAPI country code was invalid.") from None

        try:
            return get_country_target(normalized).serpapi_gl
        except ValueError:
            raise SerpApiRequestError("SerpAPI country code was invalid.") from None

    def _parse_company_result(self, raw_result: object) -> SerpApiCompanyResult | None:
        if not isinstance(raw_result, dict):
            return None

        title = self._optional_string(raw_result.get("title"), MAX_TITLE_LENGTH)

        if title is None:
            return None

        return SerpApiCompanyResult(
            position=self._optional_int(raw_result.get("position")),
            title=title,
            link=self._optional_string(raw_result.get("link"), MAX_LINK_LENGTH),
            snippet=self._optional_string(raw_result.get("snippet"), MAX_SNIPPET_LENGTH),
            source=self._optional_string(raw_result.get("source"), MAX_SOURCE_LENGTH),
        )

    def _optional_string(self, value: Any, maximum_length: int) -> str | None:
        if not isinstance(value, str):
            return None

        stripped = value.strip()
        return stripped if stripped and len(stripped) <= maximum_length else None

    def _optional_int(self, value: Any) -> int | None:
        if isinstance(value, bool):
            return None

        if isinstance(value, int) and value >= 1:
            return value

        return None
