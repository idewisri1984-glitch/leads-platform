import re
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from app.modules.company_discovery.models import CompanyDiscoveryRunStatus
from app.modules.company_discovery.staging_normalization import (
    normalize_country_code,
    normalize_display_name,
    normalize_staging_website,
)

PositiveInt = Annotated[StrictInt, Field(gt=0)]
NonNegativeInt = Annotated[StrictInt, Field(ge=0)]


_KNOWN_ERROR_CODES = {
    "authentication_error",
    "configuration_error",
    "quota_exceeded",
    "rate_limit_error",
    "request_error",
    "response_error",
    "response_too_large",
    "provider_error",
    "candidate_invalid",
    "execution_invalid",
    "execution_failed",
}


def _error_code(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not re.fullmatch(r"[a-z0-9_]+", normalized, flags=re.ASCII):
        raise ValueError("Error code must be a sanitized lowercase code.")
    return normalized


class CompanyDiscoveryStagingCandidateDraft(BaseModel):
    """
    Candidate draft used before run persistence.
    """

    model_config = ConfigDict(extra="forbid")

    project_id: PositiveInt
    provider: str = Field(min_length=1, max_length=100)
    name: str | None = Field(default=None, max_length=255)
    website: str | None = Field(default=None, max_length=500)
    country_code: str | None = Field(default=None)
    position: PositiveInt | None = None

    @field_validator("provider")
    @classmethod
    def _clean_provider(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("Provider must not be empty.")
        if "<" in normalized or ">" in normalized:
            raise ValueError("Raw markup is not allowed in company discovery text.")
        return normalized

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.strip().split())
        if not normalized:
            return None
        if "<" in normalized or ">" in normalized:
            raise ValueError("Raw markup is not allowed in company discovery text.")
        return normalized

    @field_validator("website")
    @classmethod
    def _clean_website(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.strip().split())
        if not normalized:
            return None
        if "<" in normalized or ">" in normalized:
            raise ValueError("Raw markup is not allowed in company discovery text.")
        return normalized

    @field_validator("country_code")
    @classmethod
    def _normalize_country_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_country_code(value)


class CompanyDiscoveryStagingCandidatePreview(BaseModel):
    """
    Bounded and safe preview of candidate rows for orchestration result.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=255)
    website: str | None = Field(default=None, max_length=500)
    website_identity: str | None = Field(default=None, max_length=300)
    country_code: str | None = None
    best_position: PositiveInt | None = None
    identity_key: str = Field(max_length=700)

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str | None) -> str | None:
        return normalize_display_name(value)

    @field_validator("website")
    @classmethod
    def _clean_website(cls, value: str | None) -> str | None:
        if value is None:
            return None
        website, _ = normalize_staging_website(value)
        return website

    @field_validator("website_identity")
    @classmethod
    def _clean_website_identity(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.strip().split())
        if not normalized:
            return None
        if "<" in normalized or ">" in normalized:
            raise ValueError("Raw markup is not allowed in company discovery text.")
        return normalized

    @field_validator("country_code")
    @classmethod
    def _upper_country_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_country_code(value)

    @field_validator("identity_key")
    @classmethod
    def _clean_identity_key(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("Identity key is required.")
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("Identity key is required.")
        if "<" in normalized or ">" in normalized:
            raise ValueError("Raw markup is not allowed in company discovery text.")
        return normalized


class CompanyDiscoveryStagingRunResult(BaseModel):
    """
    Safe and deterministic output of staging orchestration.
    """

    model_config = ConfigDict(extra="forbid")

    project_id: PositiveInt
    search_profile_id: PositiveInt
    profile_name: str = Field(max_length=255)
    provider: str = Field(min_length=1, max_length=100)
    dry_run: bool

    status: CompanyDiscoveryRunStatus
    request_fingerprint: str = Field(min_length=64, max_length=64)
    query_count: NonNegativeInt = 0
    executed_queries: NonNegativeInt = 0
    successful_queries: NonNegativeInt = 0
    provider_result_count: NonNegativeInt = 0
    provider_error_count: NonNegativeInt = 0
    existing_adapter_error_count: NonNegativeInt = 0
    rejected_candidate_count: NonNegativeInt = 0
    duplicate_candidate_count: NonNegativeInt = 0
    unique_candidate_count: NonNegativeInt = 0
    candidate_upserts: NonNegativeInt = 0
    candidates_created: NonNegativeInt = 0
    candidates_updated: NonNegativeInt = 0
    candidates_protected: NonNegativeInt = 0

    run_id: int | None = None
    run_persisted: bool = False
    stopped_early: bool = False
    stop_reason: str | None = None
    error_code: str | None = Field(default=None, max_length=100)
    candidates: list[CompanyDiscoveryStagingCandidatePreview] = Field(default_factory=list)
    completed_at: datetime | None = None

    @field_validator("provider")
    @classmethod
    def _clean_provider(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("Provider must not be empty.")
        if "<" in normalized or ">" in normalized:
            raise ValueError("Raw markup is not allowed in company discovery text.")
        return normalized

    @field_validator("profile_name")
    @classmethod
    def _clean_profile_name(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("Profile name is required.")
        return normalized

    @field_validator("error_code")
    @classmethod
    def _clean_error_code(cls, value: str | None) -> str | None:
        return _error_code(value)

    @field_validator("stop_reason")
    @classmethod
    def _clean_stop_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Stop reason must not be empty.")
        if "<" in normalized or ">" in normalized:
            raise ValueError("Raw markup is not allowed in stop reason.")
        return normalized

    @model_validator(mode="after")
    def _validate_counts(self) -> "CompanyDiscoveryStagingRunResult":
        if self.stop_reason is not None and not self.stopped_early:
            raise ValueError("stop_reason must be absent when stopped_early is false.")

        if self.stopped_early and self.stop_reason is None:
            raise ValueError("stop_reason is required when stopped_early is true.")

        if self.unique_candidate_count != len(self.candidates):
            raise ValueError("unique_candidate_count must equal number of candidate previews.")

        if self.dry_run:
            if self.run_id is not None:
                raise ValueError("run_id must be absent for dry runs.")
            if self.candidate_upserts != 0:
                raise ValueError("Dry run must not claim repository upserts.")
            if self.candidates_created != 0:
                raise ValueError("Dry run must not claim created candidates.")
            if self.candidates_updated != 0:
                raise ValueError("Dry run must not claim updated candidates.")
            if self.candidates_protected != 0:
                raise ValueError("Dry run must not claim protected candidates.")
            if self.run_persisted:
                raise ValueError("dry_run and run_persisted are mutually exclusive.")
        elif not self.run_persisted:
            if self.run_id is not None:
                raise ValueError("run_id must be absent when run_persisted is false.")
            if self.candidate_upserts != 0:
                raise ValueError("Persisted=False results cannot report candidate upserts.")

        if self.run_persisted:
            if self.dry_run:
                raise ValueError("run_persisted cannot be true for dry runs.")
            if self.run_id is None:
                raise ValueError("run_id is required when run_persisted is true.")
            if type(self.run_id) is not int or self.run_id <= 0:
                raise ValueError("run_id must be a positive non-bool integer.")

        if self.status in (CompanyDiscoveryRunStatus.SUCCEEDED, CompanyDiscoveryRunStatus.PARTIAL):
            if self.run_persisted and self.candidate_upserts != self.unique_candidate_count:
                raise ValueError(
                    "candidate_upserts must match unique_candidate_count for "
                    "persisted SUCCEEDED/PARTIAL runs."
                )
            if (
                self.status == CompanyDiscoveryRunStatus.PARTIAL
                and self.unique_candidate_count == 0
                and self.error_code is None
            ):
                raise ValueError("PARTIAL status must include error_code.")
            if (
                self.run_persisted
                and self.unique_candidate_count > 0
                and self.candidate_upserts == 0
            ):
                raise ValueError("Persisted SUCCEEDED/PARTIAL with candidates must report upserts.")

        if self.status in (CompanyDiscoveryRunStatus.NOT_FOUND, CompanyDiscoveryRunStatus.FAILED):
            if self.unique_candidate_count != 0:
                raise ValueError("NOT_FOUND and FAILED runs should have zero unique candidates.")
            if self.candidate_upserts != 0:
                raise ValueError("NOT_FOUND and FAILED runs must not report upserts.")
            if self.candidates_created != 0:
                raise ValueError("NOT_FOUND and FAILED runs must not report created candidates.")
            if self.candidates_updated != 0:
                raise ValueError("NOT_FOUND and FAILED runs must not report updated candidates.")
            if self.candidates_protected != 0:
                raise ValueError("NOT_FOUND and FAILED runs must not report protected candidates.")

        if self.candidates_created > self.candidate_upserts:
            raise ValueError("candidates_created cannot exceed candidate_upserts.")

        if self.candidates_updated > self.candidate_upserts:
            raise ValueError("candidates_updated cannot exceed candidate_upserts.")

        if self.candidates_created + self.candidates_updated > self.candidate_upserts:
            raise ValueError(
                "candidates_created + candidates_updated must not exceed candidate_upserts."
            )

        if self.status in (
            CompanyDiscoveryRunStatus.SUCCEEDED,
            CompanyDiscoveryRunStatus.NOT_FOUND,
        ):
            if self.error_code is not None:
                raise ValueError("Successful terminal status must not include error_code.")
        else:
            if self.error_code is None:
                raise ValueError("Failed terminal status must include error_code.")
            if self.error_code not in _KNOWN_ERROR_CODES:
                raise ValueError("error_code is not a sanitized orchestration code.")

        if self.executed_queries > self.query_count:
            raise ValueError("executed_queries cannot exceed query_count.")

        return self
