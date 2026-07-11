from typing import Self

from pydantic import BaseModel, Field, field_validator, model_validator

from app.modules.company_import.schemas import CompanyIngestionError, CompanyIngestionItem


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
