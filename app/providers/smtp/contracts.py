from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, SecretStr, field_validator, model_validator

from app.modules.contact_discovery.normalization import normalize_discovered_email

_STRICT = ConfigDict(frozen=True, extra="forbid", strict=True)
_LOCAL_TEST_HOSTS = frozenset({"127.0.0.1", "localhost"})


class SMTPSecurityMode(StrEnum):
    STARTTLS = "STARTTLS"
    TLS_IMPLICIT = "TLS_IMPLICIT"
    PLAINTEXT_LOCAL_TEST_ONLY = "PLAINTEXT_LOCAL_TEST_ONLY"


def _bounded_header(value: object, maximum: int, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if type(value) is not str or value != value.strip() or not 1 <= len(value) <= maximum:
        raise ValueError("SMTP header value is invalid.")
    if "\r" in value or "\n" in value or "\x00" in value:
        raise ValueError("SMTP header value is invalid.")
    if any(ord(character) < 32 for character in value):
        raise ValueError("SMTP header value is invalid.")
    return value


def _smtp_address(value: object) -> str:
    header = _bounded_header(value, 254)
    assert header is not None
    try:
        normalized = normalize_discovered_email(header)
    except (TypeError, ValueError):
        normalized = None
    if normalized is None or not normalized.isascii():
        raise ValueError("SMTP address is invalid.")
    local_part, domain = normalized.rsplit("@", 1)
    if len(local_part) > 64 or len(domain) > 253:
        raise ValueError("SMTP address is invalid.")
    return normalized


class SMTPTransportConfig(BaseModel):
    model_config = _STRICT

    host: str
    port: int
    security_mode: SMTPSecurityMode
    username: str | None = None
    password: SecretStr | None = None
    connection_timeout_seconds: float = 10.0

    @field_validator("host", mode="before")
    @classmethod
    def validate_host(cls, value: object) -> str:
        host = _bounded_header(value, 253)
        assert host is not None
        if any(character.isspace() for character in host):
            raise ValueError("SMTP host is invalid.")
        return host.casefold()

    @field_validator("port", mode="before")
    @classmethod
    def validate_port(cls, value: object) -> int:
        if type(value) is not int or not 1 <= value <= 65535:
            raise ValueError("SMTP port is invalid.")
        return value

    @field_validator("security_mode", mode="before")
    @classmethod
    def validate_security(cls, value: object) -> SMTPSecurityMode:
        if type(value) is not SMTPSecurityMode:
            raise ValueError("SMTP security mode is invalid.")
        return value

    @field_validator("username", mode="before")
    @classmethod
    def validate_username(cls, value: object) -> str | None:
        return _bounded_header(value, 254, optional=True)

    @field_validator("connection_timeout_seconds", mode="before")
    @classmethod
    def validate_timeout(cls, value: object) -> float:
        if type(value) is not float or not 0 < value <= 120:
            raise ValueError("SMTP timeout is invalid.")
        return value

    @model_validator(mode="after")
    def validate_auth_and_plaintext(self) -> "SMTPTransportConfig":
        if (self.username is None) != (self.password is None):
            raise ValueError("SMTP credentials are incomplete.")
        if self.security_mode is SMTPSecurityMode.PLAINTEXT_LOCAL_TEST_ONLY and (
            self.host not in _LOCAL_TEST_HOSTS or self.username is not None
        ):
            raise ValueError("Plaintext SMTP is restricted to unauthenticated local tests.")
        return self


class SMTPSenderIdentity(BaseModel):
    model_config = _STRICT

    email: str
    display_name: str | None = None
    reply_to: str | None = None

    @field_validator("email", mode="before")
    @classmethod
    def validate_email(cls, value: object) -> str:
        return _smtp_address(value)

    @field_validator("reply_to", mode="before")
    @classmethod
    def validate_reply_to(cls, value: object) -> str | None:
        if value is None:
            return None
        return _smtp_address(value)

    @field_validator("display_name", mode="before")
    @classmethod
    def validate_display_name(cls, value: object) -> str | None:
        return _bounded_header(value, 100, optional=True)


class SMTPMessageEnvelope(BaseModel):
    model_config = _STRICT

    envelope_from: str
    envelope_to: str
    sender: SMTPSenderIdentity
    subject: str
    text_body: str
    message_id: str | None = None
    date: datetime | None = None

    @field_validator("envelope_from", "envelope_to", mode="before")
    @classmethod
    def validate_addresses(cls, value: object) -> str:
        return _smtp_address(value)

    @field_validator("subject", mode="before")
    @classmethod
    def validate_subject(cls, value: object) -> str:
        subject = _bounded_header(value, 160)
        assert subject is not None
        return subject

    @field_validator("text_body", mode="before")
    @classmethod
    def validate_body(cls, value: object) -> str:
        if type(value) is not str or value != value.strip() or not 1 <= len(value) <= 5000:
            raise ValueError("SMTP body is invalid.")
        if "\x00" in value or any(
            ord(character) < 32 and character not in "\n\r\t" for character in value
        ):
            raise ValueError("SMTP body is invalid.")
        return value

    @field_validator("message_id", mode="before")
    @classmethod
    def validate_message_id(cls, value: object) -> str | None:
        message_id = _bounded_header(value, 255, optional=True)
        if message_id is not None and (
            not message_id.startswith("<")
            or not message_id.endswith(">")
            or message_id.count("@") != 1
            or not message_id.isascii()
        ):
            raise ValueError("SMTP Message-ID is invalid.")
        return message_id

    @field_validator("date", mode="before")
    @classmethod
    def validate_date(cls, value: object) -> datetime | None:
        if value is None:
            return None
        if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("SMTP date is invalid.")
        return value


class SMTPDeliveryReceipt(BaseModel):
    model_config = _STRICT

    accepted: bool
    recipient: str
    message_id: str
    smtp_code: int | None
    provider: str
    security_mode: SMTPSecurityMode

    @field_validator("accepted", mode="before")
    @classmethod
    def validate_accepted(cls, value: object) -> bool:
        if type(value) is not bool or value is not True:
            raise ValueError("SMTP receipt is invalid.")
        return value

    @field_validator("recipient", mode="before")
    @classmethod
    def validate_recipient(cls, value: object) -> str:
        return _smtp_address(value)

    @field_validator("message_id", mode="before")
    @classmethod
    def validate_receipt_message_id(cls, value: object) -> str:
        message_id = SMTPMessageEnvelope.validate_message_id(value)
        if message_id is None:
            raise ValueError("SMTP receipt is invalid.")
        return message_id

    @field_validator("smtp_code", mode="before")
    @classmethod
    def validate_code(cls, value: object) -> int | None:
        if value is None:
            return None
        if type(value) is not int or not 200 <= value <= 599:
            raise ValueError("SMTP receipt code is invalid.")
        return value

    @field_validator("provider", mode="before")
    @classmethod
    def validate_provider(cls, value: object) -> str:
        provider = _bounded_header(value, 100)
        assert provider is not None
        return provider
