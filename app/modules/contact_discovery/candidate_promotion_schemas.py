from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from app.modules.contact_discovery.models import ContactDiscoveryCandidateStatus

PositiveStrictInt = Annotated[StrictInt, Field(gt=0)]


class ContactDiscoveryCandidatePromotionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: PositiveStrictInt
    company_id: PositiveStrictInt
    contact_id: PositiveStrictInt
    previous_status: ContactDiscoveryCandidateStatus
    current_status: ContactDiscoveryCandidateStatus
    created_contact: StrictBool
    changed: StrictBool

    @model_validator(mode="after")
    def validate_promotion(self) -> "ContactDiscoveryCandidatePromotionResult":
        if self.current_status is not ContactDiscoveryCandidateStatus.PROMOTED:
            raise ValueError("current_status must be PROMOTED.")
        expected_previous = (
            ContactDiscoveryCandidateStatus.REVIEWED
            if self.changed
            else ContactDiscoveryCandidateStatus.PROMOTED
        )
        if self.previous_status is not expected_previous:
            raise ValueError("previous_status is inconsistent with changed.")
        if self.created_contact and not self.changed:
            raise ValueError("created_contact requires a changed promotion.")
        return self
