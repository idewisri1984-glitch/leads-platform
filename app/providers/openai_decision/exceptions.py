from dataclasses import dataclass
from enum import StrEnum


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


class OpenAIDecisionError(Exception):
    """Base controlled exception for the OpenAI decision boundary."""

    def __init__(self, message: str, diagnostic: OpenAIDecisionDiagnostic | None = None) -> None:
        self._diagnostic = diagnostic
        super().__init__(message)

    @property
    def diagnostic(self) -> OpenAIDecisionDiagnostic | None:
        return self._diagnostic


class OpenAIDecisionConfigurationError(OpenAIDecisionError):
    def __init__(self, diagnostic: OpenAIDecisionDiagnostic | None = None) -> None:
        super().__init__("OpenAI decision configuration is invalid.", diagnostic)


class OpenAIDecisionAuthenticationError(OpenAIDecisionError):
    def __init__(self, diagnostic: OpenAIDecisionDiagnostic | None = None) -> None:
        super().__init__("OpenAI authentication failed.", diagnostic)


class OpenAIDecisionRateLimitError(OpenAIDecisionError):
    def __init__(self, diagnostic: OpenAIDecisionDiagnostic | None = None) -> None:
        super().__init__("OpenAI rate limit exceeded.", diagnostic)


class OpenAIDecisionRequestError(OpenAIDecisionError):
    def __init__(self, diagnostic: OpenAIDecisionDiagnostic | None = None) -> None:
        super().__init__("OpenAI decision request failed.", diagnostic)


class OpenAIDecisionIncompleteError(OpenAIDecisionError):
    def __init__(self, diagnostic: OpenAIDecisionDiagnostic | None = None) -> None:
        super().__init__("OpenAI decision did not complete.", diagnostic)


class OpenAIDecisionRefusalError(OpenAIDecisionError):
    def __init__(self, diagnostic: OpenAIDecisionDiagnostic | None = None) -> None:
        super().__init__("OpenAI decision was refused.", diagnostic)


class OpenAIDecisionResponseError(OpenAIDecisionError):
    def __init__(self, diagnostic: OpenAIDecisionDiagnostic | None = None) -> None:
        super().__init__("OpenAI decision response was invalid.", diagnostic)
