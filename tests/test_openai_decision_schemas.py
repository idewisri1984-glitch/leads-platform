from decimal import Decimal

import pytest
from pydantic import ValidationError

import app.providers.openai_decision as package
from app.providers.openai_decision import (
    OpenAICompanyFit,
    OpenAIDecisionCandidate,
    OpenAIDecisionKind,
    OpenAIDecisionRequest,
    OpenAIDecisionResult,
)
from app.providers.openai_decision.schemas import _OpenAIDecisionWireResult


def candidate(index: int = 1, **changes: object) -> OpenAIDecisionCandidate:
    values: dict[str, object] = {
        "index": index,
        "name": "Bali Software",
        "website": "https://example.com",
        "country": "Indonesia",
        "city": "Denpasar",
        "industry": "Software",
        "snippet": "Public search result",
        "website_summary": "Public website summary",
    }
    return OpenAIDecisionCandidate(**(values | changes))


def select_result(**changes: object) -> OpenAIDecisionResult:
    values: dict[str, object] = {
        "decision": OpenAIDecisionKind.SELECT,
        "selected_candidate_index": 1,
        "confidence": 0.8,
        "company_fit": OpenAICompanyFit.HIGH,
        "rationale": "Strong public evidence.",
        "next_action_title": "Review candidate",
        "next_action_description": "Confirm before CRM processing.",
        "human_review_required": True,
    }
    return OpenAIDecisionResult(**(values | changes))


def no_selection_result(**changes: object) -> OpenAIDecisionResult:
    values: dict[str, object] = {
        "decision": OpenAIDecisionKind.NO_SELECTION,
        "selected_candidate_index": None,
        "confidence": 0.2,
        "company_fit": OpenAICompanyFit.NOT_SUITABLE,
        "rationale": "Evidence is insufficient.",
        "next_action_title": None,
        "next_action_description": None,
        "human_review_required": True,
    }
    return OpenAIDecisionResult(**(values | changes))


def wire_result(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "decision": "SELECT",
        "selected_candidate_index": 1,
        "confidence": 0.5,
        "company_fit": "HIGH",
        "rationale": "Strong public evidence.",
        "next_action_title": "Review candidate",
        "next_action_description": "Confirm before CRM processing.",
        "human_review_required": True,
    }
    return values | changes


def test_exact_public_exports_and_enums() -> None:
    assert package.__all__ == [
        "OpenAIDecisionKind",
        "OpenAICompanyFit",
        "OpenAIDecisionCandidate",
        "OpenAIDecisionRequest",
        "OpenAIDecisionResult",
        "OpenAIDecisionError",
        "OpenAIDecisionConfigurationError",
        "OpenAIDecisionAuthenticationError",
        "OpenAIDecisionRateLimitError",
        "OpenAIDecisionRequestError",
        "OpenAIDecisionIncompleteError",
        "OpenAIDecisionRefusalError",
        "OpenAIDecisionResponseError",
        "OpenAIDecisionClient",
    ]
    assert [(item.name, item.value) for item in OpenAIDecisionKind] == [
        ("SELECT", "SELECT"),
        ("NO_SELECTION", "NO_SELECTION"),
    ]
    assert [item.value for item in OpenAICompanyFit] == ["HIGH", "MEDIUM", "LOW", "NOT_SUITABLE"]
    assert not hasattr(package, "_OpenAIDecisionWireResult")


def test_candidate_request_and_result_are_frozen_and_forbid_extra() -> None:
    item = candidate()
    request = OpenAIDecisionRequest(goal="Find a fit", candidates=(item,))
    result = select_result()
    for model in (item, request, result):
        with pytest.raises(ValidationError):
            model.model_copy(update={"extra": "x"}).__class__.model_validate(
                model.model_dump() | {"extra": "x"}
            )
        with pytest.raises(ValidationError):
            model.__setattr__(next(iter(type(model).model_fields)), "changed")
    assert isinstance(request.candidates, tuple)


class IntSubclass(int):
    pass


class StringSubclass(str):
    pass


@pytest.mark.parametrize("value", [True, False, 0, 6, 1.0, "1", IntSubclass(1)])
def test_candidate_rejects_non_exact_or_out_of_range_index(value: object) -> None:
    with pytest.raises(ValidationError):
        candidate(index=value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "maximum"),
    [
        ("name", 200),
        ("website", 2048),
        ("country", 100),
        ("city", 100),
        ("industry", 150),
        ("snippet", 1200),
        ("website_summary", 2000),
    ],
)
def test_candidate_string_boundaries(field: str, maximum: int) -> None:
    assert getattr(candidate(**{field: "x" * maximum}), field) == "x" * maximum
    for value in ("", "   ", StringSubclass("x"), "x" * (maximum + 1), 1, True):
        with pytest.raises(ValidationError):
            candidate(**{field: value})
    if field != "name":
        assert getattr(candidate(**{field: None}), field) is None


def test_candidate_contains_exact_minimized_fields_without_pii() -> None:
    assert tuple(OpenAIDecisionCandidate.model_fields) == (
        "index",
        "name",
        "website",
        "country",
        "city",
        "industry",
        "snippet",
        "website_summary",
    )
    forbidden = {"email", "phone", "contact_name", "notes", "raw_html", "api_key"}
    assert forbidden.isdisjoint(OpenAIDecisionCandidate.model_fields)


@pytest.mark.parametrize("value", [[], iter((candidate(),)), {candidate()}])
def test_request_rejects_non_exact_tuple(value: object) -> None:
    with pytest.raises(ValidationError):
        OpenAIDecisionRequest(goal="Goal", candidates=value)  # type: ignore[arg-type]


def test_request_accepts_exact_tuple() -> None:
    items = (candidate(),)
    candidates = OpenAIDecisionRequest(goal="Goal", candidates=items).candidates

    assert type(candidates) is tuple
    assert candidates == items


class TupleSubclass(tuple[OpenAIDecisionCandidate, ...]):
    pass


def test_request_requires_one_to_five_exact_candidates() -> None:
    for count in (1, 5):
        items = tuple(candidate(index) for index in range(1, count + 1))
        assert len(OpenAIDecisionRequest(goal="Goal", candidates=items).candidates) == count
    six_items = tuple(candidate(min(index, 5)) for index in range(1, 7))
    for items in ((), six_items):
        with pytest.raises(ValidationError):
            OpenAIDecisionRequest(goal="Goal", candidates=items)
    with pytest.raises(ValidationError):
        OpenAIDecisionRequest(goal="Goal", candidates=TupleSubclass((candidate(),)))
    with pytest.raises(ValidationError):
        OpenAIDecisionRequest.model_validate({"goal": "Goal", "candidates": ({"index": 1},)})


@pytest.mark.parametrize("indices", [(1, 1), (1, 3), (2, 1), (2,)])
def test_request_rejects_duplicate_noncontiguous_or_wrong_order(indices: tuple[int, ...]) -> None:
    with pytest.raises(ValidationError):
        OpenAIDecisionRequest(goal="Goal", candidates=tuple(candidate(i) for i in indices))


@pytest.mark.parametrize("goal", ["", " ", "x" * 1001, StringSubclass("goal"), 1])
def test_request_rejects_invalid_goal(goal: object) -> None:
    with pytest.raises(ValidationError):
        OpenAIDecisionRequest(goal=goal, candidates=(candidate(),))  # type: ignore[arg-type]


def test_public_result_accepts_only_actual_enums_and_exact_confidence() -> None:
    for field, value in (
        ("decision", "SELECT"),
        ("company_fit", "HIGH"),
        ("confidence", 1),
        ("confidence", True),
        ("confidence", -0.1),
        ("confidence", 1.1),
    ):
        with pytest.raises(ValidationError):
            select_result(**{field: value})
        with pytest.raises(ValidationError):
            OpenAIDecisionResult.model_validate(select_result().model_dump() | {field: value})


@pytest.mark.parametrize(
    "changes",
    [
        {"selected_candidate_index": None},
        {"selected_candidate_index": 6},
        {"company_fit": OpenAICompanyFit.NOT_SUITABLE},
        {"next_action_title": None},
        {"next_action_description": None},
        {"human_review_required": False},
        {"human_review_required": 1},
        {"rationale": ""},
        {"rationale": "x" * 501},
        {"next_action_title": "x" * 256},
        {"next_action_description": "x" * 1001},
    ],
)
def test_select_invariants(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        select_result(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"selected_candidate_index": 1},
        {"company_fit": OpenAICompanyFit.LOW},
        {"next_action_title": "Review"},
        {"next_action_description": "Review it"},
        {"human_review_required": False},
    ],
)
def test_no_selection_invariants(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        no_selection_result(**changes)


class FloatSubclass(float):
    pass


@pytest.mark.parametrize(
    "value",
    [1, 0, True, IntSubclass(1), FloatSubclass(0.5), "0.5", Decimal("0.5"), None, object()],
)
def test_wire_confidence_requires_exact_float_during_direct_construction(value: object) -> None:
    with pytest.raises(ValidationError):
        _OpenAIDecisionWireResult(**wire_result(confidence=value))


@pytest.mark.parametrize("value", [0.0, 0.5, 1.0])
def test_wire_confidence_accepts_builtin_float_during_direct_construction(value: float) -> None:
    assert _OpenAIDecisionWireResult(**wire_result(confidence=value)).confidence == value


@pytest.mark.parametrize("value", [0, 1, True, FloatSubclass(0.5), "0.5", Decimal("0.5")])
def test_wire_confidence_requires_exact_float_during_model_validation(value: object) -> None:
    with pytest.raises(ValidationError):
        _OpenAIDecisionWireResult.model_validate(wire_result(confidence=value))


@pytest.mark.parametrize("value", [0.0, 0.25, 1.0])
def test_wire_confidence_accepts_builtin_float_during_model_validation(value: float) -> None:
    assert (
        _OpenAIDecisionWireResult.model_validate(wire_result(confidence=value)).confidence == value
    )


def test_frozen_models_reject_assignment_and_preserve_original_values() -> None:
    item = candidate()
    request = OpenAIDecisionRequest(goal="Find a fit", candidates=(item,))
    result = select_result()

    for model, field, replacement in (
        (item, "name", "Changed"),
        (request, "candidates", (candidate(), candidate(2))),
        (result, "confidence", 0.1),
    ):
        original = getattr(model, field)
        with pytest.raises(ValidationError):
            setattr(model, field, replacement)
        assert getattr(model, field) == original


def test_wire_no_selection_accepts_schema_valid_recommendation_fields() -> None:
    result = _OpenAIDecisionWireResult.model_validate(
        {
            "decision": "NO_SELECTION",
            "selected_candidate_index": None,
            "confidence": 0.18,
            "company_fit": "NOT_SUITABLE",
            "rationale": "All supplied candidates are directories.",
            "next_action_title": "Identify a specific firm",
            "next_action_description": "Have a human verify its official website.",
            "human_review_required": True,
        }
    )

    assert result.decision == "NO_SELECTION"
    assert result.next_action_title == "Identify a specific firm"
    assert result.next_action_description == "Have a human verify its official website."


@pytest.mark.parametrize(
    "changes",
    [
        {"selected_candidate_index": 1},
        {"company_fit": "LOW"},
        {"human_review_required": False},
        {"rationale": " "},
        {"unexpected": "field"},
    ],
)
def test_wire_no_selection_preserves_all_other_strict_validation(
    changes: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "decision": "NO_SELECTION",
        "selected_candidate_index": None,
        "confidence": 0.18,
        "company_fit": "NOT_SUITABLE",
        "rationale": "Evidence is insufficient.",
        "next_action_title": "Identify a specific firm",
        "next_action_description": "Have a human verify its official website.",
        "human_review_required": True,
    }

    with pytest.raises(ValidationError):
        _OpenAIDecisionWireResult.model_validate(values | changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"selected_candidate_index": None},
        {"company_fit": "NOT_SUITABLE"},
        {"next_action_title": None},
        {"next_action_description": None},
    ],
)
def test_wire_select_invariants_remain_strict(changes: dict[str, object]) -> None:
    values: dict[str, object] = {
        "decision": "SELECT",
        "selected_candidate_index": 1,
        "confidence": 0.8,
        "company_fit": "HIGH",
        "rationale": "Strong public evidence.",
        "next_action_title": "Review candidate",
        "next_action_description": "Confirm before CRM processing.",
        "human_review_required": True,
    }

    with pytest.raises(ValidationError):
        _OpenAIDecisionWireResult.model_validate(values | changes)
