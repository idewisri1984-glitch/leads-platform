from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.company_discovery.models import CompanyDiscoveryCandidateStatus

PositiveStrictInt = Annotated[int, Field(strict=True, gt=0)]


class CompanyDiscoveryCandidatePromotionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: PositiveStrictInt
    project_id: PositiveStrictInt
    company_id: PositiveStrictInt
    previous_status: CompanyDiscoveryCandidateStatus
    current_status: CompanyDiscoveryCandidateStatus
    created_company: bool
    changed: bool

    @model_validator(mode="after")
    def validate_promotion(self) -> "CompanyDiscoveryCandidatePromotionResult":
        if self.current_status != CompanyDiscoveryCandidateStatus.PROMOTED:
            raise ValueError("current_status must be PROMOTED.")
        if self.changed:
            if self.previous_status != CompanyDiscoveryCandidateStatus.REVIEWED:
                raise ValueError("A changed promotion must start in REVIEWED status.")
        elif self.previous_status != CompanyDiscoveryCandidateStatus.PROMOTED:
            raise ValueError("An unchanged promotion must already be PROMOTED.")
        if self.created_company and not self.changed:
            raise ValueError("A created Company requires a changed promotion.")
        return self


__all__ = ["CompanyDiscoveryCandidatePromotionResult"]
