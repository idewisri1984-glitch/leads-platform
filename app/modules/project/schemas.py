from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    """
    Schema for creating a project.
    """

    name: str = Field(
        min_length=1,
        max_length=255,
    )


class ProjectRead(BaseModel):
    """
    Schema returned to the application.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
