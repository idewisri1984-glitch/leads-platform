import traceback
from dataclasses import dataclass
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic import ValidationError

from app.modules.agent import (
    AgentCompanyPlanDiscoveryDataError,
    AgentCompanyPlanInput,
    AgentCompanyPlanPersistenceError,
    AgentCompanyPlanProjectNotFoundError,
    AgentCompanyPlanResult,
    AgentCompanyPlanSearchProfileNotFoundError,
    AgentCompanyPlanSelectionError,
    AgentCompanyPlanService,
    AgentCompanySelectionBinding,
    AgentCompanySelectionInput,
    AgentCompanySelectionNoCandidatesError,
)
from app.modules.agent.company_plan import (
    AgentCompanyPlanBindingError,
    AgentCompanyPlanDecisionError,
    AgentCompanyPlanFailureSubstage,
    AgentCompanyPlanInternalError,
    AgentCompanyPlanSearchProviderError,
    AgentCompanyPlanSubstageError,
    DecisionBoundary,
    DiscoveryCommitter,
    ProjectLookup,
    ProviderTelemetry,
    SearchProfileLookup,
    SelectionBoundary,
    StagingOrchestrator,
)
from app.modules.company_discovery.models import CompanyDiscoveryRunStatus
from app.modules.company_discovery.profile_execution import (
    SearchProfileDiscoveryExecutionError,
)
from app.modules.company_discovery.provider_interfaces import DiscoveryProvider
from app.modules.company_discovery.staging_orchestration import (
    CompanyDiscoveryBoundedPlanRunResult,
    CompanyDiscoveryStagingFailureSubstage,
    CompanyDiscoveryStagingServiceError,
    CompanyDiscoveryStagingSubstageError,
)
from app.modules.company_discovery.staging_repository import CompanyDiscoveryStagingRepository
from app.modules.company_discovery.staging_service_schemas import (
    CompanyDiscoveryStagingCandidatePreview,
    CompanyDiscoveryStagingRunResult,
)
from app.modules.search_profile.schemas import SearchProfileRead, SearchQuery, SearchQueryPreview
from app.providers.openai_decision import (
    OpenAICompanyFit,
    OpenAIDecisionCandidate,
    OpenAIDecisionKind,
    OpenAIDecisionRequest,
    OpenAIDecisionResult,
)


class IntSubclass(int):
    pass


class StrSubclass(str):
    pass


_DEFAULT = object()


def profile(*, project_id: int = 3, enabled: bool = True) -> SearchProfileRead:
    return SearchProfileRead(
        id=7,
        project_id=project_id,
        name="Buyer profile",
        description=None,
        product_or_service="Accounting software",
        target_customer_types=["accounting firms"],
        target_industries=[],
        positive_keywords=[],
        negative_keywords=[],
        countries=["Germany"],
        cities=["Berlin"],
        languages=[],
        query_templates=["{target_customer_type} {city} {country}"],
        result_limit=10,
        max_queries_per_run=3,
        total_result_ceiling=25,
        enabled=enabled,
    )


def query() -> SearchQuery:
    return SearchQuery(
        text="accounting firms Berlin Germany",
        profile_id=7,
        profile_name="Buyer profile",
        country="Germany",
        city="Berlin",
        source_template="{target_customer_type} {city} {country}",
        country_code="DE",
        limit=5,
    )


def preview(count: int = 1) -> SearchQueryPreview:
    queries = [query() for _ in range(count)]
    return SearchQueryPreview(
        profile_id=7,
        profile_name="Buyer profile",
        query_count=count,
        estimated_provider_requests=count,
        result_limit_per_query=5,
        total_result_ceiling=5,
        queries=queries,
    )


def staging_result(
    status: CompanyDiscoveryRunStatus = CompanyDiscoveryRunStatus.SUCCEEDED,
) -> CompanyDiscoveryStagingRunResult:
    candidates = []
    if status is CompanyDiscoveryRunStatus.SUCCEEDED:
        candidates = [
            CompanyDiscoveryStagingCandidatePreview(
                name="Alpha",
                website="https://alpha.example",
                website_identity="alpha.example",
                country_code="DE",
                best_position=1,
                identity_key="website:alpha.example",
            )
        ]
    return CompanyDiscoveryStagingRunResult(
        project_id=3,
        search_profile_id=7,
        profile_name="Buyer profile",
        provider="fake",
        dry_run=False,
        status=status,
        request_fingerprint="a" * 64,
        query_count=1,
        executed_queries=1,
        successful_queries=1,
        provider_result_count=len(candidates),
        unique_candidate_count=len(candidates),
        candidate_upserts=len(candidates),
        candidates_created=len(candidates),
        run_id=13,
        run_persisted=True,
        candidates=candidates,
        completed_at=None,
    )


def selection_input() -> AgentCompanySelectionInput:
    return AgentCompanySelectionInput(
        project_id=3,
        run_id=13,
        request=OpenAIDecisionRequest(
            goal="Choose a company",
            candidates=(
                OpenAIDecisionCandidate(
                    index=1,
                    name="Alpha",
                    website="https://alpha.example",
                    country="DE",
                    city=None,
                    industry=None,
                    snippet=None,
                    website_summary=None,
                ),
            ),
        ),
        bindings=(AgentCompanySelectionBinding(index=1, candidate_id=41),),
    )


def select_decision() -> OpenAIDecisionResult:
    return OpenAIDecisionResult(
        decision=OpenAIDecisionKind.SELECT,
        selected_candidate_index=1,
        confidence=0.9,
        company_fit=OpenAICompanyFit.HIGH,
        rationale="Strong match",
        next_action_title="Review",
        next_action_description="Review before outreach",
        human_review_required=True,
    )


@dataclass
class Lookup:
    value: object | None

    def get(self, unused_id: int) -> object | None:
        return self.value


@dataclass
class Staging:
    value: CompanyDiscoveryStagingRunResult
    telemetry: "Telemetry"
    query_count: int = 1
    calls: int = 0
    provider_calls: int = 1
    bounded_query: str = query().text
    telemetry_query: str = query().text

    def run_bounded_plan(self, **kwargs: object) -> CompanyDiscoveryBoundedPlanRunResult:
        self.calls += 1
        if self.query_count != 1:
            raise CompanyDiscoveryStagingServiceError("invalid query count")
        for _ in range(self.provider_calls):
            self.telemetry.record(self.telemetry_query)
        return CompanyDiscoveryBoundedPlanRunResult(
            staging_result=self.value,
            query=self.bounded_query,
        )


@dataclass
class Telemetry:
    calls: int = 0
    query_text: str | None = None

    def record(self, query_text: str) -> None:
        self.calls += 1
        self.query_text = query_text

    def snapshot_call_count(self) -> int:
        return self.calls

    def last_query(self) -> str | None:
        return self.query_text


@dataclass
class Committer:
    events: list[str]
    fail: bool = False

    def commit_discovery(self) -> None:
        self.events.append("commit")
        if self.fail:
            raise RuntimeError("secret SQL")


@dataclass
class Selection:
    events: list[str]
    no_candidates: bool = False

    def prepare(self, **kwargs: object) -> AgentCompanySelectionInput:
        self.events.append("prepare")
        if self.no_candidates:
            raise AgentCompanySelectionNoCandidatesError("ignored")
        return selection_input()

    def resolve_selected_candidate_id(
        self, selection: AgentCompanySelectionInput, decision: OpenAIDecisionResult
    ) -> int | None:
        self.events.append("resolve")
        if decision.decision is OpenAIDecisionKind.NO_SELECTION:
            return None
        return selection.bindings[0].candidate_id

    def revalidate_selection_input(
        self, selection: AgentCompanySelectionInput
    ) -> AgentCompanySelectionInput:
        self.events.append("revalidate")
        return AgentCompanySelectionInput(
            project_id=selection.project_id,
            run_id=selection.run_id,
            request=OpenAIDecisionRequest(
                goal=selection.request.goal,
                candidates=tuple(
                    OpenAIDecisionCandidate(
                        index=candidate.index,
                        name=candidate.name,
                        website=candidate.website,
                        country=candidate.country,
                        city=candidate.city,
                        industry=candidate.industry,
                        snippet=candidate.snippet,
                        website_summary=candidate.website_summary,
                    )
                    for candidate in selection.request.candidates
                ),
            ),
            bindings=tuple(
                AgentCompanySelectionBinding(
                    index=binding.index,
                    candidate_id=binding.candidate_id,
                )
                for binding in selection.bindings
            ),
        )


@dataclass
class ControlledSelection(Selection):
    project_id: object = 3
    run_id: object = 13

    def prepare(self, **kwargs: object) -> AgentCompanySelectionInput:
        prepared = super().prepare(**kwargs)
        request = prepared.request.model_copy(update={"goal": "sensitive-selection-sentinel"})
        return AgentCompanySelectionInput.model_construct(
            project_id=self.project_id,
            run_id=self.run_id,
            request=request,
            bindings=prepared.bindings,
        )


@dataclass
class Decision:
    events: list[str]
    value: OpenAIDecisionResult
    calls: int = 0

    def decide(self, request: OpenAIDecisionRequest) -> OpenAIDecisionResult:
        self.events.append("decide")
        self.calls += 1
        return self.value


class Provider:
    @property
    def provider_name(self) -> str:
        return "fake"

    def search(self, query: SearchQuery) -> object:
        raise AssertionError("staging fake owns the provider boundary")


def make_service(
    *,
    project_value: object | None = _DEFAULT,
    profile_value: object = _DEFAULT,
    query_count: int = 1,
    status: CompanyDiscoveryRunStatus = CompanyDiscoveryRunStatus.SUCCEEDED,
    no_candidates: bool = False,
    commit_fail: bool = False,
) -> tuple[AgentCompanyPlanService, Staging, Decision, list[str]]:
    events: list[str] = []
    telemetry = Telemetry()
    staging = Staging(staging_result(status), telemetry, query_count)
    decision = Decision(events, select_decision())
    actual_project = SimpleNamespace(id=3) if project_value is _DEFAULT else project_value
    actual_profile = profile() if profile_value is _DEFAULT else profile_value
    service = AgentCompanyPlanService(
        projects=cast(ProjectLookup, Lookup(actual_project)),
        profiles=cast(SearchProfileLookup, Lookup(actual_profile)),
        staging=cast(StagingOrchestrator, staging),
        staging_provider=cast(DiscoveryProvider, Provider()),
        staging_repository=cast(CompanyDiscoveryStagingRepository, object()),
        provider_telemetry=cast(ProviderTelemetry, telemetry),
        committer=cast(DiscoveryCommitter, Committer(events, commit_fail)),
        selection=cast(
            SelectionBoundary,
            Selection(
                events,
                no_candidates or status is CompanyDiscoveryRunStatus.NOT_FOUND,
            ),
        ),
        decision_factory=lambda: events.append("factory") or cast(DecisionBoundary, decision),
    )
    return service, staging, decision, events


@pytest.mark.parametrize("bad_id", [True, "1", 1.0, 0, -1, IntSubclass(1)])
def test_input_rejects_non_exact_positive_ids(bad_id: object) -> None:
    with pytest.raises(ValidationError):
        AgentCompanyPlanInput(project_id=bad_id, search_profile_id=1, goal="goal")


@pytest.mark.parametrize("bad_goal", [None, 1, "", "  ", "x" * 1001, StrSubclass("goal")])
def test_input_rejects_invalid_goal(bad_goal: object) -> None:
    with pytest.raises(ValidationError):
        AgentCompanyPlanInput(project_id=1, search_profile_id=1, goal=bad_goal)


def test_input_is_frozen_and_forbids_extra_without_trimming() -> None:
    data = AgentCompanyPlanInput(project_id=1, search_profile_id=2, goal="  goal  ")
    assert data.goal == "  goal  "
    with pytest.raises(ValidationError):
        AgentCompanyPlanInput(project_id=1, search_profile_id=2, goal="goal", extra=1)
    with pytest.raises(ValidationError):
        data.goal = "changed"


def test_input_and_result_reject_unpaired_surrogates_but_accept_astral_unicode() -> None:
    valid = AgentCompanyPlanInput(
        project_id=1,
        search_profile_id=2,
        goal="Выбрать компанию 🙂",
    )
    assert valid.goal.endswith("🙂")
    with pytest.raises(ValidationError):
        AgentCompanyPlanInput(project_id=1, search_profile_id=2, goal="bad\ud800")

    values = (
        make_service()[0]
        .plan(AgentCompanyPlanInput(project_id=3, search_profile_id=7, goal="goal"))
        .model_dump()
    )
    values["rationale"] = "bad\udc00"
    with pytest.raises(ValidationError):
        AgentCompanyPlanResult(**values)


def test_project_and_profile_isolation() -> None:
    service, *_ = make_service(project_value=None)
    with pytest.raises(AgentCompanyPlanProjectNotFoundError):
        service.plan(AgentCompanyPlanInput(project_id=3, search_profile_id=7, goal="goal"))
    service, *_ = make_service(profile_value=profile(project_id=4))
    with pytest.raises(AgentCompanyPlanSearchProfileNotFoundError):
        service.plan(AgentCompanyPlanInput(project_id=3, search_profile_id=7, goal="goal"))


def test_project_and_profile_are_snapshotted_with_one_raw_read_per_field() -> None:
    class OneReadProject:
        reads = 0

        @property
        def id(self) -> int:
            self.reads += 1
            return 3 if self.reads == 1 else 999

    class OneReadProfile:
        def __init__(self) -> None:
            self.values = profile().model_dump()
            self.reads: dict[str, int] = {}

        def __getattr__(self, name: str) -> object:
            if name not in self.values:
                raise AttributeError(name)
            self.reads[name] = self.reads.get(name, 0) + 1
            if self.reads[name] > 1 and name in {"project_id", "name"}:
                return 999 if name == "project_id" else "changed profile"
            return self.values[name]

    raw_project = OneReadProject()
    raw_profile = OneReadProfile()
    service, *_ = make_service(
        project_value=raw_project,
        profile_value=raw_profile,
        no_candidates=True,
    )

    result = service.plan(AgentCompanyPlanInput(project_id=3, search_profile_id=7, goal="goal"))

    assert result.project_id == 3
    assert raw_project.reads == 1
    assert raw_profile.reads == dict.fromkeys(SearchProfileRead.model_fields, 1)


@pytest.mark.parametrize("query_count", [0, 2])
def test_non_single_query_is_rejected_before_staging(query_count: int) -> None:
    service, staging, *_ = make_service(query_count=query_count)
    with pytest.raises(AgentCompanyPlanDiscoveryDataError):
        service.plan(AgentCompanyPlanInput(project_id=3, search_profile_id=7, goal="goal"))
    assert staging.calls == 1
    assert staging.telemetry.calls == 0


@pytest.mark.parametrize(
    ("staging_substage", "expected"),
    [
        (
            CompanyDiscoveryStagingFailureSubstage.QUERY_GENERATION,
            AgentCompanyPlanFailureSubstage.QUERY_GENERATION,
        ),
        (
            CompanyDiscoveryStagingFailureSubstage.DISCOVERY_REQUEST_BUILD,
            AgentCompanyPlanFailureSubstage.DISCOVERY_REQUEST_BUILD,
        ),
        (
            CompanyDiscoveryStagingFailureSubstage.RESULT_NORMALIZATION,
            AgentCompanyPlanFailureSubstage.RESULT_NORMALIZATION,
        ),
        (
            CompanyDiscoveryStagingFailureSubstage.DISCOVERY_PERSISTENCE,
            AgentCompanyPlanFailureSubstage.DISCOVERY_PERSISTENCE,
        ),
    ],
)
def test_staging_substage_is_preserved_without_raw_error(
    staging_substage: CompanyDiscoveryStagingFailureSubstage,
    expected: AgentCompanyPlanFailureSubstage,
) -> None:
    service, staging, decision, _ = make_service()

    def fail(**_kwargs):
        raise CompanyDiscoveryStagingSubstageError(staging_substage)

    staging.run_bounded_plan = fail
    with pytest.raises(AgentCompanyPlanSubstageError) as captured:
        service.plan(AgentCompanyPlanInput(project_id=3, search_profile_id=7, goal="goal"))

    assert captured.value.substage is expected
    assert "SECRET" not in repr(captured.value.args)
    assert captured.value.__cause__ is captured.value.__context__ is None
    assert decision.calls == 0


@pytest.mark.parametrize(
    ("error_code", "expected"),
    [
        ("execution_failed", AgentCompanyPlanFailureSubstage.PROVIDER_EXECUTION),
        (
            "execution_invalid",
            AgentCompanyPlanFailureSubstage.PROVIDER_RESULT_VALIDATION,
        ),
        ("candidate_invalid", AgentCompanyPlanFailureSubstage.RESULT_NORMALIZATION),
    ],
)
def test_failed_staging_result_has_exact_substage(
    error_code: str,
    expected: AgentCompanyPlanFailureSubstage,
) -> None:
    service, staging, decision, _ = make_service()
    original = staging_result()
    values = {
        field: getattr(original, field) for field in CompanyDiscoveryStagingRunResult.model_fields
    }
    values.update(status=CompanyDiscoveryRunStatus.FAILED, error_code=error_code)
    staging.value = CompanyDiscoveryStagingRunResult.model_construct(**values)

    with pytest.raises(AgentCompanyPlanSubstageError) as captured:
        service.plan(AgentCompanyPlanInput(project_id=3, search_profile_id=7, goal="goal"))

    assert captured.value.substage is expected
    assert captured.value.__cause__ is captured.value.__context__ is None
    assert decision.calls == 0


def test_typed_discovery_execution_error_remains_provider_error() -> None:
    service, staging, decision, _ = make_service()

    def fail(**_kwargs):
        raise SearchProfileDiscoveryExecutionError("SECRET_PROVIDER_VALUE")

    staging.run_bounded_plan = fail
    with pytest.raises(AgentCompanyPlanSearchProviderError) as captured:
        service.plan(AgentCompanyPlanInput(project_id=3, search_profile_id=7, goal="goal"))

    assert captured.value.substage is None
    assert "SECRET_PROVIDER_VALUE" not in repr(captured.value)
    assert captured.value.__cause__ is captured.value.__context__ is None
    assert decision.calls == 0


def test_select_orders_commit_prepare_decide_resolve_and_binds_id() -> None:
    service, staging, decision, events = make_service()
    result = service.plan(AgentCompanyPlanInput(project_id=3, search_profile_id=7, goal="goal"))
    assert events == ["commit", "prepare", "revalidate", "factory", "decide", "resolve"]
    assert staging.calls == decision.calls == 1
    assert result.selected_candidate_id == 41
    assert result.serpapi_call_count == result.openai_call_count == 1
    assert result.crm_mutated is result.candidate_promoted is False


@pytest.mark.parametrize(
    "status,no_candidates",
    [(CompanyDiscoveryRunStatus.NOT_FOUND, False), (CompanyDiscoveryRunStatus.SUCCEEDED, True)],
)
def test_no_candidates_returns_success_without_openai(
    status: CompanyDiscoveryRunStatus, no_candidates: bool
) -> None:
    service, _, decision, _ = make_service(status=status, no_candidates=no_candidates)
    result = service.plan(AgentCompanyPlanInput(project_id=3, search_profile_id=7, goal="goal"))
    assert decision.calls == 0
    assert result.openai_call_count == 0
    assert result.decision is None


def test_commit_failure_is_sanitized_and_prevents_openai() -> None:
    service, _, decision, _ = make_service(commit_fail=True)
    with pytest.raises(AgentCompanyPlanPersistenceError) as caught:
        service.plan(AgentCompanyPlanInput(project_id=3, search_profile_id=7, goal="goal"))
    assert str(caught.value) == "Company discovery state could not be persisted."
    assert caught.value.__cause__ is caught.value.__context__ is None
    assert decision.calls == 0


def test_result_deep_invariants_and_immutability() -> None:
    service, *_ = make_service()
    result = service.plan(AgentCompanyPlanInput(project_id=3, search_profile_id=7, goal="goal"))
    with pytest.raises(ValidationError):
        result.openai_call_count = 0
    invalid = result.model_dump()
    invalid["selected_candidate_id"] = None
    with pytest.raises(ValidationError):
        AgentCompanyPlanResult.model_validate(invalid)
    service, *_ = make_service(profile_value=None)
    with pytest.raises(AgentCompanyPlanSearchProfileNotFoundError):
        service.plan(AgentCompanyPlanInput(project_id=3, search_profile_id=7, goal="goal"))


@pytest.mark.parametrize(
    "updates",
    [
        {"project_id": 4},
        {"search_profile_id": 8},
        {"run_id": "13"},
    ],
)
def test_foreign_or_malformed_staging_is_rejected_before_commit(
    updates: dict[str, object],
) -> None:
    service, staging, decision, events = make_service()
    original = staging_result()
    values = {
        field: getattr(original, field) for field in CompanyDiscoveryStagingRunResult.model_fields
    }
    values.update(updates)
    staging.value = CompanyDiscoveryStagingRunResult.model_construct(**values)

    with pytest.raises(AgentCompanyPlanDiscoveryDataError) as caught:
        service.plan(AgentCompanyPlanInput(project_id=3, search_profile_id=7, goal="goal"))

    assert str(caught.value) == "Company discovery results are invalid."
    assert caught.value.__cause__ is caught.value.__context__ is None
    assert events == []
    assert decision.calls == 0


@pytest.mark.parametrize(
    ("provider_calls", "bounded_query", "telemetry_query"),
    [
        (0, query().text, query().text),
        (2, query().text, query().text),
        (1, "different query", query().text),
        (1, query().text, "different query"),
    ],
)
def test_provider_telemetry_mismatch_is_rejected_before_commit(
    provider_calls: int,
    bounded_query: str,
    telemetry_query: str,
) -> None:
    service, staging, decision, events = make_service()
    staging.provider_calls = provider_calls
    staging.bounded_query = bounded_query
    staging.telemetry_query = telemetry_query

    with pytest.raises(AgentCompanyPlanDiscoveryDataError):
        service.plan(AgentCompanyPlanInput(project_id=3, search_profile_id=7, goal="goal"))

    assert events == []
    assert decision.calls == 0


def test_repeated_plans_measure_provider_delta_per_execution() -> None:
    service, staging, decision, _ = make_service(no_candidates=True)
    data = AgentCompanyPlanInput(project_id=3, search_profile_id=7, goal="goal")

    first = service.plan(data)
    second = service.plan(data)

    assert first.serpapi_call_count == second.serpapi_call_count == 1
    assert staging.telemetry.calls == staging.calls == 2
    assert decision.calls == 0


def test_malformed_selection_prevents_decision_factory_and_call() -> None:
    service, _, decision, events = make_service()
    factory_calls = 0

    class MalformedSelection(Selection):
        def revalidate_selection_input(
            self, selection: AgentCompanySelectionInput
        ) -> AgentCompanySelectionInput:
            self.events.append("revalidate")
            raise ValueError("sensitive malformed selection")

    def factory() -> DecisionBoundary:
        nonlocal factory_calls
        factory_calls += 1
        return decision

    service.selection = cast(SelectionBoundary, MalformedSelection(events))
    service.decision_factory = factory

    with pytest.raises(AgentCompanyPlanSelectionError) as caught:
        service.plan(AgentCompanyPlanInput(project_id=3, search_profile_id=7, goal="goal"))

    assert str(caught.value) == "Agent company selection failed."
    assert caught.value.__cause__ is caught.value.__context__ is None
    assert events == ["commit", "prepare", "revalidate"]
    assert factory_calls == decision.calls == 0


@pytest.mark.parametrize(
    ("foreign_project_id", "foreign_run_id"),
    [(991, 13), (3, 992), (991, 992)],
)
def test_foreign_deeply_valid_selection_is_rejected_before_decision_and_binding(
    foreign_project_id: int,
    foreign_run_id: int,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    service, _, decision, events = make_service()
    factory_calls = 0

    def factory() -> DecisionBoundary:
        nonlocal factory_calls
        factory_calls += 1
        return decision

    service.selection = cast(
        SelectionBoundary,
        ControlledSelection(events, project_id=foreign_project_id, run_id=foreign_run_id),
    )
    service.decision_factory = factory

    with pytest.raises(AgentCompanyPlanSelectionError) as caught:
        service.plan(AgentCompanyPlanInput(project_id=3, search_profile_id=7, goal="goal"))

    rendered = "".join(traceback.format_exception(caught.value))
    captured = capsys.readouterr()
    assert str(caught.value) == "Agent company selection failed."
    assert caught.value.__cause__ is caught.value.__context__ is None
    assert events == ["commit", "prepare", "revalidate"]
    assert factory_calls == decision.calls == 0
    assert captured.out == captured.err == ""
    assert caplog.records == []
    for forbidden in ("991", "992", "sensitive-selection-sentinel"):
        assert forbidden not in str(caught.value)
        assert forbidden not in repr(caught.value)
        assert forbidden not in rendered


@pytest.mark.parametrize(
    ("project_id", "run_id"),
    [(0, 13), (True, 13), ("3", 13), (3, 0), (3, True), (3, "13")],
)
def test_malformed_selection_scope_is_rejected_by_deep_revalidation(
    project_id: object,
    run_id: object,
) -> None:
    service, _, decision, events = make_service()
    factory_calls = 0

    def factory() -> DecisionBoundary:
        nonlocal factory_calls
        factory_calls += 1
        return decision

    service.selection = cast(
        SelectionBoundary,
        ControlledSelection(events, project_id=project_id, run_id=run_id),
    )
    service.decision_factory = factory

    with pytest.raises(AgentCompanyPlanSelectionError, match="^Agent company selection failed\\.$"):
        service.plan(AgentCompanyPlanInput(project_id=3, search_profile_id=7, goal="goal"))

    assert events == ["commit", "prepare", "revalidate"]
    assert factory_calls == decision.calls == 0


def test_valid_matching_no_selection_constructs_decision_and_resolves_once() -> None:
    service, _, decision, events = make_service()
    decision.value = OpenAIDecisionResult(
        decision=OpenAIDecisionKind.NO_SELECTION,
        selected_candidate_index=None,
        confidence=0.9,
        company_fit=OpenAICompanyFit.NOT_SUITABLE,
        rationale="No suitable company",
        next_action_title=None,
        next_action_description=None,
        human_review_required=True,
    )

    result = service.plan(AgentCompanyPlanInput(project_id=3, search_profile_id=7, goal="goal"))

    assert events == ["commit", "prepare", "revalidate", "factory", "decide", "resolve"]
    assert decision.calls == result.openai_call_count == 1
    assert result.selected_candidate_id is result.selected_candidate_index is None


def test_identical_acquisition_request_is_suppressed_before_second_decision_call() -> None:
    service, *_ = make_service()
    first = service.plan(AgentCompanyPlanInput(project_id=3, search_profile_id=7, goal="goal"))
    assert first.decision_request_fingerprint is not None

    repeated = service.plan(
        AgentCompanyPlanInput(
            project_id=3,
            search_profile_id=7,
            goal="goal",
            excluded_decision_fingerprints=(first.decision_request_fingerprint,),
        )
    )

    assert repeated.decision_request_fingerprint == first.decision_request_fingerprint
    assert repeated.decision_suppressed is True
    assert repeated.openai_call_count == 0
    assert repeated.decision is None


def _raise_secret_value_error(*_args, **_kwargs):
    raise ValueError("SECRET_COMPANY_PLAN_VALUE api_key=abc123")


@pytest.mark.parametrize(
    ("boundary", "expected", "error_type"),
    [
        (
            "decision",
            AgentCompanyPlanFailureSubstage.COMPANY_DECISION,
            AgentCompanyPlanSubstageError,
        ),
        (
            "decision_result",
            AgentCompanyPlanFailureSubstage.DECISION_RESULT_VALIDATION,
            AgentCompanyPlanSubstageError,
        ),
        (
            "candidate_binding",
            AgentCompanyPlanFailureSubstage.CANDIDATE_BINDING,
            AgentCompanyPlanBindingError,
        ),
        (
            "plan_result",
            AgentCompanyPlanFailureSubstage.COMPANY_PLAN_RESULT_BUILD,
            AgentCompanyPlanBindingError,
        ),
    ],
)
def test_runtime_value_error_has_exact_safe_plan_substage(
    boundary: str,
    expected: AgentCompanyPlanFailureSubstage,
    error_type: type[AgentCompanyPlanSubstageError],
) -> None:
    service, _, decision, _ = make_service()
    if boundary == "decision":
        decision.decide = _raise_secret_value_error
    elif boundary == "decision_result":
        decision.value = SimpleNamespace(model_dump=_raise_secret_value_error)
    elif boundary == "candidate_binding":
        service.selection.resolve_selected_candidate_id = _raise_secret_value_error
    else:
        service._decision_result = _raise_secret_value_error

    with pytest.raises(error_type) as captured:
        service.plan(AgentCompanyPlanInput(project_id=3, search_profile_id=7, goal="goal"))

    error = captured.value
    assert error.substage is expected
    assert "SECRET_COMPANY_PLAN_VALUE" not in str(error)
    assert "SECRET_COMPANY_PLAN_VALUE" not in repr(error)
    assert "SECRET_COMPANY_PLAN_VALUE" not in repr(error.args)
    assert error.__cause__ is error.__context__ is None


def test_typed_decision_error_remains_distinct_from_generic_substage() -> None:
    service, _, decision, _ = make_service()

    def fail(*_args, **_kwargs):
        raise AgentCompanyPlanDecisionError("Company decision provider failed.")

    decision.decide = fail
    with pytest.raises(AgentCompanyPlanDecisionError) as captured:
        service.plan(AgentCompanyPlanInput(project_id=3, search_profile_id=7, goal="goal"))

    assert not isinstance(captured.value, AgentCompanyPlanSubstageError)
    assert captured.value.args == ("Company decision provider failed.",)
    assert captured.value.__cause__ is captured.value.__context__ is None


def test_unknown_company_plan_value_error_uses_finite_fallback() -> None:
    service, staging, decision, _ = make_service()
    staging.run_bounded_plan = _raise_secret_value_error

    with pytest.raises(AgentCompanyPlanInternalError) as captured:
        service.plan(AgentCompanyPlanInput(project_id=3, search_profile_id=7, goal="goal"))

    assert captured.value.substage is AgentCompanyPlanFailureSubstage.UNKNOWN_COMPANY_PLAN
    assert "SECRET_COMPANY_PLAN_VALUE" not in repr(captured.value)
    assert captured.value.__cause__ is captured.value.__context__ is None
    assert decision.calls == 0
