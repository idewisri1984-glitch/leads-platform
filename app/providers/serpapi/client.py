from typing import Any

import httpx

from app.providers.serpapi.exceptions import (
    SerpApiConfigurationError,
    SerpApiRateLimitError,
    SerpApiRequestError,
    SerpApiResponseError,
)
from app.providers.serpapi.schemas import SerpApiCompanyResult, SerpApiSearchResponse


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
    ) -> None:
        self._api_key = api_key.strip() if api_key is not None else None
        self._base_url = base_url
        self._timeout_seconds = timeout_seconds
        self._http_client = http_client or httpx.Client()

    def search_companies(
        self,
        *,
        query: str | None,
        country: str | None,
        city: str | None,
        industry: str | None,
        limit: int,
    ) -> SerpApiSearchResponse:
        if not self._api_key:
            raise SerpApiConfigurationError("SERPAPI_API_KEY is required to use SerpAPI.")

        if limit < 1:
            raise SerpApiRequestError("SerpAPI result limit must be at least 1.")

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
        }

        try:
            response = self._http_client.get(
                self._base_url,
                params=params,
                timeout=self._timeout_seconds,
            )
        except httpx.HTTPError as error:
            raise SerpApiRequestError("SerpAPI request failed.") from error

        if response.status_code == 429:
            raise SerpApiRateLimitError("SerpAPI rate limit exceeded.")

        if response.status_code >= 400:
            raise SerpApiRequestError("SerpAPI request returned an unsuccessful status.")

        try:
            payload = response.json()
        except ValueError as error:
            raise SerpApiResponseError("SerpAPI response was not valid JSON.") from error

        if not isinstance(payload, dict):
            raise SerpApiResponseError("SerpAPI response JSON must be an object.")

        organic_results = payload.get("organic_results")

        if organic_results is None:
            raise SerpApiResponseError("SerpAPI response did not include organic results.")

        if not isinstance(organic_results, list):
            raise SerpApiResponseError("SerpAPI organic results must be a list.")

        results = [
            result
            for raw_result in organic_results[:limit]
            if (result := self._parse_company_result(raw_result)) is not None
        ]

        return SerpApiSearchResponse(query=search_query, results=results)

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

    def _parse_company_result(self, raw_result: object) -> SerpApiCompanyResult | None:
        if not isinstance(raw_result, dict):
            return None

        title = self._optional_string(raw_result.get("title"))

        if title is None:
            return None

        return SerpApiCompanyResult(
            position=self._optional_int(raw_result.get("position")),
            title=title,
            link=self._optional_string(raw_result.get("link")),
            snippet=self._optional_string(raw_result.get("snippet")),
            source=self._optional_string(raw_result.get("source")),
        )

    def _optional_string(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None

        stripped = value.strip()
        return stripped or None

    def _optional_int(self, value: Any) -> int | None:
        if isinstance(value, bool):
            return None

        if isinstance(value, int):
            return value

        return None
