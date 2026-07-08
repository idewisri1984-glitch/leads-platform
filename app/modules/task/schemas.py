from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TaskCreate(BaseModel):
    """
    Schema for creating a task.
    """

    lead_id: int
    title: str
    description: str | None = None
    status: str = "TODO"
    due_at: datetime | None = None


class TaskRead(BaseModel):
    """
    Schema returned to the application.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    lead_id: int
    title: str
    description: str | None
    status: str
    due_at: datetime | None
