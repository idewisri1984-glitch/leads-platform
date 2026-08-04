from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.modules.company_discovery.models import CompanyDiscoveryRunStatus
from app.providers.openai_decision import (
    OpenAICompanyFit,
    OpenAIDecisionKind,
    OpenAIDecisionResult,
)

_CONFIG = ConfigDict(frozen=True, strict=True, extra="forbid")


class AgentCompanyPlanInput(BaseModel):
    model_config = _CONFIG

    project_id: int
    search_profile_id: int
    goal: str

    @field_validator("project_id", "search_profile_id", mode="before")
    @classmethod
    def validate_id(cls, value: object) -> object:
        if type(value) is not int or value <= 0:
            raise ValueError("Identifier is invalid.")
        return value

    @field_validator("goal", mode="before")
    @classmethod
    def validate_goal(cls, value: object) -> object:
        if type(value) is not str or not value.strip() or len(value) > 1000:
            raise ValueError("Goal is invalid.")
        return value


class AgentCompanyPlanResult(BaseModel):
    model_config = _CONFIG

    project_id: int
    search_profile_id: int
    discovery_run_id: int
    query: str
    discovery_run_status: CompanyDiscoveryRunStatus
    staged_candidate_count: int
    eligible_candidate_count: int
    decision: OpenAIDecisionKind | None
    selected_candidate_id: int | None
    selected_candidate_index: int | None
    confidence: float | None
    company_fit: OpenAICompanyFit | None
    rationale: str | None
    next_action_title: str | None
    next_action_description: str | None
    human_review_required: bool | None
    serpapi_call_count: int
    openai_call_count: int
    crm_mutated: Literal[False]
    candidate_promoted: Literal[False]

    @field_validator("project_id", "search_profile_id", "discovery_run_id", mode="before")
    @classmethod
    def validate_positive_id(cls, value: object) -> object:
        if type(value) is not int or value <= 0:
            raise ValueError("Identifier is invalid.")
        return value

    @field_validator("staged_candidate_count", mode="before")
    @classmethod
    def validate_staged_count(cls, value: object) -> object:
        if type(value) is not int or value < 0:
            raise ValueError("Staged candidate count is invalid.")
        return value

    @field_validator("eligible_candidate_count", mode="before")
    @classmethod
    def validate_eligible_count(cls, value: object) -> object:
        if type(value) is not int or not 0 <= value <= 5:
            raise ValueError("Eligible candidate count is invalid.")
        return value

    @field_validator("selected_candidate_id", mode="before")
    @classmethod
    def validate_selected_id(cls, value: object) -> object:
        if value is not None and (type(value) is not int or value <= 0):
            raise ValueError("Selected candidate ID is invalid.")
        return value

    @field_validator("selected_candidate_index", mode="before")
    @classmethod
    def validate_selected_index(cls, value: object) -> object:
        if value is not None and (type(value) is not int or not 1 <= value <= 5):
            raise ValueError("Selected candidate index is invalid.")
        return value

    @field_validator("query", mode="before")
    @classmethod
    def validate_query(cls, value: object) -> object:
        if type(value) is not str or not value.strip():
            raise ValueError("Query is invalid.")
        return value

    @field_validator("serpapi_call_count", mode="before")
    @classmethod
    def validate_serpapi_calls(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("SerpAPI call count is invalid.")
        return value

    @field_validator("openai_call_count", mode="before")
    @classmethod
    def validate_openai_calls(cls, value: object) -> object:
        if type(value) is not int or value not in (0, 1):
            raise ValueError("OpenAI call count is invalid.")
        return value

    @model_validator(mode="after")
    def validate_alignment(self) -> Self:
        if self.eligible_candidate_count > self.staged_candidate_count:
            raise ValueError("Candidate counts are inconsistent.")
        decision_values = (
            self.confidence,
            self.company_fit,
            self.rationale,
            self.next_action_title,
            self.next_action_description,
            self.human_review_required,
        )
        if self.openai_call_count == 0:
            if self.decision is not None or self.selected_candidate_id is not None:
                raise ValueError("Decision fields are inconsistent.")
            if self.selected_candidate_index is not None or any(
                v is not None for v in decision_values
            ):
                raise ValueError("Decision fields are inconsistent.")
            return self
        if self.decision is None:
            raise ValueError("Decision is required after an OpenAI call.")
        OpenAIDecisionResult(
            decision=self.decision,
            selected_candidate_index=self.selected_candidate_index,
            confidence=self.confidence,
            company_fit=self.company_fit,
            rationale=self.rationale,
            next_action_title=self.next_action_title,
            next_action_description=self.next_action_description,
            human_review_required=self.human_review_required,
        )
        if self.decision is OpenAIDecisionKind.NO_SELECTION:
            if self.selected_candidate_id is not None or self.selected_candidate_index is not None:
                raise ValueError("No-selection fields are inconsistent.")
        elif self.decision is OpenAIDecisionKind.SELECT:
            if self.selected_candidate_id is None or self.selected_candidate_index is None:
                raise ValueError("Selection fields are incomplete.")
            if self.selected_candidate_index > self.eligible_candidate_count:
                raise ValueError("Selected candidate index is inconsistent.")
        else:
            raise ValueError("Decision is required after an OpenAI call.")
        if self.human_review_required is not True:
            raise ValueError("Human review is required.")
        return self


__all__ = ["AgentCompanyPlanInput", "AgentCompanyPlanResult"]
