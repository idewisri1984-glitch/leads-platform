from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.contact.channel_normalization import (
    normalize_instagram_url,
    normalize_linkedin_url,
)


class ContactCreate(BaseModel):
    """
    Schema for creating a contact.
    """

    company_id: int

    first_name: str | None = Field(
        default=None,
        max_length=100,
    )

    last_name: str | None = Field(default=None, max_length=100)
    job_title: str | None = None
    email: str | None = None
    phone: str | None = None
    linkedin_url: str | None = None
    instagram_url: str | None = None
    country: str | None = None
    city: str | None = None
    source: str | None = None
    external_id: str | None = None
    status: str = "NEW"
    notes: str | None = None

    @field_validator("first_name", "last_name", mode="before")
    @classmethod
    def normalize_name(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return " ".join(value.split()) or None

    @field_validator("linkedin_url")
    @classmethod
    def normalize_linkedin(cls, value: str | None) -> str | None:
        normalized = normalize_linkedin_url(value)
        if normalized is not None and len(normalized) > 255:
            raise ValueError("LinkedIn URL exceeds 255 characters.")
        return normalized

    @field_validator("instagram_url")
    @classmethod
    def normalize_instagram(cls, value: str | None) -> str | None:
        normalized = normalize_instagram_url(value)
        if normalized is not None and len(normalized) > 255:
            raise ValueError("Instagram URL exceeds 255 characters.")
        return normalized

    @model_validator(mode="after")
    def require_name_or_channel(self) -> "ContactCreate":
        has_name = any(_has_text(value) for value in (self.first_name, self.last_name))
        has_channel = any(
            _has_text(value)
            for value in (self.email, self.phone, self.linkedin_url, self.instagram_url)
        )
        if not has_name and not has_channel:
            raise ValueError("A contact requires a name or usable contact channel.")
        return self


class ContactRead(BaseModel):
    """
    Schema returned to the application.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int

    first_name: str | None
    last_name: str | None
    job_title: str | None
    email: str | None
    phone: str | None
    linkedin_url: str | None
    instagram_url: str | None
    country: str | None
    city: str | None
    source: str | None
    external_id: str | None
    status: str
    notes: str | None


def _has_text(value: str | None) -> bool:
    return value is not None and bool(value.strip())
