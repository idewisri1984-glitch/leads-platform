from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.modules.company_discovery.models import CompanyDiscoveryCandidateStatus

_CONFIG = ConfigDict(frozen=True, strict=True, extra="forbid")


class AgentCompanyApplyInput(BaseModel):
    model_config = _CONFIG

    project_id: int
    discovery_run_id: int
    candidate_id: int
    confirmed: Literal[True]

    @field_validator("project_id", "discovery_run_id", "candidate_id", mode="before")
    @classmethod
    def validate_id(cls, value: object) -> object:
        if type(value) is not int or value <= 0:
            raise ValueError("Identifier is invalid.")
        return value

    @field_validator("confirmed", mode="before")
    @classmethod
    def validate_confirmation(cls, value: object) -> object:
        if type(value) is not bool or value is not True:
            raise ValueError("Confirmation is required.")
        return value


class AgentCompanyApplyResult(BaseModel):
    model_config = _CONFIG

    project_id: int
    discovery_run_id: int
    candidate_id: int
    company_id: int
    candidate_status_before: CompanyDiscoveryCandidateStatus
    candidate_status_after: CompanyDiscoveryCandidateStatus
    company_created: bool
    company_reused: bool
    candidate_reviewed: bool
    candidate_promoted: bool
    crm_mutated: bool
    network_call_count: int
    contact_mutation_count: int
    lead_mutation_count: int
    task_mutation_count: int
    human_confirmation_required: Literal[True]
    human_confirmation_received: Literal[True]

    @field_validator("project_id", "discovery_run_id", "candidate_id", "company_id", mode="before")
    @classmethod
    def validate_id(cls, value: object) -> object:
        if type(value) is not int or value <= 0:
            raise ValueError("Identifier is invalid.")
        return value

    @field_validator(
        "network_call_count",
        "contact_mutation_count",
        "lead_mutation_count",
        "task_mutation_count",
        mode="before",
    )
    @classmethod
    def validate_count(cls, value: object) -> object:
        if type(value) is not int or value < 0:
            raise ValueError("Mutation count is invalid.")
        return value

    @field_validator(
        "company_created",
        "company_reused",
        "candidate_reviewed",
        "candidate_promoted",
        "crm_mutated",
        mode="before",
    )
    @classmethod
    def validate_bool(cls, value: object) -> object:
        if type(value) is not bool:
            raise ValueError("Boolean value is invalid.")
        return value

    @field_validator(
        "human_confirmation_required",
        "human_confirmation_received",
        mode="before",
    )
    @classmethod
    def validate_confirmation(cls, value: object) -> object:
        if type(value) is not bool or value is not True:
            raise ValueError("Confirmation state is invalid.")
        return value

    @field_validator("candidate_status_before", "candidate_status_after", mode="before")
    @classmethod
    def validate_status(cls, value: object) -> object:
        if type(value) is not CompanyDiscoveryCandidateStatus:
            raise ValueError("Candidate status is invalid.")
        return value

    @model_validator(mode="after")
    def validate_lifecycle(self) -> Self:
        if self.candidate_status_after is not CompanyDiscoveryCandidateStatus.PROMOTED:
            raise ValueError("Candidate must be promoted.")
        if self.network_call_count != 0 or any(
            count != 0
            for count in (
                self.contact_mutation_count,
                self.lead_mutation_count,
                self.task_mutation_count,
            )
        ):
            raise ValueError("Side-effect counts are invalid.")
        if self.company_created == self.company_reused:
            raise ValueError("Company outcome is inconsistent.")
        expected = {
            CompanyDiscoveryCandidateStatus.DISCOVERED: (True, True, True),
            CompanyDiscoveryCandidateStatus.REVIEWED: (False, True, True),
            CompanyDiscoveryCandidateStatus.PROMOTED: (False, False, False),
        }
        if self.candidate_status_before not in expected:
            raise ValueError("Candidate lifecycle is invalid.")
        reviewed, promoted, mutated = expected[self.candidate_status_before]
        if (
            self.candidate_reviewed != reviewed
            or self.candidate_promoted != promoted
            or self.crm_mutated != mutated
        ):
            raise ValueError("Candidate lifecycle is inconsistent.")
        if self.candidate_status_before is CompanyDiscoveryCandidateStatus.PROMOTED and (
            self.company_created or not self.company_reused
        ):
            raise ValueError("Idempotent Company outcome is inconsistent.")
        return self


__all__ = ["AgentCompanyApplyInput", "AgentCompanyApplyResult"]
