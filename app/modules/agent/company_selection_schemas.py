from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.providers.openai_decision import OpenAIDecisionRequest

_CONFIG = ConfigDict(frozen=True, extra="forbid", strict=True)


class AgentCompanySelectionBinding(BaseModel):
    model_config = _CONFIG

    index: int
    candidate_id: int

    @field_validator("index", mode="before")
    @classmethod
    def validate_index(cls, value: object) -> object:
        if type(value) is not int or not 1 <= value <= 5:
            raise ValueError("Binding index is invalid.")
        return value

    @field_validator("candidate_id", mode="before")
    @classmethod
    def validate_candidate_id(cls, value: object) -> object:
        if type(value) is not int or value <= 0:
            raise ValueError("Candidate ID is invalid.")
        return value


class AgentCompanySelectionInput(BaseModel):
    model_config = _CONFIG

    project_id: int
    run_id: int
    request: OpenAIDecisionRequest
    bindings: tuple[AgentCompanySelectionBinding, ...]

    @field_validator("project_id", "run_id", mode="before")
    @classmethod
    def validate_identifier(cls, value: object) -> object:
        if type(value) is not int or value <= 0:
            raise ValueError("Identifier is invalid.")
        return value

    @field_validator("request", mode="before")
    @classmethod
    def validate_request(cls, value: object) -> object:
        if type(value) is not OpenAIDecisionRequest:
            raise ValueError("Decision request is invalid.")
        return value

    @field_validator("bindings", mode="before")
    @classmethod
    def validate_bindings(cls, value: object) -> object:
        if type(value) is not tuple or not 1 <= len(value) <= 5:
            raise ValueError("Bindings must be an exact bounded tuple.")
        if any(type(binding) is not AgentCompanySelectionBinding for binding in value):
            raise ValueError("Bindings must contain exact binding models.")
        return value

    @model_validator(mode="after")
    def validate_alignment(self) -> "AgentCompanySelectionInput":
        if len(self.bindings) != len(self.request.candidates):
            raise ValueError("Bindings and candidates are inconsistent.")
        expected = tuple(range(1, len(self.bindings) + 1))
        binding_indices = tuple(binding.index for binding in self.bindings)
        request_indices = tuple(candidate.index for candidate in self.request.candidates)
        if binding_indices != expected or request_indices != expected:
            raise ValueError("Selection indices are inconsistent.")
        if any(
            binding.index != candidate.index
            for binding, candidate in zip(self.bindings, self.request.candidates, strict=True)
        ):
            raise ValueError("Binding order is inconsistent.")
        candidate_ids = tuple(binding.candidate_id for binding in self.bindings)
        if len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError("Candidate IDs must be unique.")
        return self


__all__ = ["AgentCompanySelectionBinding", "AgentCompanySelectionInput"]
