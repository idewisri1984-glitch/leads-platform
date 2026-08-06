from datetime import datetime
from math import isfinite
from typing import Any

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
    model_config = ConfigDict(extra="forbid")

    company_id: int = Field(gt=0)
    name: str | None = Field(default=None, max_length=255)
    title: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=100)
    source_url: str | None = Field(default=None, max_length=500)
    source_type: ContactDiscoverySourceType
    confidence: int = Field(default=0, ge=0, le=100)
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
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=255)
    title: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=100)
    source_url: str | None = Field(default=None, max_length=500)
    source_type: ContactDiscoverySourceType | None = None
    confidence: int | None = Field(default=None, ge=0, le=100)
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
    promoted_contact_id: int | None = None
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


class ContactDiscoveryPersistedCandidateRaw(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    id: int
    company_id: int
    promoted_contact_id: int | None
    name: str | None = Field(max_length=255)
    title: str | None = Field(max_length=255)
    email: str | None = Field(max_length=255)
    normalized_email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(max_length=100)
    source_url: str | None = Field(max_length=500)
    source_type: ContactDiscoverySourceType
    confidence: float
    discovery_status: ContactDiscoveryCandidateStatus
    deduplication_key: str = Field(max_length=255)
    notes: str | None = None
    last_error: str | None = None

    @model_validator(mode="before")
    @classmethod
    def validate_raw_persisted_values(cls, value: object) -> object:
        def field(name: str) -> Any:
            if isinstance(value, dict):
                return value.get(name)
            return getattr(value, name, None)

        candidate_id = field("id")
        company_id = field("company_id")
        promoted_contact_id = field("promoted_contact_id")
        confidence = field("confidence")
        text_fields = (
            "name",
            "title",
            "email",
            "normalized_email",
            "phone",
            "source_url",
            "deduplication_key",
            "notes",
            "last_error",
        )
        source_type = field("source_type")
        discovery_status = field("discovery_status")
        if (
            type(candidate_id) is not int
            or candidate_id <= 0
            or type(company_id) is not int
            or company_id <= 0
            or (
                promoted_contact_id is not None
                and (type(promoted_contact_id) is not int or promoted_contact_id <= 0)
            )
            or type(confidence) is not float
            or not isfinite(confidence)
            or not 0.0 <= confidence <= 1.0
            or any(field(name) is not None and type(field(name)) is not str for name in text_fields)
            or type(field("deduplication_key")) is not str
            or not field("deduplication_key").strip()
            or not (
                isinstance(source_type, ContactDiscoverySourceType)
                or (
                    type(source_type) is str
                    and source_type in ContactDiscoverySourceType._value2member_map_
                )
            )
            or not (
                isinstance(discovery_status, ContactDiscoveryCandidateStatus)
                or (
                    type(discovery_status) is str
                    and discovery_status in ContactDiscoveryCandidateStatus._value2member_map_
                )
            )
        ):
            raise ValueError("Candidate persistence result is invalid.")
        for name in text_fields:
            if field(name) is not None and "\x00" in field(name):
                raise ValueError("Candidate persistence result is invalid.")
            _safe_text(field(name))
        return value


class ContactDiscoveryCandidateUpsertResult(BaseModel):
    candidate: ContactDiscoveryCandidateRead
    persisted_candidate: ContactDiscoveryPersistedCandidateRaw
    created: bool = False
    updated: bool = False
    protected: bool = False


def _safe_text(value: str | None) -> str | None:
    if value is not None and ("<" in value or ">" in value):
        raise ValueError("Raw markup is not allowed in contact discovery text.")
    return value
