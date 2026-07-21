from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator

from app.modules.company_discovery.models import CompanyDiscoveryCandidateStatus
from app.modules.company_discovery.staging_schemas import CompanyDiscoveryCandidateRead


class CompanyDiscoveryCandidateReviewAction(StrEnum):
    REVIEW = "REVIEW"
    REJECT = "REJECT"

    def to_status(self) -> CompanyDiscoveryCandidateStatus:
        if self == self.REVIEW:
            return CompanyDiscoveryCandidateStatus.REVIEWED
        return CompanyDiscoveryCandidateStatus.REJECTED


class CompanyDiscoveryCandidateReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate: CompanyDiscoveryCandidateRead
    previous_status: CompanyDiscoveryCandidateStatus
    current_status: CompanyDiscoveryCandidateStatus
    changed: bool

    @model_validator(mode="after")
    def validate_changed(self) -> "CompanyDiscoveryCandidateReviewResult":
        if self.changed and self.previous_status == self.current_status:
            raise ValueError("changed must be false when statuses are equal.")
        if not self.changed and self.previous_status != self.current_status:
            raise ValueError("changed must be true when statuses differ.")
        return self
