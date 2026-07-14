from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EnrichmentStatus = Literal["PENDING", "SUCCEEDED", "PARTIAL", "NOT_FOUND", "FAILED"]


class CompanyEnrichmentTarget(BaseModel):
    company_id: int
    company_name: str
    website: str | None = None
    country: str | None = None
    city: str | None = None


class CompanyEnrichmentProviderResult(BaseModel):
    provider: str
    website: str | None = None
    email: str | None = None
    phone: str | None = None
    instagram_url: str | None = None
    linkedin_url: str | None = None
    contact_page_url: str | None = None
    about_page_url: str | None = None
    source_url: str | None = None
    notes: str | None = None
    errors: list[str] = Field(default_factory=list)

    @field_validator("provider")
    @classmethod
    def require_provider(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Provider name is required.")
        return value.strip()


class CompanyEnrichmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    company_id: int
    enrichment_status: EnrichmentStatus
    website_checked_at: datetime | None
    email: str | None
    phone: str | None
    instagram_url: str | None
    linkedin_url: str | None
    contact_page_url: str | None
    about_page_url: str | None
    source_url: str | None
    notes: str | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class CompanyEnrichmentRunItem(BaseModel):
    company_id: int
    provider: str
    status: EnrichmentStatus
    created: bool = False
    updated: bool = False
    unchanged: bool = False
    changed_fields: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class CompanyEnrichmentRunResult(BaseModel):
    project_id: int
    provider: str
    selected: int = Field(ge=0)
    attempted: int = Field(ge=0)
    created: int = Field(ge=0)
    updated: int = Field(ge=0)
    unchanged: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    partial: int = Field(ge=0)
    not_found: int = Field(ge=0)
    failed: int = Field(ge=0)
    dry_run: bool
    items: list[CompanyEnrichmentRunItem]

    @model_validator(mode="after")
    def validate_counts(self) -> "CompanyEnrichmentRunResult":
        if self.selected != len(self.items) or self.attempted != len(self.items):
            raise ValueError("Selected and attempted counts must match items.")
        if self.created + self.updated + self.unchanged != self.attempted:
            raise ValueError("Change counts must match attempted count.")
        if self.succeeded + self.partial + self.not_found + self.failed != self.attempted:
            raise ValueError("Status counts must match attempted count.")
        return self
