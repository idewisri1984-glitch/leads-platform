from typing import Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from app.modules.company_import.schemas import (
    CompanyIngestionError,
    CompanyIngestionItem,
    CompanyIngestionResult,
)
from app.modules.search_profile.schemas import SearchQuery

ProviderErrorCode = Literal[
    "configuration_error",
    "rate_limit_error",
    "request_error",
    "response_error",
    "provider_error",
]

StopReason = Literal[
    "configuration_error",
    "rate_limit_error",
    "provider_error",
]


class DiscoveryProviderResult(BaseModel):
    """
    Provider-independent result returned by a discovery provider.
    """

    title: str
    link: str | None = None
    snippet: str | None = None
    source: str | None = None
    position: int | None = Field(default=None, ge=1)
    provider_reference: str | None = None

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, value: object) -> object:
        if not isinstance(value, str):
            return value

        normalized = value.strip()

        if not normalized:
            raise ValueError("Discovery provider result title is required.")

        return normalized

    @field_validator("link", "snippet", "source", "provider_reference", mode="before")
    @classmethod
    def normalize_optional_strings(cls, value: object) -> object:
        if not isinstance(value, str):
            return value

        normalized = value.strip()
        return normalized or None


class DiscoveryProviderResponse(BaseModel):
    """
    Provider-independent response for one discovery query.
    """

    provider: str
    query: str
    results: list[DiscoveryProviderResult] = Field(default_factory=list)
    total_results: int | None = Field(default=None, ge=0)

    @field_validator("provider", "query", mode="before")
    @classmethod
    def normalize_required_strings(cls, value: object) -> object:
        if not isinstance(value, str):
            return value

        normalized = value.strip()

        if not normalized:
            raise ValueError("Discovery provider and query are required.")

        return normalized


class SearchProfileDiscoveryProviderError(BaseModel):
    """
    Safe controlled provider failure for one generated search query.
    """

    code: ProviderErrorCode
    message: str

    @field_validator("message", mode="before")
    @classmethod
    def normalize_message(cls, value: object) -> object:
        if not isinstance(value, str):
            return value

        normalized = value.strip()

        if not normalized:
            raise ValueError("Provider error message is required.")

        return normalized


class SearchProfileDiscoveryAdapterError(BaseModel):
    """
    Safe adaptation failure for one provider result.
    """

    position: int | None = Field(default=None, ge=1)
    message: str

    @field_validator("message", mode="before")
    @classmethod
    def normalize_message(cls, value: object) -> object:
        if not isinstance(value, str):
            return value

        normalized = value.strip()

        if not normalized:
            raise ValueError("Adapter error message is required.")

        return normalized


class SearchProfileDiscoveryQueryResult(BaseModel):
    """
    Dry-run outcome for one generated search query.
    """

    query: SearchQuery
    provider: str
    provider_result_count: int = Field(ge=0)
    adapted_item_count: int = Field(ge=0)
    adapter_error_count: int = Field(ge=0)
    provider_error: SearchProfileDiscoveryProviderError | None = None
    items: list[CompanyIngestionItem] = Field(default_factory=list)
    adapter_errors: list[SearchProfileDiscoveryAdapterError] = Field(default_factory=list)

    @field_validator("provider", mode="before")
    @classmethod
    def normalize_provider(cls, value: object) -> object:
        if not isinstance(value, str):
            return value

        normalized = value.strip()

        if not normalized:
            raise ValueError("Discovery provider name is required.")

        return normalized

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if self.adapted_item_count != len(self.items):
            raise ValueError("adapted_item_count must equal len(items).")

        if self.adapter_error_count != len(self.adapter_errors):
            raise ValueError("adapter_error_count must equal len(adapter_errors).")

        return self


class SearchProfileDiscoveryDryRunResult(BaseModel):
    """
    Provider-independent dry-run report for one search profile.
    """

    profile_id: int
    profile_name: str
    provider: str
    query_count: int = Field(ge=0)
    estimated_provider_requests: int = Field(ge=0)
    executed_queries: int = Field(ge=0)
    total_provider_results: int = Field(ge=0)
    total_adapted_items: int = Field(ge=0)
    total_adapter_errors: int = Field(ge=0)
    total_provider_errors: int = Field(ge=0)
    total_result_ceiling: int = Field(ge=0)
    stopped_early: bool
    stop_reason: StopReason | None = None
    query_results: list[SearchProfileDiscoveryQueryResult] = Field(default_factory=list)

    @field_validator("profile_name", "provider", mode="before")
    @classmethod
    def normalize_required_text(cls, value: object) -> object:
        if not isinstance(value, str):
            return value

        normalized = value.strip()

        if not normalized:
            raise ValueError("Profile and provider names are required.")

        return normalized

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if self.executed_queries != len(self.query_results):
            raise ValueError("executed_queries must equal len(query_results).")

        if self.total_provider_results != sum(
            result.provider_result_count for result in self.query_results
        ):
            raise ValueError("total_provider_results is inconsistent with query_results.")

        if self.total_adapted_items != sum(
            result.adapted_item_count for result in self.query_results
        ):
            raise ValueError("total_adapted_items is inconsistent with query_results.")

        if self.total_adapter_errors != sum(
            result.adapter_error_count for result in self.query_results
        ):
            raise ValueError("total_adapter_errors is inconsistent with query_results.")

        provider_error_count = sum(
            result.provider_error is not None for result in self.query_results
        )

        if self.total_provider_errors != provider_error_count:
            raise ValueError("total_provider_errors is inconsistent with query_results.")

        if self.query_count < self.executed_queries:
            raise ValueError("query_count must be greater than or equal to executed_queries.")

        if self.estimated_provider_requests != self.query_count:
            raise ValueError("estimated_provider_requests must equal query_count.")

        if self.stopped_early and self.stop_reason is None:
            raise ValueError("stop_reason is required when stopped_early is true.")

        if not self.stopped_early and self.stop_reason is not None:
            raise ValueError("stop_reason must be absent when stopped_early is false.")

        return self


class SearchProfileDiscoveryPersistResult(SearchProfileDiscoveryDryRunResult):
    """
    Search profile discovery outcome with optional ingestion results.
    """

    ingestion_attempted: bool
    total_items_submitted_to_ingestion: int = Field(ge=0)
    ingestion_result: CompanyIngestionResult | None = None

    @model_validator(mode="after")
    def validate_ingestion(self) -> Self:
        if self.ingestion_attempted:
            if self.ingestion_result is None:
                raise ValueError("ingestion_result is required when ingestion was attempted.")

            if self.total_items_submitted_to_ingestion != self.ingestion_result.total_rows:
                raise ValueError(
                    "total_items_submitted_to_ingestion must equal ingestion_result.total_rows."
                )
        elif self.ingestion_result is not None:
            raise ValueError("ingestion_result must be absent when ingestion was not attempted.")
        elif self.total_items_submitted_to_ingestion != 0:
            raise ValueError(
                "total_items_submitted_to_ingestion must be zero when ingestion was not attempted."
            )

        if self.total_items_submitted_to_ingestion > self.total_adapted_items:
            raise ValueError(
                "total_items_submitted_to_ingestion cannot exceed total_adapted_items."
            )

        return self


class CompanyDiscoveryRequest(BaseModel):
    """
    Source discovery input accepted by provider-backed company discovery services.
    """

    query: str | None = None
    country: str | None = None
    city: str | None = None
    industry: str | None = None
    limit: int = Field(default=10, gt=0, le=100)

    @field_validator("query", "country", "city", "industry", mode="before")
    @classmethod
    def normalize_blank_strings(cls, value: object) -> object:
        if not isinstance(value, str):
            return value

        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def require_search_input(self) -> Self:
        if all(value is None for value in [self.query, self.country, self.city, self.industry]):
            raise ValueError("At least one discovery search field is required.")

        return self


class CompanyDiscoveryResult(BaseModel):
    """
    Dry-run discovery output.

    The total_results invariant is total_results == len(items) + len(errors).
    """

    provider: str = "serpapi"
    query: str
    total_results: int
    items: list[CompanyIngestionItem]
    errors: list[CompanyIngestionError]

    @model_validator(mode="after")
    def validate_total_results(self) -> Self:
        if self.total_results != len(self.items) + len(self.errors):
            raise ValueError("total_results must equal len(items) + len(errors).")

        return self


class CompanyDiscoveryPersistenceResult(BaseModel):
    """
    Discovery persistence output after valid discovered items are passed to ingestion.

    The discovered invariant is discovered == imported + skipped_duplicates + failed.
    """

    provider: str = "serpapi"
    query: str
    discovered: int
    imported: int
    skipped_duplicates: int
    failed: int
    created_company_ids: list[int]
    errors: list[CompanyIngestionError]
    rolled_back: bool

    @model_validator(mode="after")
    def validate_discovered_count(self) -> Self:
        if self.discovered != self.imported + self.skipped_duplicates + self.failed:
            raise ValueError("discovered must equal imported + skipped_duplicates + failed.")

        return self
