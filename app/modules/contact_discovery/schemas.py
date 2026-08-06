from datetime import datetime
from decimal import Decimal
from math import isfinite
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.contact_discovery.models import (
    ContactDiscoveryCandidate,
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
    confidence_percent: int
    discovery_status: ContactDiscoveryCandidateStatus
    deduplication_key: str = Field(max_length=255)
    notes: str | None = None
    last_error: str | None = None

    @classmethod
    def from_record(cls, record: object) -> "ContactDiscoveryPersistedCandidateRaw":
        fields = (
            "id",
            "company_id",
            "promoted_contact_id",
            "name",
            "title",
            "email",
            "normalized_email",
            "phone",
            "source_url",
            "source_type",
            "confidence",
            "confidence_percent",
            "discovery_status",
            "deduplication_key",
            "notes",
            "last_error",
        )
        values = {name: getattr(record, name, None) for name in fields}
        confidence = values["confidence"]
        if type(record) is ContactDiscoveryCandidate:
            if type(confidence) is not int or not 0 <= confidence <= 100:
                raise ValueError("Candidate persistence result is invalid.")
            values["confidence_percent"] = confidence
            values["confidence"] = float(confidence) / 100.0
        return cls.model_validate(values)

    def to_read_model(
        self, *, created_at: object, updated_at: object
    ) -> ContactDiscoveryCandidateRead:
        if type(created_at) is not datetime or type(updated_at) is not datetime:
            raise ValueError("Candidate persistence result is invalid.")
        return ContactDiscoveryCandidateRead(
            id=self.id,
            company_id=self.company_id,
            promoted_contact_id=self.promoted_contact_id,
            name=self.name,
            title=self.title,
            email=self.email,
            normalized_email=self.normalized_email,
            phone=self.phone,
            source_url=self.source_url,
            source_type=self.source_type,
            confidence=self.confidence_percent,
            discovery_status=self.discovery_status,
            deduplication_key=self.deduplication_key,
            notes=self.notes,
            last_error=self.last_error,
            created_at=created_at,
            updated_at=updated_at,
        )

    def matches_read_model(self, candidate: object) -> bool:
        if type(candidate) is not ContactDiscoveryCandidateRead:
            return False
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
        if (
            type(candidate.id) is not int
            or type(candidate.company_id) is not int
            or (
                candidate.promoted_contact_id is not None
                and type(candidate.promoted_contact_id) is not int
            )
            or type(candidate.confidence) is not int
            or not 0 <= candidate.confidence <= 100
            or type(candidate.source_type) is not ContactDiscoverySourceType
            or type(candidate.discovery_status) is not ContactDiscoveryCandidateStatus
            or any(
                getattr(candidate, name) is not None and type(getattr(candidate, name)) is not str
                for name in text_fields
            )
        ):
            return False
        return (
            candidate.id == self.id
            and candidate.company_id == self.company_id
            and candidate.promoted_contact_id == self.promoted_contact_id
            and candidate.name == self.name
            and candidate.title == self.title
            and candidate.email == self.email
            and candidate.normalized_email == self.normalized_email
            and candidate.phone == self.phone
            and candidate.source_url == self.source_url
            and candidate.source_type is self.source_type
            and candidate.confidence == self.confidence_percent
            and candidate.discovery_status is self.discovery_status
            and candidate.deduplication_key == self.deduplication_key
            and candidate.notes == self.notes
            and candidate.last_error == self.last_error
        )

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
        confidence_percent = field("confidence_percent")
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
        scaled_confidence = Decimal(str(confidence)) * Decimal(100)
        if scaled_confidence != scaled_confidence.to_integral_value():
            raise ValueError("Candidate persistence result is invalid.")
        derived_confidence_percent = int(scaled_confidence)
        if confidence_percent is None:
            confidence_percent = derived_confidence_percent
        if (
            type(confidence_percent) is not int
            or not 0 <= confidence_percent <= 100
            or confidence_percent != derived_confidence_percent
        ):
            raise ValueError("Candidate persistence result is invalid.")
        for name in text_fields:
            if field(name) is not None and "\x00" in field(name):
                raise ValueError("Candidate persistence result is invalid.")
            _safe_text(field(name))
        field_names = (
            "id",
            "company_id",
            "promoted_contact_id",
            "name",
            "title",
            "email",
            "normalized_email",
            "phone",
            "source_url",
            "source_type",
            "confidence",
            "discovery_status",
            "deduplication_key",
            "notes",
            "last_error",
        )
        validated = {name: field(name) for name in field_names}
        validated["confidence_percent"] = confidence_percent
        return validated


class ContactDiscoveryCandidateUpsertResult(BaseModel):
    model_config = ConfigDict(revalidate_instances="always")

    candidate: ContactDiscoveryCandidateRead
    persisted_candidate: ContactDiscoveryPersistedCandidateRaw
    created: bool = False
    updated: bool = False
    protected: bool = False

    @model_validator(mode="after")
    def require_consistent_representations(self) -> "ContactDiscoveryCandidateUpsertResult":
        if not self.persisted_candidate.matches_read_model(self.candidate):
            raise ValueError("Candidate persistence result is invalid.")
        return self


def _safe_text(value: str | None) -> str | None:
    if value is not None and ("<" in value or ">" in value):
        raise ValueError("Raw markup is not allowed in contact discovery text.")
    return value
