from dataclasses import dataclass
from typing import cast

import pytest
from pydantic import ValidationError

from app.modules.agent import (
    AgentCompanyPlanInput,
    AgentCompanyPlanPersistenceError,
    AgentCompanyPlanProjectNotFoundError,
    AgentCompanyPlanResult,
    AgentCompanyPlanSearchProfileNotFoundError,
    AgentCompanyPlanSearchProfileNotReadyError,
    AgentCompanyPlanService,
    AgentCompanySelectionBinding,
    AgentCompanySelectionInput,
    AgentCompanySelectionNoCandidatesError,
)
from app.modules.agent.company_plan import (
    DecisionBoundary,
    DiscoveryCommitter,
    ProjectLookup,
    QueryGenerator,
    SearchProfileLookup,
    SelectionBoundary,
    StagingOrchestrator,
)
from app.modules.company_discovery.models import CompanyDiscoveryRunStatus
from app.modules.company_discovery.provider_interfaces import DiscoveryProvider
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
class Generator:
    value: SearchQueryPreview

    def generate_preview(self, profile: object, options: object) -> SearchQueryPreview:
        return self.value


@dataclass
class Staging:
    value: CompanyDiscoveryStagingRunResult
    calls: int = 0

    def run(self, **kwargs: object) -> CompanyDiscoveryStagingRunResult:
        self.calls += 1
        return self.value


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
    project_value: object | None = object(),
    profile_value: SearchProfileRead | None = None,
    query_count: int = 1,
    status: CompanyDiscoveryRunStatus = CompanyDiscoveryRunStatus.SUCCEEDED,
    no_candidates: bool = False,
    commit_fail: bool = False,
) -> tuple[AgentCompanyPlanService, Staging, Decision, list[str]]:
    events: list[str] = []
    staging = Staging(staging_result(status))
    decision = Decision(events, select_decision())
    service = AgentCompanyPlanService(
        projects=cast(ProjectLookup, Lookup(project_value)),
        profiles=cast(SearchProfileLookup, Lookup(profile_value or profile())),
        query_generator=cast(QueryGenerator, Generator(preview(query_count))),
        staging=cast(StagingOrchestrator, staging),
        staging_provider=cast(DiscoveryProvider, Provider()),
        staging_repository=cast(CompanyDiscoveryStagingRepository, object()),
        committer=cast(DiscoveryCommitter, Committer(events, commit_fail)),
        selection=cast(SelectionBoundary, Selection(events, no_candidates)),
        decision=cast(DecisionBoundary, decision),
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


def test_project_and_profile_isolation() -> None:
    service, *_ = make_service(project_value=None)
    with pytest.raises(AgentCompanyPlanProjectNotFoundError):
        service.plan(AgentCompanyPlanInput(project_id=3, search_profile_id=7, goal="goal"))
    service, *_ = make_service(profile_value=profile(project_id=4))
    with pytest.raises(AgentCompanyPlanSearchProfileNotFoundError):
        service.plan(AgentCompanyPlanInput(project_id=3, search_profile_id=7, goal="goal"))


@pytest.mark.parametrize("query_count", [0, 2])
def test_non_single_query_is_rejected_before_staging(query_count: int) -> None:
    service, staging, *_ = make_service(query_count=query_count)
    with pytest.raises(AgentCompanyPlanSearchProfileNotReadyError):
        service.plan(AgentCompanyPlanInput(project_id=3, search_profile_id=7, goal="goal"))
    assert staging.calls == 0


def test_select_orders_commit_prepare_decide_resolve_and_binds_id() -> None:
    service, staging, decision, events = make_service()
    result = service.plan(AgentCompanyPlanInput(project_id=3, search_profile_id=7, goal="goal"))
    assert events == ["commit", "prepare", "decide", "resolve"]
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
