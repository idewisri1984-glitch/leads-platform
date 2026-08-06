from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    StringConstraints,
    field_validator,
    model_validator,
)

_CONFIG = ConfigDict(frozen=True, extra="forbid", strict=True)


class OpenAIDecisionKind(StrEnum):
    SELECT = "SELECT"
    NO_SELECTION = "NO_SELECTION"


class OpenAICompanyFit(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    NOT_SUITABLE = "NOT_SUITABLE"


def _required_string(value: object, maximum: int) -> object:
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise ValueError("String value is invalid.")
    return value


def _optional_string(value: object, maximum: int) -> object:
    if value is None:
        return None
    return _required_string(value, maximum)


class OpenAIDecisionCandidate(BaseModel):
    model_config = _CONFIG

    index: int
    name: str
    website: str | None
    country: str | None
    city: str | None
    industry: str | None
    snippet: str | None
    website_summary: str | None

    @field_validator("index", mode="before")
    @classmethod
    def validate_index(cls, value: object) -> object:
        if type(value) is not int or not 1 <= value <= 5:
            raise ValueError("Candidate index is invalid.")
        return value

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, value: object) -> object:
        return _required_string(value, 200)

    @field_validator("website", mode="before")
    @classmethod
    def validate_website(cls, value: object) -> object:
        return _optional_string(value, 2048)

    @field_validator("country", "city", mode="before")
    @classmethod
    def validate_location(cls, value: object) -> object:
        return _optional_string(value, 100)

    @field_validator("industry", mode="before")
    @classmethod
    def validate_industry(cls, value: object) -> object:
        return _optional_string(value, 150)

    @field_validator("snippet", mode="before")
    @classmethod
    def validate_snippet(cls, value: object) -> object:
        return _optional_string(value, 1200)

    @field_validator("website_summary", mode="before")
    @classmethod
    def validate_website_summary(cls, value: object) -> object:
        return _optional_string(value, 2000)


class OpenAIDecisionRequest(BaseModel):
    model_config = _CONFIG

    goal: str
    candidates: tuple[OpenAIDecisionCandidate, ...]

    @field_validator("goal", mode="before")
    @classmethod
    def validate_goal(cls, value: object) -> object:
        return _required_string(value, 1000)

    @field_validator("candidates", mode="before")
    @classmethod
    def validate_candidates(cls, value: object) -> object:
        if type(value) is not tuple or not 1 <= len(value) <= 5:
            raise ValueError("Candidates must be an exact bounded tuple.")
        if any(type(candidate) is not OpenAIDecisionCandidate for candidate in value):
            raise ValueError("Candidates must contain exact candidate models.")
        return value

    @model_validator(mode="after")
    def validate_candidate_indices(self) -> "OpenAIDecisionRequest":
        indices = tuple(candidate.index for candidate in self.candidates)
        if indices != tuple(range(1, len(self.candidates) + 1)):
            raise ValueError("Candidate indices must be unique, contiguous, and ordered.")
        return self


class OpenAIDecisionResult(BaseModel):
    model_config = _CONFIG

    decision: OpenAIDecisionKind
    selected_candidate_index: int | None
    confidence: float
    company_fit: OpenAICompanyFit
    rationale: str
    next_action_title: str | None
    next_action_description: str | None
    human_review_required: bool

    @field_validator("decision", mode="before")
    @classmethod
    def validate_decision(cls, value: object) -> object:
        if type(value) is not OpenAIDecisionKind:
            raise ValueError("Decision enum is invalid.")
        return value

    @field_validator("selected_candidate_index", mode="before")
    @classmethod
    def validate_selected_index(cls, value: object) -> object:
        if value is not None and (type(value) is not int or not 1 <= value <= 5):
            raise ValueError("Selected candidate index is invalid.")
        return value

    @field_validator("confidence", mode="before")
    @classmethod
    def validate_confidence(cls, value: object) -> object:
        if type(value) is not float or not 0.0 <= value <= 1.0:
            raise ValueError("Confidence is invalid.")
        return value

    @field_validator("company_fit", mode="before")
    @classmethod
    def validate_company_fit(cls, value: object) -> object:
        if type(value) is not OpenAICompanyFit:
            raise ValueError("Company fit enum is invalid.")
        return value

    @field_validator("rationale", mode="before")
    @classmethod
    def validate_rationale(cls, value: object) -> object:
        return _required_string(value, 500)

    @field_validator("next_action_title", mode="before")
    @classmethod
    def validate_next_action_title(cls, value: object) -> object:
        return _optional_string(value, 255)

    @field_validator("next_action_description", mode="before")
    @classmethod
    def validate_next_action_description(cls, value: object) -> object:
        return _optional_string(value, 1000)

    @field_validator("human_review_required", mode="before")
    @classmethod
    def validate_human_review(cls, value: object) -> object:
        if type(value) is not bool or value is not True:
            raise ValueError("Human review must be required.")
        return value

    @model_validator(mode="after")
    def validate_decision_invariants(self) -> "OpenAIDecisionResult":
        if self.decision is OpenAIDecisionKind.SELECT:
            if (
                self.selected_candidate_index is None
                or self.company_fit is OpenAICompanyFit.NOT_SUITABLE
                or self.next_action_title is None
                or self.next_action_description is None
            ):
                raise ValueError("SELECT result is inconsistent.")
        elif (
            self.selected_candidate_index is not None
            or self.company_fit is not OpenAICompanyFit.NOT_SUITABLE
            or self.next_action_title is not None
            or self.next_action_description is not None
        ):
            raise ValueError("NO_SELECTION result is inconsistent.")
        return self


_WireString500 = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=500)]
_WireString255 = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=255)]
_WireString1000 = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=1000)]


class _OpenAIDecisionWireResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    decision: Literal["SELECT", "NO_SELECTION"]
    selected_candidate_index: Annotated[StrictInt, Field(ge=1, le=5)] | None
    confidence: Annotated[StrictFloat, Field(ge=0.0, le=1.0)]
    company_fit: Literal["HIGH", "MEDIUM", "LOW", "NOT_SUITABLE"]
    rationale: _WireString500
    next_action_title: _WireString255 | None
    next_action_description: _WireString1000 | None
    human_review_required: Literal[True]

    @field_validator("confidence", mode="before")
    @classmethod
    def validate_exact_confidence(cls, value: object) -> object:
        if type(value) is not float:
            raise ValueError("Wire confidence must be an exact float.")
        return value

    @field_validator("rationale", "next_action_title", "next_action_description")
    @classmethod
    def reject_blank_wire_strings(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Wire string is blank.")
        return value

    @model_validator(mode="after")
    def validate_wire_invariants(self) -> "_OpenAIDecisionWireResult":
        if self.decision == "SELECT":
            if (
                self.selected_candidate_index is None
                or self.company_fit == "NOT_SUITABLE"
                or self.next_action_title is None
                or self.next_action_description is None
            ):
                raise ValueError("SELECT wire result is inconsistent.")
        elif self.selected_candidate_index is not None or self.company_fit != "NOT_SUITABLE":
            raise ValueError("NO_SELECTION wire result is inconsistent.")
        return self
