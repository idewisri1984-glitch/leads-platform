from typing import Self

from pydantic import BaseModel, Field, field_validator, model_validator

from app.modules.company_import.schemas import CompanyIngestionError, CompanyIngestionItem


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
