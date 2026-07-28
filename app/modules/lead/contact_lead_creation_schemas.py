from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

PositiveStrictInt = Annotated[StrictInt, Field(gt=0)]


class ContactLeadCreationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lead_id: PositiveStrictInt
    company_id: PositiveStrictInt
    contact_id: PositiveStrictInt
    status: Literal["NEW"]
