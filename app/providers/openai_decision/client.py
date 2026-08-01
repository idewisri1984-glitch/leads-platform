import json
from types import TracebackType

from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    OpenAI,
    PermissionDeniedError,
    RateLimitError,
)
from pydantic import ValidationError

from app.providers.openai_decision.exceptions import (
    OpenAIDecisionAuthenticationError,
    OpenAIDecisionConfigurationError,
    OpenAIDecisionError,
    OpenAIDecisionIncompleteError,
    OpenAIDecisionRateLimitError,
    OpenAIDecisionRefusalError,
    OpenAIDecisionRequestError,
    OpenAIDecisionResponseError,
)
from app.providers.openai_decision.prompt import OPENAI_COMPANY_DECISION_INSTRUCTIONS
from app.providers.openai_decision.schemas import (
    OpenAICompanyFit,
    OpenAIDecisionKind,
    OpenAIDecisionRequest,
    OpenAIDecisionResult,
    _OpenAIDecisionWireResult,
)

_MAX_INPUT_BYTES = 20_000
_MAX_OUTPUT_BYTES = 10_000
_SCHEMA_NAME = "company_candidate_decision"
_SCHEMA_DESCRIPTION = (
    "Select at most one supplied Company candidate and recommend one human-reviewed next action."
)
_INCOMPLETE_STATUSES = frozenset({"incomplete", "in_progress", "queued", "cancelled"})


def _serialize_request(request: OpenAIDecisionRequest) -> str:
    serialized = json.dumps(
        request.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(serialized.encode("utf-8")) > _MAX_INPUT_BYTES:
        raise OpenAIDecisionConfigurationError()
    return serialized


def _contains_refusal(response: object) -> bool:
    output = getattr(response, "output", None)
    if type(output) is not list:
        raise OpenAIDecisionResponseError()
    for item in output:
        content = getattr(item, "content", None)
        if content is None:
            continue
        if type(content) is not list:
            raise OpenAIDecisionResponseError()
        if any(getattr(part, "type", None) == "refusal" for part in content):
            return True
    return False


class OpenAIDecisionClient:
    def __init__(
        self,
        *,
        api_key: str | None,
        model: str | None,
        timeout_seconds: float = 30.0,
        max_output_tokens: int = 600,
        openai_client: OpenAI | None = None,
    ) -> None:
        if (
            type(api_key) is not str
            or not api_key
            or api_key != api_key.strip()
            or len(api_key) > 512
            or type(model) is not str
            or not model
            or model != model.strip()
            or len(model) > 200
            or type(timeout_seconds) not in {int, float}
            or not 0 < timeout_seconds <= 120
            or type(max_output_tokens) is not int
            or not 100 <= max_output_tokens <= 2000
        ):
            raise OpenAIDecisionConfigurationError()

        self._model = model
        self._max_output_tokens = max_output_tokens
        self._owns_client = openai_client is None
        self._closed = False
        self._client = (
            openai_client
            if openai_client is not None
            else OpenAI(
                api_key=api_key,
                timeout=timeout_seconds,
                max_retries=0,
            )
        )

    def decide(self, request: OpenAIDecisionRequest) -> OpenAIDecisionResult:
        if self._closed or type(request) is not OpenAIDecisionRequest:
            raise OpenAIDecisionConfigurationError()

        serialized = _serialize_request(request)
        translated: OpenAIDecisionError | None = None
        response: object | None = None
        try:
            response = self._client.responses.create(
                model=self._model,
                instructions=OPENAI_COMPANY_DECISION_INSTRUCTIONS,
                input=serialized,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": _SCHEMA_NAME,
                        "description": _SCHEMA_DESCRIPTION,
                        "schema": _OpenAIDecisionWireResult.model_json_schema(),
                        "strict": True,
                    }
                },
                max_output_tokens=self._max_output_tokens,
                store=False,
                truncation="disabled",
            )
        except (AuthenticationError, PermissionDeniedError):
            translated = OpenAIDecisionAuthenticationError()
        except RateLimitError:
            translated = OpenAIDecisionRateLimitError()
        except (APIConnectionError, APITimeoutError, APIStatusError, APIError, Exception):
            translated = OpenAIDecisionRequestError()
        if translated is not None:
            raise translated from None
        if response is None:
            raise OpenAIDecisionResponseError()

        status = getattr(response, "status", None)
        if status == "failed":
            raise OpenAIDecisionRequestError()
        if status in _INCOMPLETE_STATUSES:
            raise OpenAIDecisionIncompleteError()
        if status != "completed":
            raise OpenAIDecisionResponseError()
        if _contains_refusal(response):
            raise OpenAIDecisionRefusalError()

        output_text = getattr(response, "output_text", None)
        if (
            type(output_text) is not str
            or not output_text.strip()
            or len(output_text.encode("utf-8")) > _MAX_OUTPUT_BYTES
        ):
            raise OpenAIDecisionResponseError()

        wire: _OpenAIDecisionWireResult | None = None
        parsing_error = False
        try:
            wire = _OpenAIDecisionWireResult.model_validate_json(output_text)
        except (ValidationError, ValueError, TypeError):
            parsing_error = True
        if parsing_error or wire is None:
            raise OpenAIDecisionResponseError() from None

        candidate_indices = {candidate.index for candidate in request.candidates}
        if (
            wire.selected_candidate_index is not None
            and wire.selected_candidate_index not in candidate_indices
        ):
            raise OpenAIDecisionResponseError()

        result: OpenAIDecisionResult | None = None
        result_error = False
        try:
            result = OpenAIDecisionResult(
                decision=OpenAIDecisionKind(wire.decision),
                selected_candidate_index=wire.selected_candidate_index,
                confidence=wire.confidence,
                company_fit=OpenAICompanyFit(wire.company_fit),
                rationale=wire.rationale,
                next_action_title=wire.next_action_title,
                next_action_description=wire.next_action_description,
                human_review_required=wire.human_review_required,
            )
        except (ValidationError, ValueError, TypeError):
            result_error = True
        if result_error or result is None:
            raise OpenAIDecisionResponseError() from None
        return result

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "OpenAIDecisionClient":
        if self._closed:
            raise OpenAIDecisionConfigurationError()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        self.close()
