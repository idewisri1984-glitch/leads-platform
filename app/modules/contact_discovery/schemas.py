from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.contact_discovery.models import (
    ContactDiscoveryCandidateStatus,
    ContactDiscoverySourceType,
    ContactDiscoveryStatus,
)


class ContactDiscoveryStateCreate(BaseModel):
    company_id: int = Field(gt=0)
    provider: str | None = Field(default=None, max_length=100)
    discovery_status: ContactDiscoveryStatus = ContactDiscoveryStatus.PENDING
    checked_at: datetime | None = None
    last_error: str | None = None

    @field_validator("last_error")
    @classmethod
    def reject_raw_markup(cls, value: str | None) -> str | None:
        return _safe_text(value)


class ContactDiscoveryStateUpdate(BaseModel):
    provider: str | None = Field(default=None, max_length=100)
    discovery_status: ContactDiscoveryStatus | None = None
    checked_at: datetime | None = None
    last_error: str | None = None

    @field_validator("last_error")
    @classmethod
    def reject_raw_markup(cls, value: str | None) -> str | None:
        return _safe_text(value)


class ContactDiscoveryCandidateCreate(BaseModel):
    company_id: int = Field(gt=0)
    name: str | None = Field(default=None, max_length=255)
    title: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=100)
    source_url: str | None = Field(default=None, max_length=500)
    source_type: ContactDiscoverySourceType
    confidence: int = Field(default=0, ge=0, le=100)
    discovery_status: ContactDiscoveryCandidateStatus = ContactDiscoveryCandidateStatus.DISCOVERED
    notes: str | None = None
    last_error: str | None = None

    @field_validator("notes", "last_error")
    @classmethod
    def reject_raw_markup(cls, value: str | None) -> str | None:
        return _safe_text(value)

    @model_validator(mode="after")
    def require_meaningful_identity(self) -> "ContactDiscoveryCandidateCreate":
        if not any(
            value is not None and value.strip() for value in (self.email, self.name, self.title)
        ):
            raise ValueError("Candidate requires an email, name, or title.")
        return self


class ContactDiscoveryCandidateUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    title: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=100)
    source_url: str | None = Field(default=None, max_length=500)
    source_type: ContactDiscoverySourceType | None = None
    confidence: int | None = Field(default=None, ge=0, le=100)
    discovery_status: ContactDiscoveryCandidateStatus | None = None
    notes: str | None = None
    last_error: str | None = None

    @field_validator("notes", "last_error")
    @classmethod
    def reject_raw_markup(cls, value: str | None) -> str | None:
        return _safe_text(value)


class ContactDiscoveryCandidateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    name: str | None
    title: str | None
    email: str | None
    normalized_email: str | None
    phone: str | None
    source_url: str | None
    source_type: ContactDiscoverySourceType
    confidence: int
    discovery_status: ContactDiscoveryCandidateStatus
    deduplication_key: str
    notes: str | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class ContactDiscoveryCandidateUpsertResult(BaseModel):
    candidate: ContactDiscoveryCandidateRead
    created: bool = False
    updated: bool = False
    protected: bool = False


def _safe_text(value: str | None) -> str | None:
    if value is not None and ("<" in value or ">" in value):
        raise ValueError("Raw markup is not allowed in contact discovery text.")
    return value
