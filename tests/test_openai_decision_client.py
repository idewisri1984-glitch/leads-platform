import inspect
import json
import traceback
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

import httpx
import pytest
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

from app.providers.openai_decision import (
    OpenAICompanyFit,
    OpenAIDecisionAuthenticationError,
    OpenAIDecisionCandidate,
    OpenAIDecisionClient,
    OpenAIDecisionConfigurationError,
    OpenAIDecisionIncompleteError,
    OpenAIDecisionKind,
    OpenAIDecisionRateLimitError,
    OpenAIDecisionRefusalError,
    OpenAIDecisionRequest,
    OpenAIDecisionRequestError,
    OpenAIDecisionResponseError,
    OpenAIDecisionResult,
)
from app.providers.openai_decision.exceptions import (
    OpenAIDecisionDiagnostic,
    OpenAIDecisionDiagnosticCategory,
)
from app.providers.openai_decision.prompt import OPENAI_COMPANY_DECISION_INSTRUCTIONS

SELECT = {
    "decision": "SELECT",
    "selected_candidate_index": 1,
    "confidence": 0.8,
    "company_fit": "HIGH",
    "rationale": "Strong fit.",
    "next_action_title": "Review",
    "next_action_description": "Confirm the candidate.",
    "human_review_required": True,
}
NO_SELECTION = {
    "decision": "NO_SELECTION",
    "selected_candidate_index": None,
    "confidence": 0.2,
    "company_fit": "NOT_SUITABLE",
    "rationale": "Insufficient evidence.",
    "next_action_title": None,
    "next_action_description": None,
    "human_review_required": True,
}


def request(count: int = 1) -> OpenAIDecisionRequest:
    return OpenAIDecisionRequest(
        goal="Find a fit",
        candidates=tuple(
            OpenAIDecisionCandidate(
                index=i,
                name=f"Company {i}",
                website=None,
                country=None,
                city=None,
                industry=None,
                snippet=None,
                website_summary=None,
            )
            for i in range(1, count + 1)
        ),
    )


def response(
    payload: dict[str, object] = SELECT,
    *,
    status: str = "completed",
    refusal: bool = False,
    incomplete_reason: str | None = None,
    request_id: object = None,
) -> SimpleNamespace:
    content = (
        [SimpleNamespace(type="refusal", refusal="secret refusal")]
        if refusal
        else [SimpleNamespace(type="output_text")]
    )
    return SimpleNamespace(
        status=status,
        output=[SimpleNamespace(content=content)],
        output_text=json.dumps(payload),
        incomplete_details=(
            SimpleNamespace(reason=incomplete_reason) if incomplete_reason is not None else None
        ),
        request_id=request_id,
    )


class FakeResponses:
    def __init__(self, result: object = None, error: BaseException | None = None) -> None:
        self.result = result or response()
        self.error = error
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


class FakeSDK:
    def __init__(self, result: object = None, error: BaseException | None = None) -> None:
        self.responses = FakeResponses(result, error)
        self.close_calls = 0
        self.close_error: BaseException | None = None

    def close(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


class SequencedResponses:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        outcome = self.outcomes[len(self.calls) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class SequencedSDK(FakeSDK):
    def __init__(self, outcomes: list[object]) -> None:
        super().__init__()
        self.responses = SequencedResponses(outcomes)


class HostileSDKObject:
    def __init__(
        self,
        *,
        values: dict[str, object],
        hostile_name: str,
        failure: BaseException,
    ) -> None:
        object.__setattr__(self, "_values", values)
        object.__setattr__(self, "_hostile_name", hostile_name)
        object.__setattr__(self, "_failure", failure)

    def __getattribute__(self, name: str) -> object:
        if name in {"_values", "_hostile_name", "_failure"}:
            return object.__getattribute__(self, name)
        if name == object.__getattribute__(self, "_hostile_name"):
            raise object.__getattribute__(self, "_failure")
        values = cast(dict[str, object], object.__getattribute__(self, "_values"))
        if name in values:
            return values[name]
        raise AttributeError(name)


def client(fake: FakeSDK | None = None, **changes: object) -> tuple[OpenAIDecisionClient, FakeSDK]:
    sdk = fake if fake is not None else FakeSDK()
    values: dict[str, object] = {
        "api_key": "test-key",
        "model": "test-model",
        "openai_client": cast(OpenAI, sdk),
        "sleeper": lambda _: None,
    }
    return OpenAIDecisionClient(**(values | changes)), sdk  # type: ignore[arg-type]


def test_public_signatures_are_bounded() -> None:
    assert tuple(inspect.signature(OpenAIDecisionClient).parameters) == (
        "api_key",
        "model",
        "timeout_seconds",
        "max_output_tokens",
        "openai_client",
        "sleeper",
    )
    assert tuple(inspect.signature(OpenAIDecisionClient.decide).parameters) == ("self", "request")


@pytest.mark.parametrize(
    "field,value",
    [
        ("api_key", None),
        ("api_key", ""),
        ("api_key", " key"),
        ("api_key", "x" * 513),
        ("api_key", True),
        ("model", None),
        ("model", ""),
        ("model", "model "),
        ("model", "x" * 201),
        ("model", True),
        ("timeout_seconds", True),
        ("timeout_seconds", 0),
        ("timeout_seconds", -1),
        ("timeout_seconds", 120.1),
        ("timeout_seconds", "30"),
        ("max_output_tokens", True),
        ("max_output_tokens", 99),
        ("max_output_tokens", 2001),
        ("max_output_tokens", 600.0),
        ("max_output_tokens", "600"),
    ],
)
def test_configuration_is_validated_before_client_use(field: str, value: object) -> None:
    fake = FakeSDK()
    with pytest.raises(
        OpenAIDecisionConfigurationError, match=r"^OpenAI decision configuration is invalid\.$"
    ):
        client(fake, **{field: value})
    assert fake.responses.calls == []


def test_decide_requires_exact_request_type() -> None:
    wrapper, fake = client()

    class RequestSubclass(OpenAIDecisionRequest):
        pass

    for value in (
        {},
        [],
        "{}",
        object(),
        RequestSubclass(goal="Goal", candidates=request().candidates),
    ):
        with pytest.raises(OpenAIDecisionConfigurationError):
            wrapper.decide(value)  # type: ignore[arg-type]
    assert fake.responses.calls == []


@pytest.mark.parametrize(
    "payload,kind,fit",
    [
        (SELECT, OpenAIDecisionKind.SELECT, OpenAICompanyFit.HIGH),
        (NO_SELECTION, OpenAIDecisionKind.NO_SELECTION, OpenAICompanyFit.NOT_SUITABLE),
    ],
)
def test_exact_single_request_and_safe_public_result(
    payload: dict[str, object], kind: OpenAIDecisionKind, fit: OpenAICompanyFit
) -> None:
    wrapper, fake = client(FakeSDK(response(payload)))
    result = wrapper.decide(request())
    assert type(result) is OpenAIDecisionResult
    assert result.decision is kind and result.company_fit is fit
    assert len(fake.responses.calls) == 1
    call = fake.responses.calls[0]
    assert set(call) == {
        "model",
        "instructions",
        "input",
        "text",
        "max_output_tokens",
        "store",
        "truncation",
    }
    assert call["model"] == "test-model"
    assert call["instructions"] == OPENAI_COMPANY_DECISION_INSTRUCTIONS
    assert json.loads(cast(str, call["input"])) == request().model_dump(mode="json")
    assert call["max_output_tokens"] == 600 and call["store"] is False
    assert call["truncation"] == "disabled"
    format_ = cast(dict[str, Any], cast(dict[str, Any], call["text"])["format"])
    assert format_["type"] == "json_schema" and format_["name"] == "company_candidate_decision"
    assert format_["strict"] is True and format_["description"]
    assert format_["schema"]["additionalProperties"] is False
    assert set(format_["schema"]["required"]) == set(format_["schema"]["properties"])


def test_first_attempt_success_does_not_sleep() -> None:
    sleeper_calls: list[float] = []
    wrapper, fake = client(sleeper=sleeper_calls.append)

    assert wrapper.decide(request()).decision is OpenAIDecisionKind.SELECT
    assert len(fake.responses.calls) == 1
    assert sleeper_calls == []


@pytest.mark.parametrize("error_type", [APIConnectionError, APITimeoutError, RateLimitError])
def test_transient_request_failure_retries_once_then_succeeds(
    error_type: type[BaseException],
) -> None:
    sleeper_calls: list[float] = []
    sdk = SequencedSDK([sdk_error(error_type, 429), response()])
    wrapper, _ = client(cast(FakeSDK, sdk), sleeper=sleeper_calls.append)

    result = wrapper.decide(request())

    assert result.decision is OpenAIDecisionKind.SELECT
    assert len(sdk.responses.calls) == 2
    assert sleeper_calls == [0.5]


@pytest.mark.parametrize("error_type", [APIConnectionError, APITimeoutError, RateLimitError])
def test_transient_request_failure_twice_raises_final_controlled_error(
    error_type: type[BaseException],
) -> None:
    sleeper_calls: list[float] = []
    first = sdk_error(error_type, 429)
    final = sdk_error(error_type, 429)
    sdk = SequencedSDK([first, final])
    wrapper, _ = client(cast(FakeSDK, sdk), sleeper=sleeper_calls.append)

    with pytest.raises((OpenAIDecisionRequestError, OpenAIDecisionRateLimitError)) as raised:
        wrapper.decide(request())

    assert raised.value.diagnostic is not None
    assert raised.value.diagnostic.exception_class == type(final).__name__
    assert len(sdk.responses.calls) == 2
    assert sleeper_calls == [0.5]


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_transient_server_status_retries_once(status: int) -> None:
    sleeper_calls: list[float] = []
    sdk = SequencedSDK([sdk_error(APIStatusError, status), response()])
    wrapper, _ = client(cast(FakeSDK, sdk), sleeper=sleeper_calls.append)

    assert wrapper.decide(request()).decision is OpenAIDecisionKind.SELECT
    assert len(sdk.responses.calls) == 2
    assert sleeper_calls == [0.5]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422, 501, 505, 507, 508, 511])
def test_nontransient_api_status_does_not_retry(status: int) -> None:
    sleeper_calls: list[float] = []
    sdk = SequencedSDK([sdk_error(APIStatusError, status)])
    wrapper, _ = client(cast(FakeSDK, sdk), sleeper=sleeper_calls.append)

    with pytest.raises(OpenAIDecisionRequestError):
        wrapper.decide(request())

    assert len(sdk.responses.calls) == 1
    assert sleeper_calls == []


def test_reentrant_sleeper_cannot_create_a_third_provider_call() -> None:
    sleeper_calls: list[float] = []
    sdk = SequencedSDK(
        [sdk_error(APIConnectionError), response(), AssertionError("third provider call")]
    )
    wrapper: OpenAIDecisionClient | None = None

    def reentrant_sleeper(delay: float) -> None:
        sleeper_calls.append(delay)
        assert wrapper is not None
        assert wrapper.decide(request()).decision is OpenAIDecisionKind.SELECT

    wrapper, _ = client(cast(FakeSDK, sdk), sleeper=reentrant_sleeper)

    with pytest.raises(OpenAIDecisionRequestError) as raised:
        wrapper.decide(request())

    assert raised.value.diagnostic is not None
    assert raised.value.diagnostic.category is OpenAIDecisionDiagnosticCategory.CONNECTION
    assert len(sdk.responses.calls) == 2
    assert sleeper_calls == [0.5]


def test_retry_budget_resets_after_reentrant_outer_flow() -> None:
    sdk = SequencedSDK([sdk_error(APIConnectionError), response(), response()])
    wrapper: OpenAIDecisionClient | None = None

    def reentrant_sleeper(_: float) -> None:
        assert wrapper is not None
        wrapper.decide(request())

    wrapper, _ = client(cast(FakeSDK, sdk), sleeper=reentrant_sleeper)
    with pytest.raises(OpenAIDecisionRequestError):
        wrapper.decide(request())

    assert wrapper.decide(request()).decision is OpenAIDecisionKind.SELECT
    assert len(sdk.responses.calls) == 3


def test_reentrant_cross_instance_flow_shares_only_the_active_budget() -> None:
    first_sdk = SequencedSDK([sdk_error(APIConnectionError)])
    second_sdk = SequencedSDK([response(), response()])
    second, _ = client(cast(FakeSDK, second_sdk))

    def reentrant_sleeper(_: float) -> None:
        assert second.decide(request()).decision is OpenAIDecisionKind.SELECT

    first, _ = client(cast(FakeSDK, first_sdk), sleeper=reentrant_sleeper)

    with pytest.raises(OpenAIDecisionRequestError):
        first.decide(request())
    assert len(first_sdk.responses.calls) == len(second_sdk.responses.calls) == 1

    assert second.decide(request()).decision is OpenAIDecisionKind.SELECT
    assert len(second_sdk.responses.calls) == 2


def test_ordinary_sleeper_failure_is_controlled_without_second_provider_call() -> None:
    source = RuntimeError("raw sleeper secret")
    sdk = SequencedSDK([sdk_error(APIConnectionError)])

    def failing_sleeper(_: float) -> None:
        raise source

    wrapper, _ = client(cast(FakeSDK, sdk), sleeper=failing_sleeper)
    with pytest.raises(OpenAIDecisionRequestError) as raised:
        wrapper.decide(request())

    assert raised.value.diagnostic is not None
    assert (
        raised.value.diagnostic.category
        is OpenAIDecisionDiagnosticCategory.INTERNAL_REQUEST_FAILURE
    )
    assert raised.value.__cause__ is raised.value.__context__ is None
    assert "raw sleeper secret" not in str(raised.value)
    assert len(sdk.responses.calls) == 1


@pytest.mark.parametrize("source", [KeyboardInterrupt(), SystemExit(), GeneratorExit()])
def test_sleeper_base_exceptions_propagate_without_second_provider_call(
    source: BaseException,
) -> None:
    sdk = SequencedSDK([sdk_error(APIConnectionError)])

    def failing_sleeper(_: float) -> None:
        raise source

    wrapper, _ = client(cast(FakeSDK, sdk), sleeper=failing_sleeper)
    with pytest.raises(BaseException) as raised:
        wrapper.decide(request())

    assert raised.value is source
    assert len(sdk.responses.calls) == 1


@pytest.mark.parametrize(
    "first_type,first_status,final_type,final_status,target,category,http_status",
    [
        (
            APIConnectionError,
            500,
            AuthenticationError,
            401,
            OpenAIDecisionAuthenticationError,
            OpenAIDecisionDiagnosticCategory.AUTHENTICATION,
            401,
        ),
        (
            APITimeoutError,
            500,
            APIStatusError,
            400,
            OpenAIDecisionRequestError,
            OpenAIDecisionDiagnosticCategory.API_STATUS,
            400,
        ),
        (
            APIStatusError,
            503,
            APIConnectionError,
            500,
            OpenAIDecisionRequestError,
            OpenAIDecisionDiagnosticCategory.CONNECTION,
            None,
        ),
        (
            RateLimitError,
            429,
            PermissionDeniedError,
            403,
            OpenAIDecisionAuthenticationError,
            OpenAIDecisionDiagnosticCategory.AUTHENTICATION,
            403,
        ),
    ],
)
def test_mixed_final_failure_uses_only_second_attempt_diagnostic(
    first_type: type[BaseException],
    first_status: int,
    final_type: type[BaseException],
    final_status: int,
    target: type[Exception],
    category: OpenAIDecisionDiagnosticCategory,
    http_status: int | None,
) -> None:
    sleeper_calls: list[float] = []
    first = sdk_error(first_type, first_status)
    final = sdk_error(final_type, final_status)
    sdk = SequencedSDK([first, final])
    wrapper, _ = client(cast(FakeSDK, sdk), sleeper=sleeper_calls.append)

    with pytest.raises(target) as raised:
        wrapper.decide(request())

    assert raised.value.diagnostic is not None
    assert raised.value.diagnostic.category is category
    assert raised.value.diagnostic.exception_class == type(final).__name__
    assert raised.value.diagnostic.http_status == http_status
    assert raised.value.__cause__ is raised.value.__context__ is None
    rendered = "".join(traceback.format_exception(raised.value))
    assert "raw secret key candidate" not in rendered
    assert "example.test" not in rendered
    assert len(sdk.responses.calls) == 2
    assert sleeper_calls == [0.5]


def test_transient_then_response_validation_failure_has_no_stale_request_error() -> None:
    sleeper_calls: list[float] = []
    invalid = response()
    invalid.output_text = "not json"
    sdk = SequencedSDK([sdk_error(APIConnectionError), invalid])
    wrapper, _ = client(cast(FakeSDK, sdk), sleeper=sleeper_calls.append)

    with pytest.raises(OpenAIDecisionResponseError) as raised:
        wrapper.decide(request())

    assert raised.value.diagnostic is not None
    assert (
        raised.value.diagnostic.category is OpenAIDecisionDiagnosticCategory.WIRE_VALIDATION_FAILED
    )
    assert raised.value.__cause__ is raised.value.__context__ is None
    assert "example.test" not in "".join(traceback.format_exception(raised.value))
    assert len(sdk.responses.calls) == 2
    assert sleeper_calls == [0.5]


@pytest.mark.parametrize("status", ["incomplete", "in_progress", "queued", "cancelled"])
def test_noncompleted_statuses_are_mapped_before_parsing(status: str) -> None:
    wrapper, _ = client(FakeSDK(response(status=status)))
    with pytest.raises(OpenAIDecisionIncompleteError) as raised:
        wrapper.decide(request())
    assert raised.value.diagnostic == OpenAIDecisionDiagnostic(
        category=OpenAIDecisionDiagnosticCategory.RESPONSE_INCOMPLETE,
        response_status=status,
    )


def test_incomplete_reason_is_preserved_only_as_a_safe_token() -> None:
    wrapper, _ = client(
        FakeSDK(response(status="incomplete", incomplete_reason="max_output_tokens"))
    )
    with pytest.raises(OpenAIDecisionIncompleteError) as raised:
        wrapper.decide(request())
    assert raised.value.diagnostic is not None
    assert raised.value.diagnostic.incomplete_reason == "max_output_tokens"


def test_failed_unknown_and_refusal_statuses_are_sanitized() -> None:
    cases = [
        (
            response(status="failed"),
            OpenAIDecisionRequestError,
            OpenAIDecisionDiagnosticCategory.RESPONSE_FAILED,
        ),
        (
            response(status="unknown"),
            OpenAIDecisionResponseError,
            OpenAIDecisionDiagnosticCategory.RESPONSE_STATUS_INVALID,
        ),
        (
            response(refusal=True),
            OpenAIDecisionRefusalError,
            OpenAIDecisionDiagnosticCategory.REFUSAL,
        ),
    ]
    for sdk_response, error, category in cases:
        wrapper, _ = client(FakeSDK(sdk_response))
        with pytest.raises(error) as raised:
            wrapper.decide(request())
        assert "secret" not in str(raised.value)
        assert raised.value.diagnostic.category is category


@pytest.mark.parametrize(
    "output,category",
    [
        ("", OpenAIDecisionDiagnosticCategory.OUTPUT_INVALID),
        (" ", OpenAIDecisionDiagnosticCategory.OUTPUT_INVALID),
        ("not json", OpenAIDecisionDiagnosticCategory.WIRE_VALIDATION_FAILED),
        ("```json\n{}\n```", OpenAIDecisionDiagnosticCategory.WIRE_VALIDATION_FAILED),
        ("x" * 10001, OpenAIDecisionDiagnosticCategory.OUTPUT_INVALID),
    ],
)
def test_invalid_output_is_never_repaired(
    output: str, category: OpenAIDecisionDiagnosticCategory
) -> None:
    sdk_response = response()
    sdk_response.output_text = output
    wrapper, fake = client(FakeSDK(sdk_response))
    with pytest.raises(OpenAIDecisionResponseError) as raised:
        wrapper.decide(request())
    assert raised.value.__cause__ is None and raised.value.__context__ is None
    assert raised.value.diagnostic.category is category
    assert len(fake.responses.calls) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"human_review_required": False},
        {"confidence": 2.0},
        {"decision": "SELECT", "selected_candidate_index": None},
        {"extra": "x"},
        {"rationale": ""},
    ],
)
def test_schema_invalid_or_inconsistent_json_is_rejected(changes: dict[str, object]) -> None:
    wrapper, _ = client(FakeSDK(response(SELECT | changes)))
    with pytest.raises(OpenAIDecisionResponseError) as raised:
        wrapper.decide(request())
    assert (
        raised.value.diagnostic.category is OpenAIDecisionDiagnosticCategory.WIRE_VALIDATION_FAILED
    )


def test_selected_index_must_exist_in_submitted_request() -> None:
    wrapper, _ = client(FakeSDK(response(SELECT | {"selected_candidate_index": 2})))
    with pytest.raises(OpenAIDecisionResponseError) as raised:
        wrapper.decide(request())
    assert (
        raised.value.diagnostic.category
        is OpenAIDecisionDiagnosticCategory.RESULT_VALIDATION_FAILED
    )


def test_integer_confidence_is_rejected_without_retry_or_context() -> None:
    wrapper, fake = client(FakeSDK(response(SELECT | {"confidence": 1})))

    with pytest.raises(
        OpenAIDecisionResponseError, match=r"^OpenAI decision response was invalid\.$"
    ) as raised:
        wrapper.decide(request())

    assert len(fake.responses.calls) == 1
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert "confidence" not in str(raised.value)


def sdk_error(error_type: type[BaseException], status: int = 500) -> BaseException:
    req = httpx.Request("POST", "https://example.test/v1/responses")
    if error_type in {APIConnectionError, APITimeoutError}:
        return error_type(request=req)  # type: ignore[call-arg]
    resp = httpx.Response(status, request=req)
    return error_type("raw secret key candidate", response=resp, body={})  # type: ignore[call-arg]


def api_status_error(
    *,
    code: str = "server_error",
    parameter: str = "text.format",
    request_id: str = "req_safe-123",
) -> APIStatusError:
    req = httpx.Request("POST", "https://example.test/v1/responses")
    resp = httpx.Response(
        503,
        request=req,
        headers={"x-request-id": request_id},
    )
    return APIStatusError(
        "raw secret key candidate",
        response=resp,
        body={"code": code, "param": parameter},
    )


def generic_api_error() -> APIError:
    req = httpx.Request("POST", "https://example.test/v1/responses")
    return APIError(
        "raw secret key candidate",
        request=req,
        body={"code": "provider_error", "param": "input"},
    )


@pytest.mark.parametrize(
    "source,target,category",
    [
        (
            sdk_error(AuthenticationError, 401),
            OpenAIDecisionAuthenticationError,
            OpenAIDecisionDiagnosticCategory.AUTHENTICATION,
        ),
        (
            sdk_error(PermissionDeniedError, 403),
            OpenAIDecisionAuthenticationError,
            OpenAIDecisionDiagnosticCategory.AUTHENTICATION,
        ),
        (
            sdk_error(RateLimitError, 429),
            OpenAIDecisionRateLimitError,
            OpenAIDecisionDiagnosticCategory.RATE_LIMIT,
        ),
        (
            sdk_error(APIConnectionError),
            OpenAIDecisionRequestError,
            OpenAIDecisionDiagnosticCategory.CONNECTION,
        ),
        (
            sdk_error(APITimeoutError),
            OpenAIDecisionRequestError,
            OpenAIDecisionDiagnosticCategory.TIMEOUT,
        ),
        (
            api_status_error(),
            OpenAIDecisionRequestError,
            OpenAIDecisionDiagnosticCategory.API_STATUS,
        ),
        (
            generic_api_error(),
            OpenAIDecisionRequestError,
            OpenAIDecisionDiagnosticCategory.API_ERROR,
        ),
        (
            RuntimeError("raw secret"),
            OpenAIDecisionRequestError,
            OpenAIDecisionDiagnosticCategory.INTERNAL_REQUEST_FAILURE,
        ),
    ],
)
def test_sdk_errors_are_sanitized_without_context(
    source: BaseException,
    target: type[Exception],
    category: OpenAIDecisionDiagnosticCategory,
) -> None:
    wrapper, fake = client(FakeSDK(error=source))
    with pytest.raises(target) as raised:
        wrapper.decide(request())
    assert str(raised.value) in {
        "OpenAI authentication failed.",
        "OpenAI rate limit exceeded.",
        "OpenAI decision request failed.",
    }
    assert "secret" not in str(raised.value)
    assert raised.value.diagnostic is not None
    assert raised.value.diagnostic.category is category
    assert raised.value.diagnostic.exception_class == type(source).__name__
    assert raised.value.__cause__ is None and raised.value.__context__ is None
    expected_calls = (
        2
        if category
        in {
            OpenAIDecisionDiagnosticCategory.RATE_LIMIT,
            OpenAIDecisionDiagnosticCategory.CONNECTION,
            OpenAIDecisionDiagnosticCategory.TIMEOUT,
            OpenAIDecisionDiagnosticCategory.API_STATUS,
        }
        else 1
    )
    assert len(fake.responses.calls) == expected_calls


def test_api_status_preserves_only_allowlisted_structured_metadata() -> None:
    wrapper, _ = client(FakeSDK(error=api_status_error()))
    with pytest.raises(OpenAIDecisionRequestError) as raised:
        wrapper.decide(request())
    assert raised.value.diagnostic == OpenAIDecisionDiagnostic(
        category=OpenAIDecisionDiagnosticCategory.API_STATUS,
        exception_class="APIStatusError",
        http_status=503,
        openai_error_code="server_error",
        parameter="text.format",
        request_id="req_safe-123",
    )


def test_unsafe_structured_sdk_metadata_is_discarded() -> None:
    markers = (
        "sk-SECRET_API_KEY",
        "PRIVATE_CANDIDATE_MARKER",
        "Bearer_PRIVATE_AUTHORIZATION",
    )
    wrapper, _ = client(
        FakeSDK(
            error=api_status_error(
                code=markers[0],
                parameter=markers[1],
                request_id=markers[2],
            )
        )
    )
    with pytest.raises(OpenAIDecisionRequestError) as raised:
        wrapper.decide(request())
    assert raised.value.diagnostic is not None
    assert raised.value.diagnostic.openai_error_code is None
    assert raised.value.diagnostic.parameter is None
    assert raised.value.diagnostic.request_id is None
    assert all(marker not in repr(raised.value.diagnostic) for marker in markers)


@pytest.mark.parametrize(
    "unsafe",
    [
        "sk-proj-AbCdEf0123456789",
        "sk-AbCdEf0123456789",
        "Bearer abcdef123456",
        "AUTHORIZATION_BEARER_SECRET",
        "https://private.example/path",
        "line1\nline2",
        "nul\x00value",
        "x" * 200,
    ],
)
def test_field_specific_sdk_metadata_sanitizers_reject_unsafe_values(unsafe: str) -> None:
    error = api_status_error(code=unsafe, parameter=unsafe, request_id="req_safe-123")
    wrapper, _ = client(FakeSDK(error=error))
    with pytest.raises(OpenAIDecisionRequestError) as raised:
        wrapper.decide(request())
    assert raised.value.diagnostic is not None
    assert raised.value.diagnostic.openai_error_code is None
    assert raised.value.diagnostic.parameter is None
    assert unsafe not in repr(raised.value.diagnostic)


@pytest.mark.parametrize(
    "unsafe",
    [
        "sk-proj-AbCdEf0123456789",
        "sk-AbCdEf0123456789",
        "Bearer-abcdef123456",
        "https://private.example/path",
        "req_" + "x" * 100,
    ],
)
def test_request_id_sanitizer_rejects_credentials_urls_and_overlength(
    unsafe: str,
) -> None:
    sdk_response = response(status="incomplete", request_id=unsafe)
    wrapper, _ = client(FakeSDK(sdk_response))
    with pytest.raises(OpenAIDecisionIncompleteError) as raised:
        wrapper.decide(request())
    assert raised.value.diagnostic is not None
    assert raised.value.diagnostic.request_id is None


@pytest.mark.parametrize(
    "unsafe",
    [
        "https://private.example/path",
        "sk-proj-AbCdEf0123456789",
        "unknown_reason",
        "line1\nline2",
        "x" * 200,
    ],
)
def test_incomplete_reason_uses_an_explicit_allowlist(unsafe: str) -> None:
    wrapper, _ = client(FakeSDK(response(status="incomplete", incomplete_reason=unsafe)))
    with pytest.raises(OpenAIDecisionIncompleteError) as raised:
        wrapper.decide(request())
    assert raised.value.diagnostic is not None
    assert raised.value.diagnostic.incomplete_reason is None


def test_diagnostic_is_self_validating_frozen_and_slotted() -> None:
    diagnostic = OpenAIDecisionDiagnostic(
        category=OpenAIDecisionDiagnosticCategory.API_STATUS,
        request_id="req_safe-123",
    )
    assert not hasattr(diagnostic, "__dict__")
    with pytest.raises(FrozenInstanceError):
        diagnostic.request_id = "req_changed"  # type: ignore[misc]
    with pytest.raises(TypeError, match=r"^OpenAI decision diagnostic is invalid\.$"):
        OpenAIDecisionDiagnostic(
            category=OpenAIDecisionDiagnosticCategory.API_STATUS,
            openai_error_code="sk-proj-AbCdEf0123456789",
        )


@pytest.mark.parametrize("kind", ["runtime", "sdk", "response"])
def test_exception_constructor_never_retains_raw_diagnostic_objects(kind: str) -> None:
    marker = "RAW_DIAGNOSTIC_OBJECT_MARKER"
    raw: object
    if kind == "runtime":
        raw = RuntimeError(marker)
    elif kind == "sdk":
        raw = sdk_error(APIConnectionError)
    else:
        raw = SimpleNamespace(output_text=marker)
    with pytest.raises(
        TypeError, match=r"^OpenAI decision diagnostic must use the exact safe type\.$"
    ) as raised:
        OpenAIDecisionRequestError(diagnostic=raw)  # type: ignore[arg-type]
    exposed = "\n".join(
        (str(raised.value), repr(raised.value), "".join(traceback.format_exception(raised.value)))
    )
    assert marker not in exposed


def test_exception_diagnostic_is_keyword_only() -> None:
    raw = RuntimeError("RAW_POSITIONAL_OBJECT_MARKER")
    with pytest.raises(TypeError) as raised:
        OpenAIDecisionRequestError(raw)  # type: ignore[call-arg]
    assert "RAW_POSITIONAL_OBJECT_MARKER" not in str(raised.value)


def test_public_result_validation_failure_has_safe_diagnostic() -> None:
    wrapper, _ = client()
    with (
        patch(
            "app.providers.openai_decision.client.OpenAIDecisionResult",
            side_effect=ValueError("PRIVATE_RESULT_MARKER"),
        ),
        pytest.raises(OpenAIDecisionResponseError) as raised,
    ):
        wrapper.decide(request())
    assert (
        raised.value.diagnostic.category
        is OpenAIDecisionDiagnosticCategory.RESULT_VALIDATION_FAILED
    )
    assert "PRIVATE_RESULT_MARKER" not in str(raised.value)
    assert "PRIVATE_RESULT_MARKER" not in repr(raised.value.diagnostic)


def test_hostile_raw_context_cannot_reach_any_error_representation() -> None:
    markers = (
        "sk-SECRET_API_KEY",
        "PRIVATE_GOAL_MARKER",
        "PRIVATE_CANDIDATE_MARKER",
        "PRIVATE_SNIPPET_MARKER",
        "PRIVATE_SERIALIZED_INPUT",
        "PRIVATE_RESPONSE_BODY",
        "Bearer_PRIVATE_AUTHORIZATION",
        "RAW_SDK_MESSAGE_MARKER",
    )
    source = RuntimeError(" ".join(markers))
    hostile_request = OpenAIDecisionRequest(
        goal=markers[1],
        candidates=(
            OpenAIDecisionCandidate(
                index=1,
                name=markers[2],
                website=None,
                country=None,
                city=None,
                industry=None,
                snippet=markers[3],
                website_summary=markers[4],
            ),
        ),
    )
    wrapper, _ = client(FakeSDK(error=source))
    with pytest.raises(OpenAIDecisionRequestError) as raised:
        wrapper.decide(hostile_request)
    exposed = "\n".join(
        (
            str(raised.value),
            repr(raised.value),
            repr(raised.value.diagnostic),
            "".join(traceback.format_exception(raised.value)),
        )
    )
    assert all(marker not in exposed for marker in markers)


def _safe_completed_values() -> dict[str, object]:
    return {
        "status": "completed",
        "output": [SimpleNamespace(content=[SimpleNamespace(type="output_text")])],
        "output_text": json.dumps(SELECT),
        "incomplete_details": None,
        "request_id": "req_safe-123",
    }


@pytest.mark.parametrize(
    "target",
    ["status", "output", "content", "type", "output_text"],
)
def test_hostile_response_shape_properties_become_controlled_errors(target: str) -> None:
    marker = f"HOSTILE_{target.upper()}_SECRET"
    failure = RuntimeError(marker)
    values = _safe_completed_values()
    if target == "content":
        values["output"] = [HostileSDKObject(values={}, hostile_name="content", failure=failure)]
    elif target == "type":
        values["output"] = [
            SimpleNamespace(
                content=[HostileSDKObject(values={}, hostile_name="type", failure=failure)]
            )
        ]
    sdk_response = HostileSDKObject(values=values, hostile_name=target, failure=failure)
    wrapper, _ = client(FakeSDK(sdk_response))
    with pytest.raises(OpenAIDecisionResponseError) as raised:
        wrapper.decide(request())
    exposed = "\n".join(
        (
            str(raised.value),
            repr(raised.value),
            repr(raised.value.diagnostic),
            "".join(traceback.format_exception(raised.value)),
        )
    )
    assert marker not in exposed
    assert raised.value.__cause__ is None and raised.value.__context__ is None


@pytest.mark.parametrize("target", ["incomplete_details", "reason"])
def test_hostile_incomplete_detail_properties_remain_controlled(target: str) -> None:
    marker = f"HOSTILE_{target.upper()}_SECRET"
    failure = RuntimeError(marker)
    values = _safe_completed_values() | {"status": "incomplete"}
    if target == "reason":
        values["incomplete_details"] = HostileSDKObject(
            values={}, hostile_name="reason", failure=failure
        )
        sdk_response: object = SimpleNamespace(**values)
    else:
        sdk_response = HostileSDKObject(
            values=values, hostile_name="incomplete_details", failure=failure
        )
    wrapper, _ = client(FakeSDK(sdk_response))
    with pytest.raises(OpenAIDecisionIncompleteError) as raised:
        wrapper.decide(request())
    exposed = "\n".join(
        (
            str(raised.value),
            repr(raised.value),
            repr(raised.value.diagnostic),
            "".join(traceback.format_exception(raised.value)),
        )
    )
    assert marker not in exposed
    assert raised.value.diagnostic is not None
    assert raised.value.diagnostic.incomplete_reason is None


@pytest.mark.parametrize("failure", [KeyboardInterrupt(), SystemExit()])
def test_hostile_response_base_exceptions_propagate_by_identity(
    failure: BaseException,
) -> None:
    sdk_response = HostileSDKObject(
        values=_safe_completed_values(), hostile_name="status", failure=failure
    )
    wrapper, _ = client(FakeSDK(sdk_response))
    with pytest.raises(BaseException) as raised:
        wrapper.decide(request())
    assert raised.value is failure


class InfrastructureFailure(BaseException):
    pass


class FalseyFakeSDK(FakeSDK):
    def __bool__(self) -> bool:
        return False


class TruthinessGuardFakeSDK(FakeSDK):
    def __bool__(self) -> bool:
        raise AssertionError("Injected client truthiness must not be evaluated.")


@pytest.mark.parametrize(
    "source", [KeyboardInterrupt(), SystemExit(), GeneratorExit(), InfrastructureFailure()]
)
def test_base_exceptions_propagate_by_identity(source: BaseException) -> None:
    sleeper_calls: list[float] = []
    wrapper, fake = client(FakeSDK(error=source), sleeper=sleeper_calls.append)
    with pytest.raises(BaseException) as raised:
        wrapper.decide(request())
    assert raised.value is source and len(fake.responses.calls) == 1
    assert sleeper_calls == []


def test_client_ownership_close_and_context_manager() -> None:
    injected = FakeSDK()
    wrapper, _ = client(injected)
    wrapper.close()
    wrapper.close()
    assert injected.close_calls == 0
    with pytest.raises(OpenAIDecisionConfigurationError):
        wrapper.decide(request())
    owned = FakeSDK()
    wrapper, _ = client(owned)
    wrapper._owns_client = True
    with wrapper as entered:
        assert entered is wrapper
    wrapper.close()
    assert owned.close_calls == 1


def test_falsy_injected_client_is_preserved_used_and_never_closed() -> None:
    injected = FalseyFakeSDK()
    with patch("app.providers.openai_decision.client.OpenAI") as constructor:
        wrapper, _ = client(injected)
        with wrapper as entered:
            result = entered.decide(request())
        wrapper.close()

    assert result.decision is OpenAIDecisionKind.SELECT
    assert wrapper._client is injected
    assert wrapper._owns_client is False
    assert len(injected.responses.calls) == 1
    assert injected.close_calls == 0
    constructor.assert_not_called()


def test_injected_client_truthiness_is_never_evaluated() -> None:
    injected = TruthinessGuardFakeSDK()
    with patch("app.providers.openai_decision.client.OpenAI") as constructor:
        wrapper, _ = client(injected)
        assert wrapper.decide(request()).decision is OpenAIDecisionKind.SELECT
        wrapper.close()

    assert wrapper._client is injected
    assert injected.close_calls == 0
    constructor.assert_not_called()


def test_absent_injected_client_constructs_and_closes_one_owned_client() -> None:
    owned = FakeSDK()
    with patch(
        "app.providers.openai_decision.client.OpenAI", return_value=cast(OpenAI, owned)
    ) as constructor:
        wrapper = OpenAIDecisionClient(api_key="test-key", model="test-model")
        wrapper.close()
        wrapper.close()

    constructor.assert_called_once_with(api_key="test-key", timeout=30.0, max_retries=0)
    assert wrapper._client is owned
    assert wrapper._owns_client is True
    assert owned.close_calls == 1


@pytest.mark.parametrize("failure", [RuntimeError("close failed"), InfrastructureFailure()])
def test_close_failures_propagate_unchanged_and_close_is_at_most_once(
    failure: BaseException,
) -> None:
    owned = FakeSDK()
    owned.close_error = failure
    wrapper, _ = client(owned)
    wrapper._owns_client = True
    with pytest.raises(BaseException) as raised:
        wrapper.close()
    assert raised.value is failure
    wrapper.close()
    assert owned.close_calls == 1


def test_no_selection_wire_recommendations_are_normalized_without_retry() -> None:
    rationale = (
        "All provided candidates appear to be listicles or directories rather than a "
        "single identifiable firm."
    )
    observed = NO_SELECTION | {
        "confidence": 0.18,
        "rationale": rationale,
        "next_action_title": "Identify a specific US interior design firm",
        "next_action_description": (
            "Have a human verify a concrete firm and its official website."
        ),
    }
    wrapper, fake = client(FakeSDK(response(observed)))

    result = wrapper.decide(request())

    assert result.decision is OpenAIDecisionKind.NO_SELECTION
    assert result.selected_candidate_index is None
    assert result.company_fit is OpenAICompanyFit.NOT_SUITABLE
    assert result.next_action_title is None
    assert result.next_action_description is None
    assert result.confidence == 0.18
    assert result.rationale == rationale
    assert result.human_review_required is True
    assert len(fake.responses.calls) == 1
