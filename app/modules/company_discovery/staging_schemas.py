import hashlib
import json
import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

from app.modules.company_discovery.models import (
    CompanyDiscoveryCandidateStatus,
    CompanyDiscoveryRunStatus,
)
from app.modules.company_discovery.staging_normalization import normalize_country_code

PositiveInt = Annotated[StrictInt, Field(gt=0)]
NonNegativeInt = Annotated[StrictInt, Field(ge=0)]


class CompanyDiscoverySourceMode(StrEnum):
    SEARCH_PROFILE = "SEARCH_PROFILE"
    AD_HOC = "AD_HOC"


class CompanyDiscoveryRequestSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_mode: CompanyDiscoverySourceMode
    search_profile_id: PositiveInt | None = None
    country_codes: tuple[str, ...] = Field(default=(), max_length=20)
    query_count: Annotated[StrictInt, Field(ge=0, le=100)] = 0
    result_limit: Annotated[StrictInt, Field(gt=0, le=100)]
    total_result_ceiling: Annotated[StrictInt, Field(gt=0, le=1000)]

    @field_validator("country_codes", mode="before")
    @classmethod
    def normalize_countries(cls, value: Any) -> tuple[str, ...]:
        if isinstance(value, str):
            raise ValueError("Country codes must be a collection.")
        normalized = {normalize_country_code(item) for item in value}
        return tuple(sorted(code for code in normalized if code is not None))

    @model_validator(mode="after")
    def validate_source(self) -> "CompanyDiscoveryRequestSnapshot":
        if self.source_mode == CompanyDiscoverySourceMode.SEARCH_PROFILE:
            if self.search_profile_id is None:
                raise ValueError("SEARCH_PROFILE requires search_profile_id.")
        elif self.search_profile_id is not None:
            raise ValueError("AD_HOC must not include search_profile_id.")
        return self

    def canonical_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def canonical_json(self) -> str:
        return json.dumps(
            self.canonical_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )

    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


class CompanyDiscoveryRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: PositiveInt
    search_profile_id: PositiveInt | None = None
    provider: str = Field(min_length=1, max_length=100)
    request_snapshot: CompanyDiscoveryRequestSnapshot
    run_status: CompanyDiscoveryRunStatus = CompanyDiscoveryRunStatus.PENDING
    query_count: NonNegativeInt = 0
    result_count: NonNegativeInt = 0
    candidate_count: NonNegativeInt = 0
    error_code: str | None = Field(default=None, max_length=100)

    @field_validator("provider")
    @classmethod
    def clean_provider(cls, value: str) -> str:
        return _safe_required_text(value, "Provider")

    @field_validator("error_code")
    @classmethod
    def clean_error_code(cls, value: str | None) -> str | None:
        return _error_code(value)

    @model_validator(mode="after")
    def validate_initial_state(self) -> "CompanyDiscoveryRunCreate":
        if self.run_status != CompanyDiscoveryRunStatus.PENDING:
            raise ValueError("A new discovery run must be PENDING.")
        snapshot_id = self.request_snapshot.search_profile_id
        if snapshot_id != self.search_profile_id:
            raise ValueError("Request snapshot search profile does not match the run.")
        return self


class CompanyDiscoveryRunUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_status: CompanyDiscoveryRunStatus | None = None
    query_count: NonNegativeInt | None = None
    result_count: NonNegativeInt | None = None
    candidate_count: NonNegativeInt | None = None
    completed_at: datetime | None = None
    error_code: str | None = Field(default=None, max_length=100)

    @field_validator("error_code")
    @classmethod
    def clean_error_code(cls, value: str | None) -> str | None:
        return _error_code(value)

    @model_validator(mode="after")
    def reject_null_required_updates(self) -> "CompanyDiscoveryRunUpdate":
        for field in ("run_status", "query_count", "result_count", "candidate_count"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} must not be null when supplied.")
        return self


class CompanyDiscoveryRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    search_profile_id: int | None
    provider: str
    run_status: CompanyDiscoveryRunStatus
    request_fingerprint: str
    request_snapshot: dict[str, Any]
    query_count: int
    result_count: int
    candidate_count: int
    started_at: datetime
    completed_at: datetime | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime


class CompanyDiscoveryCandidateCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: PositiveInt
    run_id: PositiveInt
    provider: str = Field(min_length=1, max_length=100)
    name: str | None = Field(default=None, max_length=255)
    website: str | None = Field(default=None, max_length=500)
    country_code: str | None = None
    position: PositiveInt | None = None

    @field_validator("provider")
    @classmethod
    def clean_provider(cls, value: str) -> str:
        return _safe_required_text(value, "Provider")

    @field_validator("name")
    @classmethod
    def reject_name_markup(cls, value: str | None) -> str | None:
        if value is not None and ("<" in value or ">" in value):
            raise ValueError("Raw markup is not allowed in company discovery text.")
        return value

    @field_validator("country_code")
    @classmethod
    def clean_country(cls, value: str | None) -> str | None:
        return normalize_country_code(value)


class CompanyDiscoveryCandidateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    first_seen_run_id: int
    last_seen_run_id: int
    provider: str
    name: str | None
    normalized_name: str | None
    website: str | None
    website_identity: str | None
    country_code: str | None
    identity_key: str
    best_position: int | None
    candidate_status: CompanyDiscoveryCandidateStatus
    promoted_company_id: int | None
    created_at: datetime
    updated_at: datetime


class CompanyDiscoveryCandidateUpsertResult(BaseModel):
    candidate: CompanyDiscoveryCandidateRead
    created: bool = False
    updated: bool = False
    protected: bool = False


def _safe_required_text(value: str, label: str) -> str:
    normalized = " ".join(value.strip().split())
    if not normalized:
        raise ValueError(f"{label} must not be empty.")
    if "<" in normalized or ">" in normalized:
        raise ValueError("Raw markup is not allowed in company discovery text.")
    return normalized


def _error_code(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not re.fullmatch(r"[a-z0-9_]+", normalized, flags=re.ASCII):
        raise ValueError("Error code must be a sanitized lowercase code.")
    return normalized
