from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, field_serializer, field_validator

_STRICT = ConfigDict(frozen=True, extra="forbid", strict=True)


def required_text(value: object, maximum: int, *, minimum: int = 1) -> str:
    if type(value) is not str or value != value.strip() or not minimum <= len(value) <= maximum:
        raise ValueError("Text value is invalid.")
    if any(ord(character) < 32 and character not in "\n\r\t" for character in value):
        raise ValueError("Text contains control characters.")
    return value


def optional_text(value: object, maximum: int) -> str | None:
    if value is None:
        return None
    return required_text(value, maximum)


def positive_id(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError("Identifier is invalid.")
    return value


class EmailLanguage(StrEnum):
    EN = "en"
    RU = "ru"
    ID = "id"


class EmailTone(StrEnum):
    PROFESSIONAL = "professional"
    WARM = "warm"
    CONCISE = "concise"


class EmailPersonalizationContext(BaseModel):
    model_config = _STRICT

    project_id: int
    project_name: str
    company_id: int
    company_name: str
    company_website: str | None
    company_city: str | None
    company_country: str | None
    company_industry: str | None
    company_notes_data: str | None
    contact_id: int | None
    recipient_name: str
    recipient_role: str | None
    recipient_email: str
    lead_id: int
    lead_status: str
    lead_source: str | None
    task_id: int
    task_title: str
    task_description_data: str | None

    @field_validator("project_id", "company_id", "lead_id", "task_id", mode="before")
    @classmethod
    def validate_ids(cls, value: object) -> int:
        return positive_id(value)

    @field_validator("contact_id", mode="before")
    @classmethod
    def validate_contact_id(cls, value: object) -> int | None:
        return None if value is None else positive_id(value)

    @field_validator("project_name", "company_name", "recipient_name", mode="before")
    @classmethod
    def validate_names(cls, value: object) -> str:
        return required_text(value, 255)

    @field_validator("recipient_email", mode="before")
    @classmethod
    def validate_email(cls, value: object) -> str:
        return required_text(value, 255)

    @field_validator("recipient_role", mode="before")
    @classmethod
    def validate_role(cls, value: object) -> str | None:
        return optional_text(value, 150)

    @field_validator("company_website", mode="before")
    @classmethod
    def validate_website(cls, value: object) -> str | None:
        return optional_text(value, 255)

    @field_validator("company_city", "company_country", "company_industry", mode="before")
    @classmethod
    def validate_company_fields(cls, value: object) -> str | None:
        return optional_text(value, 150)

    @field_validator("company_notes_data", "task_description_data", mode="before")
    @classmethod
    def validate_untrusted_data(cls, value: object) -> str | None:
        return optional_text(value, 1000)

    @field_validator("lead_status", mode="before")
    @classmethod
    def validate_lead_status(cls, value: object) -> str:
        return required_text(value, 50)

    @field_validator("lead_source", mode="before")
    @classmethod
    def validate_lead_source(cls, value: object) -> str | None:
        return optional_text(value, 100)

    @field_validator("task_title", mode="before")
    @classmethod
    def validate_task_title(cls, value: object) -> str:
        return required_text(value, 255)


class EmailDraftGenerationInput(BaseModel):
    model_config = _STRICT

    project_id: int
    company_id: int
    contact_id: int | None
    lead_id: int
    task_id: int
    sender_name: str
    sender_company: str
    language: EmailLanguage
    tone: EmailTone
    purpose: str
    value_proposition: str | None = None
    prompt_version: str

    @field_validator("project_id", "company_id", "lead_id", "task_id", mode="before")
    @classmethod
    def validate_ids(cls, value: object) -> int:
        return positive_id(value)

    @field_validator("contact_id", mode="before")
    @classmethod
    def validate_contact_id(cls, value: object) -> int | None:
        return None if value is None else positive_id(value)

    @field_validator("sender_name", mode="before")
    @classmethod
    def validate_sender(cls, value: object) -> str:
        return required_text(value, 150)

    @field_validator("sender_company", mode="before")
    @classmethod
    def validate_sender_company(cls, value: object) -> str:
        return required_text(value, 200)

    @field_validator("language", mode="before")
    @classmethod
    def validate_language(cls, value: object) -> EmailLanguage:
        if type(value) is not EmailLanguage:
            raise ValueError("Language is invalid.")
        return value

    @field_validator("tone", mode="before")
    @classmethod
    def validate_tone(cls, value: object) -> EmailTone:
        if type(value) is not EmailTone:
            raise ValueError("Tone is invalid.")
        return value

    @field_validator("purpose", mode="before")
    @classmethod
    def validate_purpose(cls, value: object) -> str:
        return required_text(value, 500)

    @field_validator("value_proposition", mode="before")
    @classmethod
    def validate_value(cls, value: object) -> str | None:
        return optional_text(value, 1000)

    @field_validator("prompt_version", mode="before")
    @classmethod
    def validate_prompt_version(cls, value: object) -> str:
        return required_text(value, 100)


class EmailDraftProviderRequest(BaseModel):
    model_config = _STRICT

    context: EmailPersonalizationContext
    sender_name: str
    sender_company: str
    language: EmailLanguage
    tone: EmailTone
    purpose: str
    value_proposition: str | None
    prompt_version: str


class EmailDraftGenerationResult(BaseModel):
    model_config = _STRICT

    subject: str
    text_body: str
    language: EmailLanguage
    provider: str
    model: str
    prompt_version: str

    @field_validator("subject", mode="before")
    @classmethod
    def validate_subject(cls, value: object) -> str:
        return required_text(value, 160)

    @field_validator("text_body", mode="before")
    @classmethod
    def validate_body(cls, value: object) -> str:
        text = required_text(value, 5000, minimum=40)
        lowered = text.lower()
        if "system prompt" in lowered or "ignore previous instructions" in lowered:
            raise ValueError("Body contains prompt leakage.")
        return text

    @field_validator("language", mode="before")
    @classmethod
    def validate_language(cls, value: object) -> EmailLanguage:
        if type(value) is not EmailLanguage:
            raise ValueError("Language is invalid.")
        return value

    @field_validator("provider", "prompt_version", mode="before")
    @classmethod
    def validate_metadata(cls, value: object) -> str:
        return required_text(value, 100)

    @field_validator("model", mode="before")
    @classmethod
    def validate_model(cls, value: object) -> str:
        return required_text(value, 200)


class EmailDraftReviewInput(BaseModel):
    model_config = _STRICT

    project_id: int
    company_id: int
    contact_id: int | None
    draft_id: int
    confirmed: bool

    @field_validator("project_id", "company_id", "draft_id", mode="before")
    @classmethod
    def validate_ids(cls, value: object) -> int:
        return positive_id(value)

    @field_validator("contact_id", mode="before")
    @classmethod
    def validate_contact_id(cls, value: object) -> int | None:
        return None if value is None else positive_id(value)

    @field_validator("confirmed", mode="before")
    @classmethod
    def validate_confirmation(cls, value: object) -> bool:
        if type(value) is not bool or value is not True:
            raise ValueError("Confirmation is required.")
        return value


class EmailDraftScopeInput(BaseModel):
    model_config = _STRICT

    project_id: int
    company_id: int
    contact_id: int | None
    draft_id: int

    @field_validator("project_id", "company_id", "draft_id", mode="before")
    @classmethod
    def validate_ids(cls, value: object) -> int:
        return positive_id(value)

    @field_validator("contact_id", mode="before")
    @classmethod
    def validate_contact_id(cls, value: object) -> int | None:
        return None if value is None else positive_id(value)


class EmailDraftRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True, extra="forbid", strict=True)

    id: int
    project_id: int
    company_id: int
    contact_id: int | None
    lead_id: int
    task_id: int
    recipient_email: str
    recipient_name: str
    recipient_role: str | None
    sender_name: str
    sender_company: str
    generation_tone: str
    generation_purpose: str
    generation_value_proposition: str | None
    subject: str
    text_body: str
    language: str
    prompt_version: str
    provider: str
    model: str
    context_fingerprint: str
    content_hash: str
    status: str
    generated_at: datetime
    reviewed_at: datetime | None
    approved_at: datetime | None
    rejected_at: datetime | None

    @field_serializer(
        "generated_at",
        "reviewed_at",
        "approved_at",
        "rejected_at",
        when_used="json",
    )
    def serialize_datetime(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        utc_value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return utc_value.isoformat().replace("+00:00", "Z")
