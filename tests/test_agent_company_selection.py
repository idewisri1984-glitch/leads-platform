import ast
import inspect
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

import app.modules.agent as agent_package
from app.modules.agent import (
    AgentCompanySelectionBinding,
    AgentCompanySelectionConsistencyError,
    AgentCompanySelectionError,
    AgentCompanySelectionInput,
    AgentCompanySelectionInvalidDataError,
    AgentCompanySelectionNoCandidatesError,
    AgentCompanySelectionRunNotFoundError,
    AgentCompanySelectionRunNotReadyError,
    AgentCompanySelectionService,
)
from app.modules.agent.company_selection import AgentCompanySelectionRepository
from app.modules.company_discovery.models import (
    CompanyDiscoveryCandidateStatus,
    CompanyDiscoveryRunStatus,
)
from app.providers.openai_decision import (
    OpenAICompanyFit,
    OpenAIDecisionCandidate,
    OpenAIDecisionKind,
    OpenAIDecisionRequest,
    OpenAIDecisionResult,
)

INVALID = "Agent company selection data is invalid."
NOT_FOUND = "Discovery run was not found."
NOT_READY = "Discovery run is not ready for agent selection."
NO_CANDIDATES = "No eligible discovery candidates were found."
INCONSISTENT = "Agent company selection state is inconsistent."


@dataclass
class RunRecord:
    id: object = 7
    project_id: object = 3
    run_status: object = CompanyDiscoveryRunStatus.SUCCEEDED


@dataclass
class CandidateRecord:
    id: object = 11
    project_id: object = 3
    last_seen_run_id: object = 7
    name: object = "Alpha Company"
    website: object = "https://alpha.example"
    country_code: object = "US"
    identity_key: object = "website:alpha.example"
    best_position: object = 1
    candidate_status: object = CompanyDiscoveryCandidateStatus.DISCOVERED
    promoted_company_id: object = None


_DEFAULT = object()


class RepositorySpy:
    def __init__(self, run: object = _DEFAULT, candidates: object = _DEFAULT) -> None:
        self.run = RunRecord() if run is _DEFAULT else run
        self.candidates = [CandidateRecord()] if candidates is _DEFAULT else candidates
        self.calls: list[tuple[object, ...]] = []

    def get_run(self, run_id: int) -> object:
        self.calls.append(("get_run", run_id))
        return self.run

    def list_candidates_for_run(
        self,
        project_id: int,
        run_id: int,
        limit: int,
        candidate_status: CompanyDiscoveryCandidateStatus | None = None,
    ) -> object:
        self.calls.append(("list_candidates_for_run", project_id, run_id, limit, candidate_status))
        return self.candidates


def service(repository: RepositorySpy) -> AgentCompanySelectionService:
    return AgentCompanySelectionService(cast(AgentCompanySelectionRepository, repository))


def selection_input() -> AgentCompanySelectionInput:
    candidate = OpenAIDecisionCandidate(
        index=1,
        name="Alpha",
        website=None,
        country=None,
        city=None,
        industry=None,
        snippet=None,
        website_summary=None,
    )
    return AgentCompanySelectionInput(
        project_id=3,
        run_id=7,
        request=OpenAIDecisionRequest(goal="Choose", candidates=(candidate,)),
        bindings=(AgentCompanySelectionBinding(index=1, candidate_id=11),),
    )


def decision(kind: OpenAIDecisionKind = OpenAIDecisionKind.SELECT) -> OpenAIDecisionResult:
    selected = 1 if kind is OpenAIDecisionKind.SELECT else None
    fit = OpenAICompanyFit.HIGH if selected else OpenAICompanyFit.NOT_SUITABLE
    return OpenAIDecisionResult(
        decision=kind,
        selected_candidate_index=selected,
        confidence=0.9,
        company_fit=fit,
        rationale="Safe rationale",
        next_action_title="Review" if selected else None,
        next_action_description="Confirm" if selected else None,
        human_review_required=True,
    )


def test_package_exports_are_exact() -> None:
    assert agent_package.__all__ == [
        "AgentCompanySelectionBinding",
        "AgentCompanySelectionConsistencyError",
        "AgentCompanySelectionError",
        "AgentCompanySelectionInput",
        "AgentCompanySelectionInvalidDataError",
        "AgentCompanySelectionNoCandidatesError",
        "AgentCompanySelectionRunNotFoundError",
        "AgentCompanySelectionRunNotReadyError",
        "AgentCompanySelectionService",
    ]
    assert all(hasattr(agent_package, name) for name in agent_package.__all__)
    assert not hasattr(agent_package, "AgentCompanySelectionRepository")


def test_public_signatures_and_schema_fields_are_exact() -> None:
    assert tuple(inspect.signature(AgentCompanySelectionService).parameters) == ("repository",)
    assert tuple(inspect.signature(AgentCompanySelectionService.prepare).parameters) == (
        "self",
        "project_id",
        "run_id",
        "goal",
        "max_candidates",
    )
    assert tuple(
        inspect.signature(AgentCompanySelectionService.resolve_selected_candidate_id).parameters
    ) == ("self", "selection", "decision")
    assert tuple(AgentCompanySelectionBinding.model_fields) == ("index", "candidate_id")
    assert tuple(AgentCompanySelectionInput.model_fields) == (
        "project_id",
        "run_id",
        "request",
        "bindings",
    )


class IntSubclass(int):
    pass


class StringSubclass(str):
    pass


@pytest.mark.parametrize(
    "changes",
    [
        {"project_id": 0},
        {"project_id": True},
        {"project_id": "3"},
        {"project_id": 3.0},
        {"project_id": IntSubclass(3)},
        {"run_id": 0},
        {"run_id": False},
        {"run_id": StringSubclass("7")},
        {"goal": ""},
        {"goal": "   "},
        {"goal": StringSubclass("Goal")},
        {"goal": "x" * 1001},
        {"max_candidates": 0},
        {"max_candidates": 6},
        {"max_candidates": True},
        {"max_candidates": IntSubclass(2)},
    ],
)
def test_prepare_rejects_invalid_direct_input_before_repository_access(
    changes: dict[str, object],
) -> None:
    repository = RepositorySpy()
    values: dict[str, object] = {
        "project_id": 3,
        "run_id": 7,
        "goal": "Choose",
        "max_candidates": 5,
    }
    values.update(changes)
    with pytest.raises(AgentCompanySelectionInvalidDataError, match=f"^{INVALID}$"):
        service(repository).prepare(**values)  # type: ignore[arg-type]
    assert repository.calls == []


@pytest.mark.parametrize("model", [AgentCompanySelectionBinding, AgentCompanySelectionInput])
def test_public_schemas_are_frozen_strict_and_extra_forbid(model: type[object]) -> None:
    assert model.model_config["frozen"] is True  # type: ignore[attr-defined]
    assert model.model_config["strict"] is True  # type: ignore[attr-defined]
    assert model.model_config["extra"] == "forbid"  # type: ignore[attr-defined]


def test_binding_and_input_reject_coercion_mutability_and_extra_fields() -> None:
    with pytest.raises(ValidationError):
        AgentCompanySelectionBinding(index=True, candidate_id=1)
    with pytest.raises(ValidationError):
        AgentCompanySelectionBinding(index=1, candidate_id=IntSubclass(1))
    with pytest.raises(ValidationError):
        AgentCompanySelectionBinding(index=1, candidate_id=1, extra=True)  # type: ignore[call-arg]
    value = selection_input()
    with pytest.raises(ValidationError):
        value.project_id = 4  # type: ignore[misc]
    with pytest.raises(ValidationError):
        AgentCompanySelectionInput(
            project_id=3,
            run_id=7,
            request=value.request,
            bindings=list(value.bindings),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "run,error,message,expected_calls",
    [
        (None, AgentCompanySelectionRunNotFoundError, NOT_FOUND, [("get_run", 7)]),
        (
            RunRecord(project_id=4),
            AgentCompanySelectionRunNotFoundError,
            NOT_FOUND,
            [("get_run", 7)],
        ),
        (RunRecord(id="7"), AgentCompanySelectionConsistencyError, INCONSISTENT, [("get_run", 7)]),
        (RunRecord(id=8), AgentCompanySelectionConsistencyError, INCONSISTENT, [("get_run", 7)]),
        (
            RunRecord(run_status=CompanyDiscoveryRunStatus.NOT_FOUND),
            AgentCompanySelectionNoCandidatesError,
            NO_CANDIDATES,
            [("get_run", 7)],
        ),
        (
            RunRecord(run_status=CompanyDiscoveryRunStatus.PENDING),
            AgentCompanySelectionRunNotReadyError,
            NOT_READY,
            [("get_run", 7)],
        ),
        (
            RunRecord(run_status=CompanyDiscoveryRunStatus.FAILED),
            AgentCompanySelectionRunNotReadyError,
            NOT_READY,
            [("get_run", 7)],
        ),
        (
            RunRecord(run_status="UNKNOWN"),
            AgentCompanySelectionConsistencyError,
            INCONSISTENT,
            [("get_run", 7)],
        ),
        (
            RunRecord(run_status=StringSubclass("SUCCEEDED")),
            AgentCompanySelectionConsistencyError,
            INCONSISTENT,
            [("get_run", 7)],
        ),
    ],
)
def test_prepare_validates_run_state(
    run: object,
    error: type[AgentCompanySelectionError],
    message: str,
    expected_calls: list[tuple[object, ...]],
) -> None:
    repository = RepositorySpy(run=run)
    with pytest.raises(error, match=f"^{message}$"):
        service(repository).prepare(project_id=3, run_id=7, goal="Choose")
    assert repository.calls == expected_calls


@pytest.mark.parametrize(
    "status",
    [
        CompanyDiscoveryRunStatus.SUCCEEDED,
        CompanyDiscoveryRunStatus.PARTIAL,
        "SUCCEEDED",
        "PARTIAL",
    ],
)
def test_prepare_accepts_ready_enum_and_exact_string_status(status: object) -> None:
    repository = RepositorySpy(run=RunRecord(run_status=status))
    result = service(repository).prepare(project_id=3, run_id=7, goal=" Exact goal ")
    assert result.request.goal == " Exact goal "
    assert repository.calls == [
        ("get_run", 7),
        ("list_candidates_for_run", 3, 7, 5, CompanyDiscoveryCandidateStatus.DISCOVERED),
    ]


@pytest.mark.parametrize("candidates", ["candidate", b"candidate", bytearray(b"x"), iter(())])
def test_prepare_rejects_malformed_candidate_collection(candidates: object) -> None:
    with pytest.raises(AgentCompanySelectionConsistencyError, match=f"^{INCONSISTENT}$"):
        service(RepositorySpy(candidates=candidates)).prepare(project_id=3, run_id=7, goal="Choose")


def test_prepare_rejects_empty_and_too_many_candidates() -> None:
    with pytest.raises(AgentCompanySelectionNoCandidatesError, match=f"^{NO_CANDIDATES}$"):
        service(RepositorySpy(candidates=[])).prepare(project_id=3, run_id=7, goal="Choose")
    rows = [
        replace(CandidateRecord(), id=index, identity_key=f"key:{index}") for index in range(1, 7)
    ]
    with pytest.raises(AgentCompanySelectionConsistencyError, match=f"^{INCONSISTENT}$"):
        service(RepositorySpy(candidates=rows)).prepare(project_id=3, run_id=7, goal="Choose")


@pytest.mark.parametrize(
    "changes",
    [
        {"id": True},
        {"id": 0},
        {"project_id": 4},
        {"last_seen_run_id": 8},
        {"candidate_status": CompanyDiscoveryCandidateStatus.REVIEWED},
        {"candidate_status": StringSubclass("DISCOVERED")},
        {"promoted_company_id": 1},
        {"name": ""},
        {"name": "<b>Alpha</b>"},
        {"name": "Alpha\x00"},
        {"website": 1},
        {"website": ""},
        {"country_code": "USA"},
        {"country_code": "us"},
        {"identity_key": ""},
        {"identity_key": "key\x00"},
        {"best_position": 0},
        {"best_position": True},
    ],
)
def test_prepare_rejects_malformed_candidate(changes: dict[str, object]) -> None:
    candidate = replace(CandidateRecord(), **changes)
    with pytest.raises(AgentCompanySelectionConsistencyError, match=f"^{INCONSISTENT}$") as raised:
        service(RepositorySpy(candidates=[candidate])).prepare(
            project_id=3, run_id=7, goal="Choose"
        )
    assert raised.value.__cause__ is None and raised.value.__context__ is None
    assert "Alpha" not in str(raised.value)


def test_prepare_rejects_duplicate_candidate_ids_and_identity_keys() -> None:
    duplicate_id = [CandidateRecord(), replace(CandidateRecord(), identity_key="other")]
    duplicate_key = [CandidateRecord(), replace(CandidateRecord(), id=12)]
    for candidates in (duplicate_id, duplicate_key):
        with pytest.raises(AgentCompanySelectionConsistencyError):
            service(RepositorySpy(candidates=candidates)).prepare(
                project_id=3, run_id=7, goal="Choose"
            )


def test_prepare_sorts_maps_normalizes_and_truncates_without_leaking_ids() -> None:
    long_name = "  " + ("Alpha " * 50) + "  "
    candidates = [
        replace(CandidateRecord(), id=15, identity_key="null", best_position=None, name="Null"),
        replace(CandidateRecord(), id=14, identity_key="b", best_position=2, name=long_name),
        replace(
            CandidateRecord(),
            id=13,
            identity_key="a",
            best_position=2,
            name="  First\tName  ",
            website=None,
            country_code=None,
        ),
        replace(CandidateRecord(), id=12, identity_key="z", best_position=1, name="Top"),
    ]
    result = service(RepositorySpy(candidates=candidates)).prepare(
        project_id=3,
        run_id=7,
        goal="Choose",
        max_candidates=4,
    )
    assert [binding.candidate_id for binding in result.bindings] == [12, 13, 14, 15]
    assert [candidate.index for candidate in result.request.candidates] == [1, 2, 3, 4]
    assert result.request.candidates[1].name == "First Name"
    assert result.request.candidates[2].name.endswith("...")
    assert len(result.request.candidates[2].name) <= 200
    for candidate in result.request.candidates:
        assert candidate.city is None
        assert candidate.industry is None
        assert candidate.snippet is None
        assert candidate.website_summary is None
    serialized = result.request.model_dump_json()
    for forbidden in ("candidate_id", "project_id", "run_id", "identity_key", "DISCOVERED"):
        assert forbidden not in serialized


def test_prepare_translates_openai_candidate_validation_without_context() -> None:
    repository = RepositorySpy(candidates=[replace(CandidateRecord(), website=" " * 2)])
    with pytest.raises(AgentCompanySelectionConsistencyError) as raised:
        service(repository).prepare(project_id=3, run_id=7, goal="Choose")
    assert str(raised.value) == INCONSISTENT
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_resolve_select_and_no_selection_without_repository_access() -> None:
    repository = RepositorySpy()
    selection = selection_input()
    selection_service = service(repository)
    assert selection_service.resolve_selected_candidate_id(selection, decision()) == 11
    assert (
        selection_service.resolve_selected_candidate_id(
            selection, decision(OpenAIDecisionKind.NO_SELECTION)
        )
        is None
    )
    assert repository.calls == []


def test_resolve_rejects_invalid_types_and_malformed_exact_models() -> None:
    selection_service = service(RepositorySpy())
    with pytest.raises(AgentCompanySelectionInvalidDataError, match=f"^{INVALID}$"):
        selection_service.resolve_selected_candidate_id(
            cast(AgentCompanySelectionInput, object()), decision()
        )
    with pytest.raises(AgentCompanySelectionInvalidDataError, match=f"^{INVALID}$"):
        selection_service.resolve_selected_candidate_id(
            selection_input(), cast(OpenAIDecisionResult, object())
        )
    malformed_selection = AgentCompanySelectionInput.model_construct(
        project_id=3,
        run_id=7,
        request=selection_input().request,
        bindings=(
            AgentCompanySelectionBinding(index=1, candidate_id=11),
            AgentCompanySelectionBinding(index=1, candidate_id=12),
        ),
    )
    with pytest.raises(AgentCompanySelectionConsistencyError, match=f"^{INCONSISTENT}$"):
        selection_service.resolve_selected_candidate_id(malformed_selection, decision())
    malformed_decision = decision().model_copy(update={"selected_candidate_index": 5})
    with pytest.raises(AgentCompanySelectionConsistencyError, match=f"^{INCONSISTENT}$"):
        selection_service.resolve_selected_candidate_id(selection_input(), malformed_decision)


class CriticalFailure(BaseException):
    pass


@pytest.mark.parametrize("failure", [RuntimeError("database failed"), CriticalFailure("critical")])
def test_prepare_propagates_infrastructure_failure_by_identity(failure: BaseException) -> None:
    class FailingRepository(RepositorySpy):
        def get_run(self, run_id: int) -> object:
            raise failure

    with pytest.raises(type(failure)) as raised:
        service(FailingRepository()).prepare(project_id=3, run_id=7, goal="Choose")
    assert raised.value is failure


def test_service_has_no_output_logging_network_or_lifecycle_calls(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = service(RepositorySpy()).prepare(project_id=3, run_id=7, goal="Choose")
    assert result.bindings[0].candidate_id == 11
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_agent_source_has_no_forbidden_imports_or_client_construction() -> None:
    package_root = Path(agent_package.__file__).parent
    forbidden = {
        "OpenAIDecisionClient",
        "SerpApiClient",
        "SerpApiDiscoveryProvider",
        "SessionLocal",
        "BoundedPublicWebFetcher",
    }
    forbidden_modules = (
        "app.cli",
        "app.modules.company.service",
        "app.modules.contact",
        "app.modules.lead",
        "app.modules.task",
    )
    for path in package_root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported_names = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import | ast.ImportFrom)
            for alias in node.names
        }
        imported_modules = {
            node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        }
        assert forbidden.isdisjoint(imported_names)
        assert not any(
            module.startswith(prefix) for module in imported_modules for prefix in forbidden_modules
        )
