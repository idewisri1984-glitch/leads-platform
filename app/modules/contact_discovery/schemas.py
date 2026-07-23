from contextlib import suppress
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.contact.channel_normalization import (
    normalize_instagram_url,
    normalize_linkedin_url,
)
from app.modules.contact_discovery.models import (
    ContactDiscoveryCandidateStatus,
    ContactDiscoverySourceType,
    ContactDiscoveryStatus,
)
from app.modules.contact_discovery.normalization import (
    normalize_discovered_email,
    normalize_discovered_phone,
    normalize_source_for_deduplication,
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
    linkedin_url: str | None = Field(default=None, max_length=500)
    instagram_url: str | None = Field(default=None, max_length=500)
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

    @field_validator("linkedin_url")
    @classmethod
    def normalize_linkedin(cls, value: str | None) -> str | None:
        return normalize_linkedin_url(value)

    @field_validator("instagram_url")
    @classmethod
    def normalize_instagram(cls, value: str | None) -> str | None:
        return normalize_instagram_url(value)

    @model_validator(mode="after")
    def require_meaningful_identity(self) -> "ContactDiscoveryCandidateCreate":
        usable_email = False
        with suppress(ValueError):
            usable_email = normalize_discovered_email(self.email) is not None
        usable_phone = normalize_discovered_phone(self.phone) is not None
        existing_person_identity = False
        with suppress(ValueError):
            existing_person_identity = bool(
                any(value is not None and value.strip() for value in (self.name, self.title))
                and normalize_source_for_deduplication(self.source_url)
            )
        if not any(
            (
                usable_email,
                usable_phone,
                self.linkedin_url,
                self.instagram_url,
                existing_person_identity,
            )
        ):
            raise ValueError("Candidate requires a usable channel or public person identity.")
        return self


class ContactDiscoveryCandidateUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    title: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=100)
    linkedin_url: str | None = Field(default=None, max_length=500)
    instagram_url: str | None = Field(default=None, max_length=500)
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

    @field_validator("linkedin_url")
    @classmethod
    def normalize_linkedin(cls, value: str | None) -> str | None:
        return normalize_linkedin_url(value)

    @field_validator("instagram_url")
    @classmethod
    def normalize_instagram(cls, value: str | None) -> str | None:
        return normalize_instagram_url(value)


class ContactDiscoveryCandidateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    name: str | None
    title: str | None
    email: str | None
    normalized_email: str | None
    phone: str | None
    linkedin_url: str | None
    instagram_url: str | None
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
