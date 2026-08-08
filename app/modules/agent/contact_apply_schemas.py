from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from ..contact_discovery.models import ContactDiscoveryCandidateStatus

_CONFIG = ConfigDict(frozen=True, strict=True, extra="forbid")


class AgentContactApplyInput(BaseModel):
    model_config = _CONFIG

    project_id: int
    company_id: int
    candidate_id: int
    goal: str
    handoff_token: str
    confirmed: Literal[True]

    @field_validator("project_id", "company_id", "candidate_id", mode="before")
    @classmethod
    def validate_id(cls, value: object) -> object:
        if type(value) is not int or value <= 0:
            raise ValueError("Identifier is invalid.")
        return value

    @field_validator("goal", mode="before")
    @classmethod
    def validate_goal(cls, value: object) -> str:
        if type(value) is not str:
            raise ValueError("Goal is invalid.")
        normalized = " ".join(value.split())
        if not normalized or len(normalized) > 2000:
            raise ValueError("Goal is invalid.")
        return normalized

    @field_validator("handoff_token", mode="before")
    @classmethod
    def validate_token(cls, value: object) -> str:
        if (
            type(value) is not str
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ValueError("Handoff token is invalid.")
        return value

    @field_validator("confirmed", mode="before")
    @classmethod
    def validate_confirmation(cls, value: object) -> object:
        if type(value) is not bool or value is not True:
            raise ValueError("Confirmation is required.")
        return value


class AgentContactApplyResult(BaseModel):
    model_config = _CONFIG

    project_id: int
    company_id: int
    candidate_id: int
    contact_id: int
    lead_id: int
    task_id: int
    candidate_status_before: ContactDiscoveryCandidateStatus
    candidate_status_after: ContactDiscoveryCandidateStatus
    candidate_reviewed: bool
    candidate_promoted: bool
    contact_created: bool
    contact_reused: bool
    lead_created: bool
    lead_reused: bool
    task_created: bool
    task_reused: bool
    staging_mutated: bool
    crm_mutated: bool
    network_call_count: int
    contact_mutation_count: int
    lead_mutation_count: int
    task_mutation_count: int
    handoff_verified: Literal[True]
    human_confirmation_required: Literal[True]
    human_confirmation_received: Literal[True]

    @field_validator(
        "project_id",
        "company_id",
        "candidate_id",
        "contact_id",
        "lead_id",
        "task_id",
        mode="before",
    )
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
        "candidate_reviewed",
        "candidate_promoted",
        "contact_created",
        "contact_reused",
        "lead_created",
        "lead_reused",
        "task_created",
        "task_reused",
        "staging_mutated",
        "crm_mutated",
        mode="before",
    )
    @classmethod
    def validate_bool(cls, value: object) -> object:
        if type(value) is not bool:
            raise ValueError("Boolean value is invalid.")
        return value

    @field_validator(
        "handoff_verified",
        "human_confirmation_required",
        "human_confirmation_received",
        mode="before",
    )
    @classmethod
    def validate_true(cls, value: object) -> object:
        if type(value) is not bool or value is not True:
            raise ValueError("Confirmation state is invalid.")
        return value

    @field_validator("candidate_status_before", "candidate_status_after", mode="before")
    @classmethod
    def validate_status(cls, value: object) -> object:
        if type(value) is not ContactDiscoveryCandidateStatus:
            raise ValueError("Candidate status is invalid.")
        return value

    @model_validator(mode="after")
    def validate_invariants(self) -> Self:
        if self.candidate_status_after is not ContactDiscoveryCandidateStatus.PROMOTED:
            raise ValueError("Candidate must be promoted.")
        for created, reused in (
            (self.contact_created, self.contact_reused),
            (self.lead_created, self.lead_reused),
            (self.task_created, self.task_reused),
        ):
            if created == reused:
                raise ValueError("Materialization outcome is inconsistent.")
        if self.network_call_count != 0 or (
            self.contact_mutation_count != int(self.contact_created)
            or self.lead_mutation_count != int(self.lead_created)
            or self.task_mutation_count != int(self.task_created)
        ):
            raise ValueError("Mutation counts are inconsistent.")
        expected = {
            ContactDiscoveryCandidateStatus.DISCOVERED: (True, True),
            ContactDiscoveryCandidateStatus.REVIEWED: (False, True),
            ContactDiscoveryCandidateStatus.PROMOTED: (False, False),
        }
        if self.candidate_status_before not in expected:
            raise ValueError("Candidate lifecycle is invalid.")
        reviewed, promoted = expected[self.candidate_status_before]
        if self.candidate_reviewed != reviewed or self.candidate_promoted != promoted:
            raise ValueError("Candidate lifecycle is inconsistent.")
        if self.staging_mutated != (self.candidate_reviewed or self.candidate_promoted):
            raise ValueError("Staging mutation state is inconsistent.")
        if self.crm_mutated != (self.contact_created or self.lead_created or self.task_created):
            raise ValueError("CRM mutation state is inconsistent.")
        return self


__all__ = ["AgentContactApplyInput", "AgentContactApplyResult"]
