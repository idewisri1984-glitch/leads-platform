from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, field_serializer, field_validator, model_validator

from .models import EmailDeliveryOutcome

_STRICT = ConfigDict(frozen=True, extra="forbid", strict=True)
_DOT_ATOM_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!#$%&'*+-/=?^_`{|}~."
)
_HEX = frozenset("0123456789abcdef")
_SECURITY_MODES = frozenset({"STARTTLS", "TLS_IMPLICIT", "PLAINTEXT_LOCAL_TEST_ONLY"})


class EmailDeliverySMTPClassification(StrEnum):
    TRANSIENT = "TRANSIENT"
    PERMANENT = "PERMANENT"
    UNKNOWN = "UNKNOWN"


def _bounded_header(value: object, maximum: int, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if type(value) is not str or value != value.strip() or not 1 <= len(value) <= maximum:
        raise ValueError("Email delivery header value is invalid.")
    if "\r" in value or "\n" in value or "\x00" in value:
        raise ValueError("Email delivery header value is invalid.")
    if any(ord(character) < 32 for character in value):
        raise ValueError("Email delivery header value is invalid.")
    return value


def _is_dot_atom(value: str, *, maximum: int) -> bool:
    return (
        1 <= len(value) <= maximum
        and value.isascii()
        and not value.startswith(".")
        and not value.endswith(".")
        and ".." not in value
        and all(character in _DOT_ATOM_CHARACTERS for character in value)
    )


def _is_domain(value: str, *, require_dot: bool) -> bool:
    if not 1 <= len(value) <= 253 or not value.isascii():
        return False
    labels = value.split(".")
    if require_dot and len(labels) < 2:
        return False
    return all(
        1 <= len(label) <= 63
        and label[0].isalnum()
        and label[-1].isalnum()
        and all(character.isalnum() or character == "-" for character in label)
        for label in labels
    )


def _smtp_address(value: object) -> str:
    header = _bounded_header(value, 254)
    assert header is not None
    if header.count("@") != 1:
        raise ValueError("Email delivery address is invalid.")
    local_part, domain = header.split("@", 1)
    if not _is_dot_atom(local_part, maximum=64) or not _is_domain(domain, require_dot=True):
        raise ValueError("Email delivery address is invalid.")
    return header.casefold()


def _message_id(value: object) -> str:
    message_id = _bounded_header(value, 255)
    assert message_id is not None
    if (
        not message_id.startswith("<")
        or not message_id.endswith(">")
        or message_id.count("@") != 1
        or not message_id.isascii()
    ):
        raise ValueError("Email delivery Message-ID is invalid.")
    local_part, domain = message_id[1:-1].split("@", 1)
    if not _is_dot_atom(local_part, maximum=64) or not _is_domain(domain, require_dot=False):
        raise ValueError("Email delivery Message-ID is invalid.")
    return message_id


def _sha256(value: object) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or value != value.lower()
        or any(character not in _HEX for character in value)
    ):
        raise ValueError("Email delivery SHA-256 value is invalid.")
    return value


def _positive_id(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError("Email delivery identifier is invalid.")
    return value


def _aware_utc(value: object) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Email delivery timestamp is invalid.")
    return value.astimezone(UTC)


def _optional_error(value: object) -> str | None:
    return _bounded_header(value, 100, optional=True)


class EmailDeliveryAttemptCreate(BaseModel):
    model_config = _STRICT

    email_draft_id: int
    attempt_key: str
    outcome: EmailDeliveryOutcome
    recipient_email: str
    envelope_from: str
    header_from_email: str
    header_from_name: str | None
    reply_to: str | None
    message_id: str
    content_hash: str
    transport_name: str
    security_mode: str
    created_at: datetime

    @field_validator("email_draft_id", mode="before")
    @classmethod
    def validate_id(cls, value: object) -> int:
        return _positive_id(value)

    @field_validator("attempt_key", "content_hash", mode="before")
    @classmethod
    def validate_hashes(cls, value: object) -> str:
        return _sha256(value)

    @field_validator("recipient_email", "envelope_from", "header_from_email", mode="before")
    @classmethod
    def validate_addresses(cls, value: object) -> str:
        return _smtp_address(value)

    @field_validator("reply_to", mode="before")
    @classmethod
    def validate_reply_to(cls, value: object) -> str | None:
        return None if value is None else _smtp_address(value)

    @field_validator("header_from_name", mode="before")
    @classmethod
    def validate_header_name(cls, value: object) -> str | None:
        return _bounded_header(value, 100, optional=True)

    @field_validator("message_id", mode="before")
    @classmethod
    def validate_message_id(cls, value: object) -> str:
        return _message_id(value)

    @field_validator("transport_name", mode="before")
    @classmethod
    def validate_transport_name(cls, value: object) -> str:
        transport = _bounded_header(value, 100)
        assert transport is not None
        return transport

    @field_validator("security_mode", mode="before")
    @classmethod
    def validate_security_mode(cls, value: object) -> str:
        if type(value) is not str or value not in _SECURITY_MODES:
            raise ValueError("Email delivery security mode is invalid.")
        return value

    @field_validator("outcome", mode="before")
    @classmethod
    def validate_outcome(cls, value: object) -> EmailDeliveryOutcome:
        if type(value) is not EmailDeliveryOutcome or value is not EmailDeliveryOutcome.RESERVED:
            raise ValueError("Email delivery reservation outcome is invalid.")
        return value

    @field_validator("created_at", mode="before")
    @classmethod
    def validate_created_at(cls, value: object) -> datetime:
        return _aware_utc(value)


class EmailDeliveryAttemptOutcomeUpdate(BaseModel):
    model_config = _STRICT

    outcome: EmailDeliveryOutcome
    smtp_classification: EmailDeliverySMTPClassification | None
    smtp_code: int | None
    error_category: str | None
    completed_at: datetime
    accepted_at: datetime | None
    unknown_at: datetime | None

    @field_validator("outcome", mode="before")
    @classmethod
    def validate_outcome(cls, value: object) -> EmailDeliveryOutcome:
        if type(value) is not EmailDeliveryOutcome or value is EmailDeliveryOutcome.RESERVED:
            raise ValueError("Email delivery terminal outcome is invalid.")
        return value

    @field_validator("smtp_classification", mode="before")
    @classmethod
    def validate_classification(cls, value: object) -> EmailDeliverySMTPClassification | None:
        if value is None:
            return None
        if type(value) is not EmailDeliverySMTPClassification:
            raise ValueError("Email delivery SMTP classification is invalid.")
        return value

    @field_validator("smtp_code", mode="before")
    @classmethod
    def validate_smtp_code(cls, value: object) -> int | None:
        if value is None:
            return None
        if type(value) is not int or not 200 <= value <= 599:
            raise ValueError("Email delivery SMTP code is invalid.")
        return value

    @field_validator("error_category", mode="before")
    @classmethod
    def validate_error_category(cls, value: object) -> str | None:
        return _optional_error(value)

    @field_validator("completed_at", mode="before")
    @classmethod
    def validate_completed_at(cls, value: object) -> datetime:
        return _aware_utc(value)

    @field_validator("accepted_at", "unknown_at", mode="before")
    @classmethod
    def validate_optional_timestamps(cls, value: object) -> datetime | None:
        return None if value is None else _aware_utc(value)

    @model_validator(mode="after")
    def validate_outcome_fields(self) -> "EmailDeliveryAttemptOutcomeUpdate":
        if self.outcome is EmailDeliveryOutcome.ACCEPTED:
            valid = (
                self.accepted_at is not None
                and self.unknown_at is None
                and self.smtp_classification is None
                and self.error_category is None
            )
        elif self.outcome is EmailDeliveryOutcome.TRANSIENT_FAILURE:
            valid = (
                self.accepted_at is None
                and self.unknown_at is None
                and self.smtp_classification is EmailDeliverySMTPClassification.TRANSIENT
            )
        elif self.outcome is EmailDeliveryOutcome.PERMANENT_FAILURE:
            valid = (
                self.accepted_at is None
                and self.unknown_at is None
                and self.smtp_classification is EmailDeliverySMTPClassification.PERMANENT
            )
        else:
            valid = (
                self.accepted_at is None
                and self.unknown_at is not None
                and self.smtp_classification is EmailDeliverySMTPClassification.UNKNOWN
            )
        if not valid:
            raise ValueError("Email delivery outcome fields are invalid.")
        return self


class EmailDeliveryAttemptRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True, extra="forbid", strict=True)

    id: int
    email_draft_id: int
    attempt_key: str
    outcome: str
    recipient_email: str
    envelope_from: str
    header_from_email: str
    header_from_name: str | None
    reply_to: str | None
    message_id: str
    content_hash: str
    transport_name: str
    security_mode: str
    smtp_classification: str | None
    smtp_code: int | None
    error_category: str | None
    created_at: datetime
    completed_at: datetime | None
    accepted_at: datetime | None
    unknown_at: datetime | None
    updated_at: datetime

    @field_serializer(
        "created_at",
        "completed_at",
        "accepted_at",
        "unknown_at",
        "updated_at",
        when_used="json",
    )
    def serialize_datetime(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        utc_value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return utc_value.isoformat().replace("+00:00", "Z")
