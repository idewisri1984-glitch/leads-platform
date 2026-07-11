from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _normalize_text(value: str) -> str:
    return value.strip()


def _normalize_string_list(value: object) -> object:
    if value is None:
        return value

    if not isinstance(value, list):
        return value

    normalized_items: list[object] = []
    seen: set[str] = set()

    for item in value:
        if not isinstance(item, str):
            normalized_items.append(item)
            continue

        normalized = item.strip()

        if not normalized:
            continue

        dedupe_key = normalized.casefold()

        if dedupe_key in seen:
            continue

        seen.add(dedupe_key)
        normalized_items.append(normalized)

    return normalized_items


class SearchProfileCreate(BaseModel):
    """
    Schema for creating a project-scoped search profile.
    """

    project_id: int
    name: str = Field(max_length=255)
    description: str | None = None
    product_or_service: str = Field(max_length=255)

    target_customer_types: list[str] = Field(default_factory=list)
    target_industries: list[str] = Field(default_factory=list)
    positive_keywords: list[str] = Field(default_factory=list)
    negative_keywords: list[str] = Field(default_factory=list)

    countries: list[str] = Field(default_factory=list)
    cities: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)

    query_templates: list[str] = Field(default_factory=list)

    result_limit: int = Field(default=10, ge=1, le=100)
    max_queries_per_run: int = Field(default=10, ge=1, le=100)
    total_result_ceiling: int = Field(default=100, ge=1, le=1000)
    enabled: bool = True

    @field_validator("name", "product_or_service", mode="before")
    @classmethod
    def normalize_required_text(cls, value: object) -> object:
        if not isinstance(value, str):
            return value

        return _normalize_text(value)

    @field_validator(
        "target_customer_types",
        "target_industries",
        "positive_keywords",
        "negative_keywords",
        "countries",
        "cities",
        "languages",
        "query_templates",
        mode="before",
    )
    @classmethod
    def normalize_list_items(cls, value: object) -> object:
        return _normalize_string_list(value)

    @model_validator(mode="after")
    def validate_profile(self) -> Self:
        if not self.name:
            raise ValueError("Search profile name is required.")

        if not self.product_or_service:
            raise ValueError("Product or service is required.")

        if not (self.target_customer_types or self.target_industries or self.positive_keywords):
            raise ValueError("At least one targeting dimension is required.")

        return self


class SearchProfileRead(BaseModel):
    """
    Schema returned to the application.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    name: str
    description: str | None
    product_or_service: str

    target_customer_types: list[str]
    target_industries: list[str]
    positive_keywords: list[str]
    negative_keywords: list[str]

    countries: list[str]
    cities: list[str]
    languages: list[str]

    query_templates: list[str]

    result_limit: int
    max_queries_per_run: int
    total_result_ceiling: int
    enabled: bool


class SearchProfileUpdate(BaseModel):
    """
    Schema for partially updating a search profile.
    """

    name: str | None = Field(default=None, max_length=255)
    description: str | None = None
    product_or_service: str | None = Field(default=None, max_length=255)

    target_customer_types: list[str] | None = None
    target_industries: list[str] | None = None
    positive_keywords: list[str] | None = None
    negative_keywords: list[str] | None = None

    countries: list[str] | None = None
    cities: list[str] | None = None
    languages: list[str] | None = None

    query_templates: list[str] | None = None

    result_limit: int | None = Field(default=None, ge=1, le=100)
    max_queries_per_run: int | None = Field(default=None, ge=1, le=100)
    total_result_ceiling: int | None = Field(default=None, ge=1, le=1000)
    enabled: bool | None = None

    @field_validator("name", "product_or_service", mode="before")
    @classmethod
    def normalize_optional_required_text(cls, value: object) -> object:
        if value is None or not isinstance(value, str):
            return value

        return _normalize_text(value)

    @field_validator(
        "target_customer_types",
        "target_industries",
        "positive_keywords",
        "negative_keywords",
        "countries",
        "cities",
        "languages",
        "query_templates",
        mode="before",
    )
    @classmethod
    def normalize_optional_list_items(cls, value: object) -> object:
        return _normalize_string_list(value)

    @model_validator(mode="after")
    def validate_supplied_text(self) -> Self:
        if self.name is not None and not self.name:
            raise ValueError("Search profile name is required.")

        if self.product_or_service is not None and not self.product_or_service:
            raise ValueError("Product or service is required.")

        return self

    def supplied_values(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True)


class SearchQuery(BaseModel):
    """
    Provider-independent generated search query.
    """

    text: str
    profile_id: int
    profile_name: str
    language: str | None = None
    country: str | None = None
    city: str | None = None
    source_template: str
    limit: int


class SearchProfileRunOptions(BaseModel):
    """
    Query generation options for a profile run preview.
    """

    max_queries: int | None = Field(default=None, ge=1, le=100)
    result_limit_per_query: int | None = Field(default=None, ge=1, le=100)
    total_result_ceiling: int | None = Field(default=None, ge=1, le=1000)


class SearchQueryPreview(BaseModel):
    """
    Preview of generated search queries and provider-agnostic request counts.
    """

    profile_id: int
    profile_name: str
    query_count: int
    estimated_provider_requests: int
    result_limit_per_query: int
    total_result_ceiling: int
    queries: list[SearchQuery]

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if self.query_count != len(self.queries):
            raise ValueError("query_count must equal len(queries).")

        if self.estimated_provider_requests != len(self.queries):
            raise ValueError("estimated_provider_requests must equal len(queries).")

        return self
