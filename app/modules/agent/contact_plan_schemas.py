from enum import StrEnum
from re import fullmatch
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from ..contact_discovery.models import ContactDiscoverySourceType

_CONFIG = ConfigDict(frozen=True, strict=True, extra="forbid")
_MAX_COMPANY_NAME = 255
_MAX_WEBSITE = 255
_MAX_PROVIDER_NAME = 100
_MAX_CONTACT_NAME = 255
_MAX_CONTACT_TITLE = 255
_MAX_EMAIL = 255
_MAX_PHONE = 100
_MAX_SOURCE_URL = 500
_MAX_RATIONALE = 1000
_MAX_LEAD_TITLE = 255
_MAX_TASK_TITLE = 255
_MAX_TASK_DESCRIPTION = 4000


class AgentContactDecision(StrEnum):
    SELECT = "SELECT"
    NO_SELECTION = "NO_SELECTION"


class AgentContactDiscoveryStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    NOT_FOUND = "NOT_FOUND"


def _utf8(value: str) -> str:
    if "\x00" in value:
        raise ValueError("NUL characters are not allowed.")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("String is not valid UTF-8.") from None
    return value


def _normalized(value: object, maximum: int) -> str:
    if type(value) is not str:
        raise ValueError("String value is invalid.")
    normalized = " ".join(_utf8(value).split())
    if not normalized or len(normalized) > maximum:
        raise ValueError("String value is invalid.")
    return normalized


def _optional(value: object, maximum: int) -> str | None:
    if value is None:
        return None
    return _normalized(value, maximum)


class AgentContactPlanInput(BaseModel):
    model_config = _CONFIG

    project_id: int
    company_id: int
    goal: str

    @field_validator("project_id", "company_id", mode="before")
    @classmethod
    def validate_id(cls, value: object) -> object:
        if type(value) is not int or value <= 0:
            raise ValueError("Identifier is invalid.")
        return value

    @field_validator("goal", mode="before")
    @classmethod
    def validate_goal(cls, value: object) -> str:
        return _normalized(value, 2000)


class AgentContactPlanResult(BaseModel):
    model_config = _CONFIG

    project_id: int
    company_id: int
    company_name: str
    company_website: str
    goal: str
    decision: AgentContactDecision
    discovery_status: AgentContactDiscoveryStatus
    provider_name: str
    provider_call_count: int
    attempted_pages: int
    successful_pages: int
    selected_urls: int
    limited_link_scan: bool
    candidate_upsert_count: int
    staged_candidate_count: int
    eligible_candidate_count: int
    selected_candidate_id: int | None
    selected_contact_name: str | None
    selected_contact_title: str | None
    selected_contact_email: str | None
    selected_contact_phone: str | None
    selected_contact_source_url: str | None
    selected_contact_source_type: ContactDiscoverySourceType | None
    selected_contact_confidence: float | None
    selection_rationale: str
    proposed_lead_title: str | None
    proposed_task_title: str | None
    proposed_task_description: str | None
    handoff_token: str | None
    human_review_required: Literal[True]
    staging_mutated: Literal[True]
    contact_mutation_count: Literal[0]
    lead_mutation_count: Literal[0]
    task_mutation_count: Literal[0]

    @field_validator("project_id", "company_id", mode="before")
    @classmethod
    def validate_positive_id(cls, value: object) -> object:
        if type(value) is not int or value <= 0:
            raise ValueError("Identifier is invalid.")
        return value

    @field_validator("selected_candidate_id", mode="before")
    @classmethod
    def validate_optional_id(cls, value: object) -> object:
        if value is not None and (type(value) is not int or value <= 0):
            raise ValueError("Selected candidate ID is invalid.")
        return value

    @field_validator(
        "provider_call_count",
        "attempted_pages",
        "successful_pages",
        "selected_urls",
        "candidate_upsert_count",
        "staged_candidate_count",
        "eligible_candidate_count",
        mode="before",
    )
    @classmethod
    def validate_count(cls, value: object) -> object:
        if type(value) is not int or value < 0:
            raise ValueError("Count is invalid.")
        return value

    @field_validator("company_name", mode="before")
    @classmethod
    def validate_company_name(cls, value: object) -> str:
        return _normalized(value, _MAX_COMPANY_NAME)

    @field_validator("company_website", mode="before")
    @classmethod
    def validate_company_website(cls, value: object) -> str:
        return _normalized(value, _MAX_WEBSITE)

    @field_validator("goal", mode="before")
    @classmethod
    def validate_goal(cls, value: object) -> str:
        return _normalized(value, 2000)

    @field_validator("provider_name", mode="before")
    @classmethod
    def validate_provider_name(cls, value: object) -> str:
        return _normalized(value, _MAX_PROVIDER_NAME)

    @field_validator("selected_contact_name", mode="before")
    @classmethod
    def validate_contact_name(cls, value: object) -> str | None:
        return _optional(value, _MAX_CONTACT_NAME)

    @field_validator("selected_contact_title", mode="before")
    @classmethod
    def validate_contact_title(cls, value: object) -> str | None:
        return _optional(value, _MAX_CONTACT_TITLE)

    @field_validator("selected_contact_email", mode="before")
    @classmethod
    def validate_email(cls, value: object) -> str | None:
        return _optional(value, _MAX_EMAIL)

    @field_validator("selected_contact_phone", mode="before")
    @classmethod
    def validate_phone(cls, value: object) -> str | None:
        return _optional(value, _MAX_PHONE)

    @field_validator("selected_contact_source_url", mode="before")
    @classmethod
    def validate_source_url(cls, value: object) -> str | None:
        return _optional(value, _MAX_SOURCE_URL)

    @field_validator("selected_contact_confidence", mode="before")
    @classmethod
    def validate_confidence(cls, value: object) -> object:
        if value is not None and (type(value) is not float or not 0.0 <= value <= 1.0):
            raise ValueError("Contact confidence is invalid.")
        return value

    @field_validator("selection_rationale", mode="before")
    @classmethod
    def validate_rationale(cls, value: object) -> str:
        return _normalized(value, _MAX_RATIONALE)

    @field_validator("proposed_lead_title", mode="before")
    @classmethod
    def validate_lead_title(cls, value: object) -> str | None:
        return _optional(value, _MAX_LEAD_TITLE)

    @field_validator("proposed_task_title", mode="before")
    @classmethod
    def validate_task_title(cls, value: object) -> str | None:
        return _optional(value, _MAX_TASK_TITLE)

    @field_validator("proposed_task_description", mode="before")
    @classmethod
    def validate_task_description(cls, value: object) -> str | None:
        return _optional(value, _MAX_TASK_DESCRIPTION)

    @field_validator("handoff_token", mode="before")
    @classmethod
    def validate_handoff_token(cls, value: object) -> object:
        if value is not None and (
            type(value) is not str or fullmatch(r"[0-9a-f]{64}", value) is None
        ):
            raise ValueError("Handoff token is invalid.")
        return value

    @model_validator(mode="after")
    def validate_alignment(self) -> Self:
        if self.provider_call_count != 1:
            raise ValueError("Provider call count is invalid.")
        if self.successful_pages > self.attempted_pages:
            raise ValueError("Page counts are inconsistent.")
        if self.eligible_candidate_count > self.staged_candidate_count:
            raise ValueError("Candidate counts are inconsistent.")
        if self.candidate_upsert_count < self.staged_candidate_count:
            raise ValueError("Staging counts are inconsistent.")
        selected_fields = (
            self.selected_candidate_id,
            self.selected_contact_name,
            self.selected_contact_title,
            self.selected_contact_email,
            self.selected_contact_phone,
            self.selected_contact_source_url,
            self.selected_contact_source_type,
            self.selected_contact_confidence,
            self.proposed_lead_title,
            self.proposed_task_title,
            self.proposed_task_description,
            self.handoff_token,
        )
        if self.decision is AgentContactDecision.NO_SELECTION:
            if any(value is not None for value in selected_fields):
                raise ValueError("NO_SELECTION fields are inconsistent.")
            if self.eligible_candidate_count != 0:
                raise ValueError("NO_SELECTION eligibility is inconsistent.")
            return self
        required = (
            self.selected_candidate_id,
            self.selected_contact_name,
            self.selected_contact_source_type,
            self.selected_contact_confidence,
            self.proposed_lead_title,
            self.proposed_task_title,
            self.proposed_task_description,
            self.handoff_token,
        )
        if any(value is None for value in required) or self.eligible_candidate_count <= 0:
            raise ValueError("SELECT fields are incomplete.")
        if self.discovery_status is AgentContactDiscoveryStatus.NOT_FOUND:
            raise ValueError("NOT_FOUND cannot select a candidate.")
        return self


__all__ = [
    "AgentContactDecision",
    "AgentContactDiscoveryStatus",
    "AgentContactPlanInput",
    "AgentContactPlanResult",
]
