from pydantic import BaseModel, ConfigDict


class LeadCreate(BaseModel):
    """
    Schema for creating a lead.
    """

    company_id: int
    contact_id: int | None = None
    status: str = "NEW"
    source: str | None = None
    notes: str | None = None


class LeadRead(BaseModel):
    """
    Schema returned to the application.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    contact_id: int | None
    status: str
    source: str | None
    notes: str | None
