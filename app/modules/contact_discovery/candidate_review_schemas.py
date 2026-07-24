from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator

from app.modules.contact_discovery.models import ContactDiscoveryCandidateStatus
from app.modules.contact_discovery.schemas import ContactDiscoveryCandidateRead


class ContactDiscoveryCandidateReviewAction(StrEnum):
    REVIEW = "REVIEW"
    REJECT = "REJECT"


class ContactDiscoveryCandidateReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate: ContactDiscoveryCandidateRead
    previous_status: ContactDiscoveryCandidateStatus
    current_status: ContactDiscoveryCandidateStatus
    changed: bool

    @model_validator(mode="after")
    def validate_changed(self) -> "ContactDiscoveryCandidateReviewResult":
        if self.changed and self.previous_status == self.current_status:
            raise ValueError("changed must be false when statuses are equal.")
        if not self.changed and self.previous_status != self.current_status:
            raise ValueError("changed must be true when statuses differ.")
        return self
