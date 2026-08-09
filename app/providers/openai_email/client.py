from types import TracebackType
from typing import Protocol, cast

from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    OpenAI,
    RateLimitError,
)
from pydantic import BaseModel, ConfigDict, ValidationError

from app.modules.email_draft.provider_interfaces import (
    EmailDraftProviderAuthenticationError,
    EmailDraftProviderConfigurationError,
    EmailDraftProviderRateLimitError,
    EmailDraftProviderRefusalError,
    EmailDraftProviderResponseError,
    EmailDraftProviderTimeoutError,
    EmailDraftProviderUnavailableError,
)
from app.modules.email_draft.schemas import (
    EmailDraftGenerationResult,
    EmailDraftProviderRequest,
    EmailLanguage,
)

from .prompt import EMAIL_DRAFT_SYSTEM_INSTRUCTIONS


class _Responses(Protocol):
    def create(self, **kwargs: object) -> object: ...


class _Client(Protocol):
    responses: _Responses

    def close(self) -> None: ...


class _WireResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    subject: str
    text_body: str
    language: str


class OpenAIEmailDraftGenerator:
    def __init__(
        self,
        *,
        api_key: str | None,
        model: str | None,
        timeout_seconds: float,
        max_output_tokens: int,
        client: _Client | None = None,
    ) -> None:
        if (
            type(api_key) is not str
            or not api_key.strip()
            or type(model) is not str
            or not model.strip()
            or type(timeout_seconds) is not float
            or not 0 < timeout_seconds <= 120
            or type(max_output_tokens) is not int
            or not 100 <= max_output_tokens <= 2000
        ):
            raise EmailDraftProviderConfigurationError(
                "Email draft provider configuration is invalid."
            )
        self.model = model
        self.max_output_tokens = max_output_tokens
        self._client = client or cast(_Client, OpenAI(api_key=api_key, timeout=timeout_seconds))
        self._owns_client = client is None
        self._closed = False

    def generate(self, request: EmailDraftProviderRequest) -> EmailDraftGenerationResult:
        if self._closed or type(request) is not EmailDraftProviderRequest:
            raise EmailDraftProviderConfigurationError(
                "Email draft provider configuration is invalid."
            )
        try:
            response = self._client.responses.create(
                model=self.model,
                instructions=EMAIL_DRAFT_SYSTEM_INSTRUCTIONS,
                input="UNTRUSTED_REFERENCE_DATA_JSON:\n" + request.model_dump_json(),
                max_output_tokens=self.max_output_tokens,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "email_draft_generation",
                        "strict": True,
                        "schema": _WireResult.model_json_schema(),
                    }
                },
                tools=[],
            )
        except AuthenticationError:
            raise EmailDraftProviderAuthenticationError(
                "Email draft provider authentication failed."
            ) from None
        except RateLimitError:
            raise EmailDraftProviderRateLimitError(
                "Email draft provider rate limit exceeded."
            ) from None
        except APITimeoutError:
            raise EmailDraftProviderTimeoutError("Email draft provider timed out.") from None
        except APIConnectionError:
            raise EmailDraftProviderUnavailableError(
                "Email draft provider is unavailable."
            ) from None
        except Exception:
            raise EmailDraftProviderUnavailableError(
                "Email draft provider is unavailable."
            ) from None
        if getattr(response, "status", None) != "completed":
            raise EmailDraftProviderResponseError("Email draft provider response is invalid.")
        if self._contains_refusal(response):
            raise EmailDraftProviderRefusalError("Email draft generation was refused.")
        output_text = getattr(response, "output_text", None)
        if type(output_text) is not str or not output_text.strip() or len(output_text) > 20000:
            raise EmailDraftProviderResponseError("Email draft provider response is invalid.")
        try:
            wire = _WireResult.model_validate_json(output_text)
            return EmailDraftGenerationResult(
                subject=wire.subject,
                text_body=wire.text_body,
                language=EmailLanguage(wire.language),
                provider="openai",
                model=self.model,
                prompt_version=request.prompt_version,
            )
        except (ValidationError, ValueError, TypeError):
            raise EmailDraftProviderResponseError(
                "Email draft provider response is invalid."
            ) from None

    @staticmethod
    def _contains_refusal(response: object) -> bool:
        output = getattr(response, "output", None)
        if type(output) is not list:
            return False
        for item in output:
            content = getattr(item, "content", None)
            if type(content) is list and any(
                getattr(part, "type", None) == "refusal" for part in content
            ):
                return True
        return False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "OpenAIEmailDraftGenerator":
        if self._closed:
            raise EmailDraftProviderConfigurationError(
                "Email draft provider configuration is invalid."
            )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        self.close()
