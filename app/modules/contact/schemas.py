from pydantic import BaseModel, ConfigDict, Field


class ContactCreate(BaseModel):
    """
    Schema for creating a contact.
    """

    company_id: int

    first_name: str = Field(
        min_length=1,
        max_length=100,
    )

    last_name: str | None = None
    job_title: str | None = None
    email: str | None = None
    phone: str | None = None
    linkedin_url: str | None = None
    country: str | None = None
    city: str | None = None
    source: str | None = None
    external_id: str | None = None
    status: str = "NEW"
    notes: str | None = None


class ContactRead(BaseModel):
    """
    Schema returned to the application.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int

    first_name: str
    last_name: str | None
    job_title: str | None
    email: str | None
    phone: str | None
    linkedin_url: str | None
    country: str | None
    city: str | None
    source: str | None
    external_id: str | None
    status: str
    notes: str | None
