import ast
import inspect
import json
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


class _CorrectiveRun:
    id = 7
    project_id = 3
    run_status = CompanyDiscoveryRunStatus.SUCCEEDED


class _CorrectiveCandidate:
    def __init__(
        self,
        *,
        candidate_id: int = 11,
        name: str = "Safe Candidate",
        website: str | None = "https://example.test",
        identity_key: str = "safe-candidate",
        best_position: int | None = 1,
    ) -> None:
        self.id = candidate_id
        self.project_id = 3
        self.last_seen_run_id = 7
        self.name = name
        self.website = website
        self.country_code = "US"
        self.identity_key = identity_key
        self.best_position = best_position
        self.candidate_status = CompanyDiscoveryCandidateStatus.DISCOVERED
        self.promoted_company_id = None


class _CorrectiveRepository:
    def __init__(self, candidates: object, run: object | None = None) -> None:
        self.run = _CorrectiveRun() if run is None else run
        self.candidates = candidates
        self.get_calls = 0
        self.list_calls = 0

    def get_run(self, run_id: int) -> object:
        self.get_calls += 1
        return self.run

    def list_candidates_for_run(self, *args: object) -> object:
        self.list_calls += 1
        return self.candidates


def _corrective_prepare(candidates: object, *, goal: str = "Choose one") -> object:
    return AgentCompanySelectionService(_CorrectiveRepository(candidates)).prepare(
        project_id=3,
        run_id=7,
        goal=goal,
        max_candidates=5,
    )


def _corrective_select(index: object = 1) -> OpenAIDecisionResult:
    return OpenAIDecisionResult(
        decision=OpenAIDecisionKind.SELECT,
        selected_candidate_index=index,
        confidence=0.9,
        company_fit=OpenAICompanyFit.HIGH,
        rationale="Strong fit.",
        next_action_title="Contact company",
        next_action_description="Prepare a focused introduction.",
        human_review_required=True,
    )


def test_prepare_snapshots_every_stateful_candidate_property_once() -> None:
    class StatefulCandidate:
        values = {
            "id": (11, 999),
            "project_id": (3, 999),
            "last_seen_run_id": (7, 999),
            "name": ("Safe Candidate", "<POST_VALIDATION_MARKUP>"),
            "website": ("https://safe.test", object()),
            "country_code": ("US", "XX"),
            "identity_key": ("a", "z"),
            "best_position": (1, 999),
            "candidate_status": (
                CompanyDiscoveryCandidateStatus.DISCOVERED,
                CompanyDiscoveryCandidateStatus.REJECTED,
            ),
            "promoted_company_id": (None, 999),
        }

        def __init__(self) -> None:
            self.reads = dict.fromkeys(self.values, 0)

        def __getattr__(self, name: str) -> object:
            if name not in self.values:
                raise AttributeError(name)
            count = self.reads[name]
            self.reads[name] = count + 1
            return self.values[name][min(count, 1)]

    candidate = StatefulCandidate()
    selection = _corrective_prepare((candidate,))

    assert selection.bindings[0].candidate_id == 11
    assert selection.request.candidates[0].name == "Safe Candidate"
    assert "POST_VALIDATION_MARKUP" not in selection.request.candidates[0].name
    assert candidate.reads == dict.fromkeys(candidate.values, 1)
    assert (
        AgentCompanySelectionService.resolve_selected_candidate_id(
            AgentCompanySelectionService(_CorrectiveRepository(())),
            selection,
            _corrective_select(),
        )
        == 11
    )


def test_prepare_sorting_uses_stateful_candidate_snapshots() -> None:
    class StatefulSortCandidate(_CorrectiveCandidate):
        def __init__(self, candidate_id: int, identity: str, position: int) -> None:
            super().__init__(candidate_id=candidate_id)
            self._identity_values = (identity, "changed")
            self._position_values = (position, 999)
            self.identity_reads = 0
            self.position_reads = 0

        @property
        def identity_key(self) -> str:
            value = self._identity_values[min(self.identity_reads, 1)]
            self.identity_reads += 1
            return value

        @identity_key.setter
        def identity_key(self, value: str) -> None:
            pass

        @property
        def best_position(self) -> int:
            value = self._position_values[min(self.position_reads, 1)]
            self.position_reads += 1
            return value

        @best_position.setter
        def best_position(self, value: int | None) -> None:
            pass

    later = StatefulSortCandidate(12, "b", 2)
    first = StatefulSortCandidate(11, "a", 1)
    selection = _corrective_prepare((later, first))
    assert tuple(item.candidate_id for item in selection.bindings) == (11, 12)
    assert (later.identity_reads, later.position_reads) == (1, 1)
    assert (first.identity_reads, first.position_reads) == (1, 1)


@pytest.mark.parametrize("missing", ["id", "project_id", "run_status"])
def test_prepare_sanitizes_missing_run_attributes(missing: str) -> None:
    values = {
        "id": 7,
        "project_id": 3,
        "run_status": CompanyDiscoveryRunStatus.SUCCEEDED,
    }
    del values[missing]
    run = type("MalformedRun", (), values)()
    service = AgentCompanySelectionService(_CorrectiveRepository((), run))
    with pytest.raises(
        AgentCompanySelectionConsistencyError,
        match="^Agent company selection state is inconsistent\\.$",
    ) as exc_info:
        service.prepare(project_id=3, run_id=7, goal="Choose", max_candidates=5)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    assert missing not in repr(exc_info.value)


def test_prepare_preserves_custom_run_property_base_exception() -> None:
    class Stop(BaseException):
        pass

    failure = Stop()

    class Run:
        @property
        def id(self) -> object:
            raise failure

    with pytest.raises(Stop) as exc_info:
        AgentCompanySelectionService(_CorrectiveRepository((), Run())).prepare(
            project_id=3, run_id=7, goal="Choose", max_candidates=5
        )
    assert exc_info.value is failure


@pytest.mark.parametrize("candidate_id", [0, -1, True, "11", 11.0])
def test_resolution_deeply_revalidates_bypassed_binding(candidate_id: object) -> None:
    selection = _corrective_prepare((_CorrectiveCandidate(),))
    invalid = AgentCompanySelectionInput.model_construct(
        project_id=selection.project_id,
        run_id=selection.run_id,
        request=selection.request,
        bindings=(
            AgentCompanySelectionBinding.model_construct(index=1, candidate_id=candidate_id),
        ),
    )
    with pytest.raises(AgentCompanySelectionConsistencyError) as exc_info:
        AgentCompanySelectionService(_CorrectiveRepository(())).resolve_selected_candidate_id(
            invalid, _corrective_select()
        )
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None


@pytest.mark.parametrize(
    ("field", "value"),
    [("goal", " "), ("goal", "x" * 1001)],
)
def test_resolution_deeply_revalidates_bypassed_request(field: str, value: object) -> None:
    selection = _corrective_prepare((_CorrectiveCandidate(),))
    request_data = selection.request.model_dump()
    request_data[field] = value
    invalid_request = OpenAIDecisionRequest.model_construct(**request_data)
    invalid = AgentCompanySelectionInput.model_construct(
        project_id=selection.project_id,
        run_id=selection.run_id,
        request=invalid_request,
        bindings=selection.bindings,
    )
    with pytest.raises(AgentCompanySelectionConsistencyError) as exc_info:
        AgentCompanySelectionService(_CorrectiveRepository(())).resolve_selected_candidate_id(
            invalid, _corrective_select()
        )
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None


@pytest.mark.parametrize("field,value", [("name", " "), ("website", object()), ("index", 2)])
def test_resolution_deeply_revalidates_bypassed_request_candidate(
    field: str, value: object
) -> None:
    selection = _corrective_prepare((_CorrectiveCandidate(),))
    data = selection.request.candidates[0].model_dump()
    data[field] = value
    invalid_candidate = OpenAIDecisionCandidate.model_construct(**data)
    invalid_request = OpenAIDecisionRequest.model_construct(
        goal=selection.request.goal,
        candidates=(invalid_candidate,),
    )
    invalid = AgentCompanySelectionInput.model_construct(
        project_id=selection.project_id,
        run_id=selection.run_id,
        request=invalid_request,
        bindings=selection.bindings,
    )
    with pytest.raises(AgentCompanySelectionConsistencyError):
        AgentCompanySelectionService(_CorrectiveRepository(())).resolve_selected_candidate_id(
            invalid, _corrective_select()
        )


def _corrective_goal_for_serialized_size(
    target: int,
) -> tuple[str, tuple[_CorrectiveCandidate, ...]]:
    for control_count in range(600, 701):
        candidates = tuple(
            _CorrectiveCandidate(
                candidate_id=index,
                name=f"Candidate {index}",
                website="\x01" * control_count,
                identity_key=f"candidate-{index}",
                best_position=index,
            )
            for index in range(1, 6)
        )
        try:
            baseline = _corrective_prepare(candidates, goal="x")
        except AgentCompanySelectionInvalidDataError:
            continue
        size = len(
            json.dumps(
                baseline.request.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        goal_length = target - size + 1
        if 1 <= goal_length <= 1000:
            return "x" * goal_length, candidates
    raise AssertionError("Unable to construct a request-size boundary fixture.")


def test_prepare_accepts_exactly_20000_serialized_bytes() -> None:
    goal, candidates = _corrective_goal_for_serialized_size(20_000)
    selection = _corrective_prepare(candidates, goal=goal)
    serialized = json.dumps(
        selection.request.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert len(serialized.encode("utf-8")) == 20_000


def test_prepare_rejects_20001_serialized_bytes_without_payload_leak() -> None:
    goal, candidates = _corrective_goal_for_serialized_size(20_001)
    with pytest.raises(
        AgentCompanySelectionInvalidDataError,
        match="^Agent company selection data is invalid\\.$",
    ) as exc_info:
        _corrective_prepare(candidates, goal=goal)
    assert "\\u0001" not in repr(exc_info.value)


def test_prepare_rejects_schema_maximum_multibyte_request() -> None:
    candidates = tuple(
        _CorrectiveCandidate(
            candidate_id=index,
            name="\U0001f642" * 200,
            website="\x01" * 2048,
            identity_key=f"max-{index}",
            best_position=index,
        )
        for index in range(1, 6)
    )
    with pytest.raises(AgentCompanySelectionInvalidDataError):
        _corrective_prepare(candidates, goal="g" * 1000)


def test_resolution_rejects_bypassed_duplicate_bindings_without_repository_calls() -> None:
    selection = _corrective_prepare(
        (_CorrectiveCandidate(), _CorrectiveCandidate(candidate_id=12, identity_key="second"))
    )
    invalid = AgentCompanySelectionInput.model_construct(
        project_id=3,
        run_id=7,
        request=selection.request,
        bindings=(selection.bindings[0], selection.bindings[0]),
    )
    repository = _CorrectiveRepository(())
    with pytest.raises(AgentCompanySelectionConsistencyError):
        AgentCompanySelectionService(repository).resolve_selected_candidate_id(
            invalid, _corrective_select()
        )
    assert repository.get_calls == 0
    assert repository.list_calls == 0


@pytest.mark.parametrize(
    "bindings",
    [
        (AgentCompanySelectionBinding.model_construct(index=0, candidate_id=11),),
        (AgentCompanySelectionBinding.model_construct(index=2, candidate_id=11),),
        (
            AgentCompanySelectionBinding.model_construct(index=1, candidate_id=11),
            AgentCompanySelectionBinding.model_construct(index=2, candidate_id=11),
        ),
        (AgentCompanySelectionBinding.model_construct(candidate_id=11),),
    ],
)
def test_resolution_rejects_bypassed_binding_structure(
    bindings: tuple[AgentCompanySelectionBinding, ...],
) -> None:
    selection = _corrective_prepare((_CorrectiveCandidate(),))
    invalid = AgentCompanySelectionInput.model_construct(
        project_id=3,
        run_id=7,
        request=selection.request,
        bindings=bindings,
    )
    with pytest.raises(AgentCompanySelectionConsistencyError) as exc_info:
        AgentCompanySelectionService(_CorrectiveRepository(())).resolve_selected_candidate_id(
            invalid, _corrective_select()
        )
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None


def test_resolution_rejects_mutable_nested_candidate_collection() -> None:
    selection = _corrective_prepare((_CorrectiveCandidate(),))
    invalid_request = OpenAIDecisionRequest.model_construct(
        goal=selection.request.goal,
        candidates=list(selection.request.candidates),
    )
    invalid = AgentCompanySelectionInput.model_construct(
        project_id=3,
        run_id=7,
        request=invalid_request,
        bindings=selection.bindings,
    )
    with pytest.raises(AgentCompanySelectionConsistencyError):
        AgentCompanySelectionService(_CorrectiveRepository(())).resolve_selected_candidate_id(
            invalid, _corrective_select()
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"selected_candidate_index": None},
        {"confidence": 1},
        {"confidence": 1.1},
        {"company_fit": "HIGH"},
        {"next_action_title": None},
        {"human_review_required": False},
    ],
)
def test_resolution_deeply_revalidates_bypassed_decision(
    changes: dict[str, object],
) -> None:
    selection = _corrective_prepare((_CorrectiveCandidate(),))
    values = _corrective_select().model_dump()
    values.update(changes)
    invalid = OpenAIDecisionResult.model_construct(**values)
    with pytest.raises(AgentCompanySelectionConsistencyError) as exc_info:
        AgentCompanySelectionService(_CorrectiveRepository(())).resolve_selected_candidate_id(
            selection, invalid
        )
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None


def test_resolution_deep_reconstruction_accepts_exact_select_and_no_selection() -> None:
    selection = _corrective_prepare((_CorrectiveCandidate(),))
    selection_service = AgentCompanySelectionService(_CorrectiveRepository(()))
    assert selection_service.resolve_selected_candidate_id(selection, _corrective_select()) == 11
    assert (
        selection_service.resolve_selected_candidate_id(
            selection, decision(OpenAIDecisionKind.NO_SELECTION)
        )
        is None
    )


def test_prepare_preserves_candidate_property_infrastructure_exception_identity() -> None:
    failure = RuntimeError("repository property failure")

    class Candidate:
        @property
        def id(self) -> object:
            raise failure

    with pytest.raises(RuntimeError) as exc_info:
        _corrective_prepare((Candidate(),))
    assert exc_info.value is failure


def test_prepare_rejects_mapping_and_non_sequence_collection() -> None:
    for candidates in ({"candidate": _CorrectiveCandidate()}, (_ for _ in ())):
        with pytest.raises(AgentCompanySelectionConsistencyError):
            _corrective_prepare(candidates)
