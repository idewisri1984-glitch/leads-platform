from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

PositiveStrictInt = Annotated[StrictInt, Field(gt=0)]


class LeadTaskCreationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: PositiveStrictInt
    company_id: PositiveStrictInt
    lead_id: PositiveStrictInt
    status: Literal["TODO"]
