import re
from dataclasses import dataclass
from enum import StrEnum

_ERROR_CODE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}\Z")
_EXCEPTION_CLASS = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
_PARAMETER = re.compile(r"[A-Za-z][A-Za-z0-9_.\[\]-]{0,127}\Z")
_REQUEST_ID = re.compile(r"(?:req|request)_[A-Za-z0-9][A-Za-z0-9_-]{0,95}\Z")
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


def _has_credential_or_url(value: str) -> bool:
    lowered = value.casefold()
    return "://" in lowered or any(marker in lowered for marker in _CREDENTIAL_MARKERS)


def _safe_exception_class(value: object) -> str | None:
    if type(value) is not str or _EXCEPTION_CLASS.fullmatch(value) is None:
        return None
    return None if _has_credential_or_url(value) else value


def _safe_error_code(value: object) -> str | None:
    if type(value) is not str or _ERROR_CODE.fullmatch(value) is None:
        return None
    return None if _has_credential_or_url(value) else value


def _safe_parameter(value: object) -> str | None:
    if type(value) is not str or _PARAMETER.fullmatch(value) is None:
        return None
    return None if _has_credential_or_url(value) else value


def _safe_request_id(value: object) -> str | None:
    if type(value) is not str or _REQUEST_ID.fullmatch(value) is None:
        return None
    return None if _has_credential_or_url(value) else value


def _safe_response_status(value: object) -> str | None:
    return value if type(value) is str and value in _RESPONSE_STATUSES else None


def _safe_incomplete_reason(value: object) -> str | None:
    return value if type(value) is str and value in _INCOMPLETE_REASONS else None


def _safe_http_status(value: object) -> int | None:
    return value if type(value) is int and 100 <= value <= 599 else None


class OpenAIDecisionDiagnosticCategory(StrEnum):
    AUTHENTICATION = "AUTHENTICATION"
    RATE_LIMIT = "RATE_LIMIT"
    CONNECTION = "CONNECTION"
    TIMEOUT = "TIMEOUT"
    API_STATUS = "API_STATUS"
    API_ERROR = "API_ERROR"
    INTERNAL_REQUEST_FAILURE = "INTERNAL_REQUEST_FAILURE"
    RESPONSE_FAILED = "RESPONSE_FAILED"
    RESPONSE_INCOMPLETE = "RESPONSE_INCOMPLETE"
    RESPONSE_STATUS_INVALID = "RESPONSE_STATUS_INVALID"
    REFUSAL = "REFUSAL"
    OUTPUT_INVALID = "OUTPUT_INVALID"
    WIRE_VALIDATION_FAILED = "WIRE_VALIDATION_FAILED"
    RESULT_VALIDATION_FAILED = "RESULT_VALIDATION_FAILED"


@dataclass(frozen=True, slots=True)
class OpenAIDecisionDiagnostic:
    category: OpenAIDecisionDiagnosticCategory
    exception_class: str | None = None
    http_status: int | None = None
    openai_error_code: str | None = None
    parameter: str | None = None
    request_id: str | None = None
    response_status: str | None = None
    incomplete_reason: str | None = None

    def __post_init__(self) -> None:
        valid = (
            type(self.category) is OpenAIDecisionDiagnosticCategory
            and (
                self.exception_class is None
                or _safe_exception_class(self.exception_class) == self.exception_class
            )
            and (
                self.http_status is None or _safe_http_status(self.http_status) == self.http_status
            )
            and (
                self.openai_error_code is None
                or _safe_error_code(self.openai_error_code) == self.openai_error_code
            )
            and (self.parameter is None or _safe_parameter(self.parameter) == self.parameter)
            and (self.request_id is None or _safe_request_id(self.request_id) == self.request_id)
            and (
                self.response_status is None
                or _safe_response_status(self.response_status) == self.response_status
            )
            and (
                self.incomplete_reason is None
                or _safe_incomplete_reason(self.incomplete_reason) == self.incomplete_reason
            )
        )
        if not valid:
            raise TypeError("OpenAI decision diagnostic is invalid.")


class OpenAIDecisionError(Exception):
    """Base controlled exception for the OpenAI decision boundary."""

    def __init__(
        self,
        message: str,
        *,
        diagnostic: OpenAIDecisionDiagnostic | None = None,
    ) -> None:
        if diagnostic is not None and type(diagnostic) is not OpenAIDecisionDiagnostic:
            raise TypeError("OpenAI decision diagnostic must use the exact safe type.")
        self._diagnostic = diagnostic
        super().__init__(message)

    @property
    def diagnostic(self) -> OpenAIDecisionDiagnostic | None:
        return self._diagnostic


class OpenAIDecisionConfigurationError(OpenAIDecisionError):
    def __init__(self, *, diagnostic: OpenAIDecisionDiagnostic | None = None) -> None:
        super().__init__("OpenAI decision configuration is invalid.", diagnostic=diagnostic)


class OpenAIDecisionAuthenticationError(OpenAIDecisionError):
    def __init__(self, *, diagnostic: OpenAIDecisionDiagnostic | None = None) -> None:
        super().__init__("OpenAI authentication failed.", diagnostic=diagnostic)


class OpenAIDecisionRateLimitError(OpenAIDecisionError):
    def __init__(self, *, diagnostic: OpenAIDecisionDiagnostic | None = None) -> None:
        super().__init__("OpenAI rate limit exceeded.", diagnostic=diagnostic)


class OpenAIDecisionRequestError(OpenAIDecisionError):
    def __init__(self, *, diagnostic: OpenAIDecisionDiagnostic | None = None) -> None:
        super().__init__("OpenAI decision request failed.", diagnostic=diagnostic)


class OpenAIDecisionIncompleteError(OpenAIDecisionError):
    def __init__(self, *, diagnostic: OpenAIDecisionDiagnostic | None = None) -> None:
        super().__init__("OpenAI decision did not complete.", diagnostic=diagnostic)


class OpenAIDecisionRefusalError(OpenAIDecisionError):
    def __init__(self, *, diagnostic: OpenAIDecisionDiagnostic | None = None) -> None:
        super().__init__("OpenAI decision was refused.", diagnostic=diagnostic)


class OpenAIDecisionResponseError(OpenAIDecisionError):
    def __init__(self, *, diagnostic: OpenAIDecisionDiagnostic | None = None) -> None:
        super().__init__("OpenAI decision response was invalid.", diagnostic=diagnostic)
