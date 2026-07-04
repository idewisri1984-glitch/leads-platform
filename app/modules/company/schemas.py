from pydantic import BaseModel, ConfigDict, Field


class CompanyCreate(BaseModel):
    """
    Schema for creating a company.
    """

    project_id: int

    name: str = Field(
        min_length=1,
        max_length=255,
    )

    website: str | None = None
    country: str | None = None
    city: str | None = None
    industry: str | None = None
    status: str = "NEW"
    notes: str | None = None


class CompanyRead(BaseModel):
    """
    Schema returned to the application.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int

    name: str

    website: str | None
    country: str | None
    city: str | None
    industry: str | None
    status: str
    notes: str | None
