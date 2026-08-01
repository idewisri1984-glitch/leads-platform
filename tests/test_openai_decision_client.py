import inspect
import json
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

import httpx
import pytest
from openai import (
    APIConnectionError,
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
    payload: dict[str, object] = SELECT, *, status: str = "completed", refusal: bool = False
) -> SimpleNamespace:
    content = (
        [SimpleNamespace(type="refusal", refusal="secret refusal")]
        if refusal
        else [SimpleNamespace(type="output_text")]
    )
    return SimpleNamespace(
        status=status, output=[SimpleNamespace(content=content)], output_text=json.dumps(payload)
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


def client(fake: FakeSDK | None = None, **changes: object) -> tuple[OpenAIDecisionClient, FakeSDK]:
    sdk = fake if fake is not None else FakeSDK()
    values: dict[str, object] = {
        "api_key": "test-key",
        "model": "test-model",
        "openai_client": cast(OpenAI, sdk),
    }
    return OpenAIDecisionClient(**(values | changes)), sdk  # type: ignore[arg-type]


def test_public_signatures_are_bounded() -> None:
    assert tuple(inspect.signature(OpenAIDecisionClient).parameters) == (
        "api_key",
        "model",
        "timeout_seconds",
        "max_output_tokens",
        "openai_client",
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


@pytest.mark.parametrize("status", ["incomplete", "in_progress", "queued", "cancelled"])
def test_noncompleted_statuses_are_mapped_before_parsing(status: str) -> None:
    wrapper, _ = client(FakeSDK(response(status=status)))
    with pytest.raises(OpenAIDecisionIncompleteError):
        wrapper.decide(request())


def test_failed_unknown_and_refusal_statuses_are_sanitized() -> None:
    cases = [
        (response(status="failed"), OpenAIDecisionRequestError),
        (response(status="unknown"), OpenAIDecisionResponseError),
        (response(refusal=True), OpenAIDecisionRefusalError),
    ]
    for sdk_response, error in cases:
        wrapper, _ = client(FakeSDK(sdk_response))
        with pytest.raises(error) as raised:
            wrapper.decide(request())
        assert "secret" not in str(raised.value)


@pytest.mark.parametrize("output", ["", " ", "not json", "```json\n{}\n```", "x" * 10001])
def test_invalid_output_is_never_repaired(output: str) -> None:
    sdk_response = response()
    sdk_response.output_text = output
    wrapper, fake = client(FakeSDK(sdk_response))
    with pytest.raises(OpenAIDecisionResponseError) as raised:
        wrapper.decide(request())
    assert raised.value.__cause__ is None and raised.value.__context__ is None
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
    with pytest.raises(OpenAIDecisionResponseError):
        wrapper.decide(request())


def test_selected_index_must_exist_in_submitted_request() -> None:
    wrapper, _ = client(FakeSDK(response(SELECT | {"selected_candidate_index": 2})))
    with pytest.raises(OpenAIDecisionResponseError):
        wrapper.decide(request())


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


@pytest.mark.parametrize(
    "source,target",
    [
        (sdk_error(AuthenticationError, 401), OpenAIDecisionAuthenticationError),
        (sdk_error(PermissionDeniedError, 403), OpenAIDecisionAuthenticationError),
        (sdk_error(RateLimitError, 429), OpenAIDecisionRateLimitError),
        (sdk_error(APIConnectionError), OpenAIDecisionRequestError),
        (sdk_error(APITimeoutError), OpenAIDecisionRequestError),
        (RuntimeError("raw secret"), OpenAIDecisionRequestError),
    ],
)
def test_sdk_errors_are_sanitized_without_context(
    source: BaseException, target: type[Exception]
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
    assert raised.value.__cause__ is None and raised.value.__context__ is None
    assert len(fake.responses.calls) == 1


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
    wrapper, fake = client(FakeSDK(error=source))
    with pytest.raises(BaseException) as raised:
        wrapper.decide(request())
    assert raised.value is source and len(fake.responses.calls) == 1


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
