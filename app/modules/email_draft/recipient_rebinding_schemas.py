from __future__ import annotations

import re
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, field_validator

from app.modules.contact_discovery.normalization import normalize_discovered_email

_STRICT = ConfigDict(frozen=True, extra="forbid", strict=True)
_HASH = re.compile(r"[0-9a-f]{64}")
_MARKUP = re.compile(r"<[^>]*>")


def _positive_id(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError("Identifier is invalid.")
    return value


def _normalized_text(value: object, maximum: int) -> str:
    if type(value) is not str or not value or len(value) > maximum:
        raise ValueError("Text value is invalid.")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("Text value is invalid.")
    if _MARKUP.search(value) is not None or "<" in value or ">" in value:
        raise ValueError("Text value is invalid.")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > maximum:
        raise ValueError("Text value is invalid.")
    return normalized


def _source_url(value: object) -> str:
    if type(value) is not str or not value or len(value) > 500:
        raise ValueError("Source URL is invalid.")
    if value != value.strip() or any(character.isspace() for character in value):
        raise ValueError("Source URL is invalid.")
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        raise ValueError("Source URL is invalid.") from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("Source URL is invalid.")
    return value


class PersonRecipientRebindingInput(BaseModel):
    model_config = _STRICT

    project_id: int
    company_id: int
    lead_id: int
    task_id: int
    email_draft_id: int
    recipient_email: str
    expected_content_hash: str
    first_name: str
    last_name: str
    job_title: str
    country: str
    city: str
    person_source_url: str
    location_source_url: str
    confirmed: bool

    @field_validator(
        "project_id", "company_id", "lead_id", "task_id", "email_draft_id", mode="before"
    )
    @classmethod
    def validate_ids(cls, value: object) -> int:
        return _positive_id(value)

    @field_validator("recipient_email", mode="before")
    @classmethod
    def validate_email(cls, value: object) -> str:
        if type(value) is not str:
            raise ValueError("Recipient email is invalid.")
        try:
            normalized = normalize_discovered_email(value)
        except (TypeError, ValueError):
            raise ValueError("Recipient email is invalid.") from None
        if normalized is None:
            raise ValueError("Recipient email is invalid.")
        return normalized

    @field_validator("expected_content_hash", mode="before")
    @classmethod
    def validate_hash(cls, value: object) -> str:
        if type(value) is not str or _HASH.fullmatch(value) is None:
            raise ValueError("Content hash is invalid.")
        return value

    @field_validator("first_name", "last_name", mode="before")
    @classmethod
    def validate_names(cls, value: object) -> str:
        return _normalized_text(value, 100)

    @field_validator("job_title", mode="before")
    @classmethod
    def validate_job_title(cls, value: object) -> str:
        return _normalized_text(value, 150)

    @field_validator("country", "city", mode="before")
    @classmethod
    def validate_location(cls, value: object) -> str:
        return _normalized_text(value, 100)

    @field_validator("person_source_url", "location_source_url", mode="before")
    @classmethod
    def validate_source_urls(cls, value: object) -> str:
        return _source_url(value)

    @field_validator("confirmed", mode="before")
    @classmethod
    def validate_confirmation(cls, value: object) -> bool:
        if type(value) is not bool or value is not True:
            raise ValueError("Confirmation is required.")
        return value


class PersonRecipientRebindingResult(BaseModel):
    model_config = _STRICT

    project_id: int
    company_id: int
    contact_id: int
    lead_id: int
    task_id: int
    email_draft_id: int
    contact_created: bool
    contact_reused: bool
    country_before: str | None
    country_after: str
    city_before: str | None
    city_after: str
    lead_contact_id_before: int | None
    lead_contact_id_after: int
    draft_contact_id_before: int | None
    draft_contact_id_after: int
    recipient_email: str
    recipient_name_before: str
    recipient_name_after: str
    recipient_role_before: str | None
    recipient_role_after: str
    context_fingerprint_before: str
    context_fingerprint_after: str
    request_fingerprint_before: str
    request_fingerprint_after: str
    content_hash_before: str
    content_hash_after: str
    person_source_url: str
    location_source_url: str
    changed: bool
    network_call_count: int
    smtp_call_count: int
