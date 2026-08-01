import json
from collections.abc import Callable

import httpx
import pytest
from openai import OpenAI

from app.providers.openai_decision import (
    OpenAIDecisionAuthenticationError,
    OpenAIDecisionCandidate,
    OpenAIDecisionClient,
    OpenAIDecisionIncompleteError,
    OpenAIDecisionKind,
    OpenAIDecisionRateLimitError,
    OpenAIDecisionRefusalError,
    OpenAIDecisionRequest,
    OpenAIDecisionRequestError,
    OpenAIDecisionResponseError,
)
from app.providers.openai_decision.prompt import OPENAI_COMPANY_DECISION_INSTRUCTIONS

KEY = "integration-secret-key"
MODEL = "integration-model"
SELECT = {
    "decision": "SELECT",
    "selected_candidate_index": 1,
    "confidence": 0.8,
    "company_fit": "HIGH",
    "rationale": "Strong fit.",
    "next_action_title": "Review",
    "next_action_description": "Confirm candidate.",
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


def decision_request() -> OpenAIDecisionRequest:
    return OpenAIDecisionRequest(
        goal="Find a fit",
        candidates=(
            OpenAIDecisionCandidate(
                index=1,
                name="Public Company",
                website="https://example.com",
                country=None,
                city=None,
                industry=None,
                snippet=None,
                website_summary=None,
            ),
        ),
    )


def response_payload(
    output: dict[str, object] = SELECT, *, status: str = "completed", refusal: bool = False
) -> dict[str, object]:
    content: list[dict[str, object]] = (
        [{"type": "refusal", "refusal": "private refusal"}]
        if refusal
        else [{"type": "output_text", "text": json.dumps(output), "annotations": []}]
    )
    return {
        "id": "resp_test",
        "object": "response",
        "created_at": 1.0,
        "model": MODEL,
        "output": [
            {
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": content,
            }
        ],
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "status": status,
    }


def wrapper(handler: Callable[[httpx.Request], httpx.Response]) -> OpenAIDecisionClient:
    sdk = OpenAI(
        api_key=KEY,
        base_url="https://mock.openai.test/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_retries=0,
    )
    return OpenAIDecisionClient(api_key=KEY, model=MODEL, openai_client=sdk)


@pytest.mark.parametrize(
    "payload,kind",
    [(SELECT, OpenAIDecisionKind.SELECT), (NO_SELECTION, OpenAIDecisionKind.NO_SELECTION)],
)
def test_real_sdk_sends_one_strict_responses_request(
    payload: dict[str, object], kind: OpenAIDecisionKind
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content)
        assert request.method == "POST" and request.url.path == "/v1/responses"
        assert request.headers["authorization"] == f"Bearer {KEY}"
        assert (
            body["model"] == MODEL and body["instructions"] == OPENAI_COMPANY_DECISION_INSTRUCTIONS
        )
        assert json.loads(body["input"]) == decision_request().model_dump(mode="json")
        assert body["store"] is False and body["max_output_tokens"] == 600
        assert body["truncation"] == "disabled" and "tools" not in body
        assert body["text"]["format"]["type"] == "json_schema"
        assert body["text"]["format"]["strict"] is True
        return httpx.Response(200, json=response_payload(payload), request=request)

    result = wrapper(handler).decide(decision_request())
    assert result.decision is kind and len(requests) == 1


@pytest.mark.parametrize(
    "status,error",
    [
        (401, OpenAIDecisionAuthenticationError),
        (403, OpenAIDecisionAuthenticationError),
        (429, OpenAIDecisionRateLimitError),
        (400, OpenAIDecisionRequestError),
        (404, OpenAIDecisionRequestError),
        (500, OpenAIDecisionRequestError),
    ],
)
def test_real_sdk_http_errors_are_mapped(status: int, error: type[Exception]) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            status,
            json={
                "error": {
                    "message": "raw secret",
                    "type": "test_error",
                    "param": None,
                    "code": "test",
                }
            },
            request=request,
        )

    with pytest.raises(error) as raised:
        wrapper(handler).decide(decision_request())
    assert calls == 1 and KEY not in str(raised.value) and "raw secret" not in str(raised.value)


def test_real_sdk_transport_timeout_has_zero_retries() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("raw timeout", request=request)

    with pytest.raises(OpenAIDecisionRequestError):
        wrapper(handler).decide(decision_request())
    assert calls == 1


@pytest.mark.parametrize(
    "payload,error",
    [
        (response_payload(status="incomplete"), OpenAIDecisionIncompleteError),
        (response_payload(refusal=True), OpenAIDecisionRefusalError),
        (response_payload(SELECT | {"selected_candidate_index": 2}), OpenAIDecisionResponseError),
    ],
)
def test_real_sdk_status_refusal_and_malformed_output(
    payload: dict[str, object], error: type[Exception]
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=payload, request=request)

    with pytest.raises(error):
        wrapper(handler).decide(decision_request())
    assert calls == 1
