from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

_SAFE_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,127}\Z")
_SAFE_PARAMETER = re.compile(r"[A-Za-z][A-Za-z0-9_.\[\]-]{0,127}\Z")
_SAFE_REQUEST_ID = re.compile(r"(?:req|request)_[A-Za-z0-9][A-Za-z0-9_-]{0,95}\Z")
_RESPONSE_STATUSES = frozenset(
    {"cancelled", "completed", "failed", "in_progress", "incomplete", "queued"}
)
_INCOMPLETE_REASONS = frozenset({"content_filter", "max_output_tokens"})
_CREDENTIAL_MARKERS = (
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "basic ",
    "bearer",
    "candidate",
    "goal",
    "password",
    "prompt",
    "refresh_token",
    "secret",
    "sk-",
    "token=",
)


def _is_safe(value: str, pattern: re.Pattern[str]) -> bool:
    lowered = value.casefold()
    return (
        pattern.fullmatch(value) is not None
        and "://" not in lowered
        and not any(marker in lowered for marker in _CREDENTIAL_MARKERS)
    )


class OpenAIDecisionProviderDiagnostic(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    category: Literal[
        "AUTHENTICATION",
        "RATE_LIMIT",
        "CONNECTION",
        "TIMEOUT",
        "API_STATUS",
        "API_ERROR",
        "INTERNAL_REQUEST_FAILURE",
        "RESPONSE_FAILED",
        "RESPONSE_INCOMPLETE",
        "RESPONSE_STATUS_INVALID",
        "REFUSAL",
        "OUTPUT_INVALID",
        "WIRE_VALIDATION_FAILED",
        "RESULT_VALIDATION_FAILED",
    ]
    exception_class: str | None = None
    http_status: int | None = None
    openai_error_code: str | None = None
    parameter: str | None = None
    request_id: str | None = None
    response_status: str | None = None
    incomplete_reason: str | None = None

    @model_validator(mode="after")
    def validate_safe_fields(self) -> OpenAIDecisionProviderDiagnostic:
        valid = (
            (self.exception_class is None or _is_safe(self.exception_class, _SAFE_TOKEN))
            and (self.http_status is None or 100 <= self.http_status <= 599)
            and (self.openai_error_code is None or _is_safe(self.openai_error_code, _SAFE_TOKEN))
            and (self.parameter is None or _is_safe(self.parameter, _SAFE_PARAMETER))
            and (self.request_id is None or _is_safe(self.request_id, _SAFE_REQUEST_ID))
            and (self.response_status is None or self.response_status in _RESPONSE_STATUSES)
            and (self.incomplete_reason is None or self.incomplete_reason in _INCOMPLETE_REASONS)
        )
        if not valid:
            raise ValueError("OpenAI decision provider diagnostic is invalid.")
        return self
