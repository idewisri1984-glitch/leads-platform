from collections.abc import Generator
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from app.core.database.base import Base
from app.modules.agent import (
    AgentCompanyPlanDecisionError,
    AgentCompanyPlanInput,
    AgentCompanyPlanSelectionError,
    AgentCompanyPlanService,
    AgentCompanySelectionInput,
    AgentCompanySelectionService,
)
from app.modules.agent.company_plan import DecisionBoundary, SelectionBoundary
from app.modules.agent.company_selection import AgentCompanySelectionRepository
from app.modules.company.models import Company
from app.modules.company_discovery.models import (
    CompanyDiscoveryCandidate,
    CompanyDiscoveryRun,
    CompanyDiscoveryRunStatus,
)
from app.modules.company_discovery.schemas import (
    DiscoveryProviderResponse,
    DiscoveryProviderResult,
)
from app.modules.company_discovery.staging_orchestration import (
    CompanyDiscoveryStagingService,
)
from app.modules.company_discovery.staging_repository import (
    CompanyDiscoveryStagingRepository,
)
from app.modules.contact.models import Contact
from app.modules.lead.models import Lead
from app.modules.project import ProjectRepository
from app.modules.project.models import Project
from app.modules.search_profile import (
    SearchProfileQueryGenerator,
    SearchProfileRepository,
    SearchProfileService,
)
from app.modules.search_profile.models import SearchProfile
from app.modules.search_profile.schemas import (
    SearchProfileRead,
    SearchProfileRunOptions,
    SearchQuery,
    SearchQueryPreview,
)
from app.modules.task.models import Task
from app.providers.openai_decision import (
    OpenAICompanyFit,
    OpenAIDecisionKind,
    OpenAIDecisionRequest,
    OpenAIDecisionResult,
)


@pytest.fixture
def session(tmp_path: Path) -> Generator[Session]:
    engine = create_engine(f"sqlite:///{(tmp_path / 'stage2b.sqlite3').as_posix()}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection: object, record: object) -> None:
        cursor = connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as value:
            yield value
    finally:
        engine.dispose()


class Provider:
    def __init__(self, results: list[DiscoveryProviderResult]) -> None:
        self.results = results
        self.calls: list[SearchQuery] = []

    @property
    def provider_name(self) -> str:
        return "fake"

    def search(self, query: SearchQuery) -> DiscoveryProviderResponse:
        self.calls.append(query)
        return DiscoveryProviderResponse(
            provider="fake",
            query=query.text,
            results=self.results,
            total_results=len(self.results),
        )

    def snapshot_call_count(self) -> int:
        return len(self.calls)

    def last_query(self) -> str | None:
        return self.calls[-1].text if self.calls else None


class Committer:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.calls = 0

    def commit_discovery(self) -> None:
        self.calls += 1
        self.session.commit()


class RecordingQueryGenerator(SearchProfileQueryGenerator):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def generate_preview(
        self,
        profile: SearchProfileRead,
        options: SearchProfileRunOptions | None = None,
    ) -> SearchQueryPreview:
        self.calls += 1
        return super().generate_preview(profile, options)


class Decision:
    def __init__(self, session: Session, *, fail: bool = False) -> None:
        self.session = session
        self.fail = fail
        self.calls = 0

    def decide(self, request: OpenAIDecisionRequest) -> OpenAIDecisionResult:
        self.calls += 1
        assert self.session.scalar(select(func.count()).select_from(CompanyDiscoveryRun)) == 1
        if self.fail:
            raise RuntimeError("provider secret")
        return OpenAIDecisionResult(
            decision=OpenAIDecisionKind.SELECT,
            selected_candidate_index=1,
            confidence=0.9,
            company_fit=OpenAICompanyFit.HIGH,
            rationale="Strong fit",
            next_action_title="Review",
            next_action_description="Review before outreach",
            human_review_required=True,
        )


class TwoAttemptDecision(Decision):
    def decide(self, request: OpenAIDecisionRequest) -> OpenAIDecisionResult:
        self.calls += 1
        assert self.session.scalar(select(func.count()).select_from(CompanyDiscoveryRun)) == 1
        self.calls += 1
        return OpenAIDecisionResult(
            decision=OpenAIDecisionKind.SELECT,
            selected_candidate_index=1,
            confidence=0.9,
            company_fit=OpenAICompanyFit.HIGH,
            rationale="Strong fit after one transient provider failure",
            next_action_title="Review",
            next_action_description="Review before outreach",
            human_review_required=True,
        )


class ForeignSelection:
    def __init__(
        self,
        delegate: AgentCompanySelectionService,
        *,
        project_id: int | None,
        run_id: int | None,
    ) -> None:
        self.delegate = delegate
        self.project_id = project_id
        self.run_id = run_id
        self.revalidation_calls = 0
        self.resolver_calls = 0

    def prepare(self, **kwargs: object) -> AgentCompanySelectionInput:
        prepared = self.delegate.prepare(**kwargs)
        return AgentCompanySelectionInput.model_construct(
            project_id=(prepared.project_id if self.project_id is None else self.project_id),
            run_id=prepared.run_id if self.run_id is None else self.run_id,
            request=prepared.request,
            bindings=prepared.bindings,
        )

    def revalidate_selection_input(
        self, selection: AgentCompanySelectionInput
    ) -> AgentCompanySelectionInput:
        self.revalidation_calls += 1
        return self.delegate.revalidate_selection_input(selection)

    def resolve_selected_candidate_id(
        self,
        selection: AgentCompanySelectionInput,
        decision: OpenAIDecisionResult,
    ) -> int | None:
        self.resolver_calls += 1
        return self.delegate.resolve_selected_candidate_id(selection, decision)


class DecisionFactory:
    def __init__(self, decision: Decision) -> None:
        self.decision = decision
        self.calls = 0

    def __call__(self) -> DecisionBoundary:
        self.calls += 1
        return self.decision


def seed(session: Session) -> tuple[Project, SearchProfile]:
    project = Project(name="Stage 2B")
    session.add(project)
    session.flush()
    profile = SearchProfile(
        project_id=project.id,
        name="Buyer profile",
        product_or_service="Accounting software",
        target_customer_types=["accounting firms"],
        countries=["Germany"],
        cities=["Berlin"],
        query_templates=["{target_customer_type} {city} {country}"],
        result_limit=20,
        max_queries_per_run=10,
        total_result_ceiling=100,
        enabled=True,
    )
    session.add(profile)
    session.commit()
    return project, profile


def build_service(
    session: Session, provider: Provider, decision: Decision
) -> tuple[AgentCompanyPlanService, Committer, RecordingQueryGenerator]:
    repository = CompanyDiscoveryStagingRepository(session)
    generator = RecordingQueryGenerator()
    committer = Committer(session)
    service = AgentCompanyPlanService(
        projects=ProjectRepository(session),
        profiles=SearchProfileService(SearchProfileRepository(session)),
        staging=CompanyDiscoveryStagingService(repository=repository, query_generator=generator),
        staging_provider=provider,
        staging_repository=repository,
        provider_telemetry=provider,
        committer=committer,
        selection=AgentCompanySelectionService(cast(AgentCompanySelectionRepository, repository)),
        decision_factory=lambda: decision,
    )
    return (
        service,
        committer,
        generator,
    )


def provider_result() -> DiscoveryProviderResult:
    return DiscoveryProviderResult(
        title="Alpha Accounting",
        link="https://alpha.example",
        snippet="Accounting firm",
        source="google",
        position=1,
        provider_reference=None,
    )


def test_real_staging_selection_and_transaction_boundary(session: Session) -> None:
    project, profile = seed(session)
    provider = Provider([provider_result(), provider_result()])
    decision = Decision(session)
    service, committer, generator = build_service(session, provider, decision)

    result = service.plan(
        AgentCompanyPlanInput(
            project_id=project.id,
            search_profile_id=profile.id,
            goal="Choose the strongest company",
        )
    )

    assert result.discovery_run_status is CompanyDiscoveryRunStatus.SUCCEEDED
    assert result.selected_candidate_id == session.scalar(select(CompanyDiscoveryCandidate.id))
    assert result.staged_candidate_count == result.eligible_candidate_count == 1
    assert len(provider.calls) == committer.calls == decision.calls == 1
    assert generator.calls == result.serpapi_call_count == 1
    assert result.query == provider.calls[0].text
    assert provider.calls[0].limit == 5
    assert session.scalar(select(func.count()).select_from(CompanyDiscoveryRun)) == 1
    assert session.scalar(select(func.count()).select_from(Company)) == 0
    assert session.scalar(select(func.count()).select_from(Contact)) == 0
    assert session.scalar(select(func.count()).select_from(Lead)) == 0
    assert session.scalar(select(func.count()).select_from(Task)) == 0


def test_two_decision_attempts_do_not_repeat_discovery_or_business_persistence(
    session: Session,
) -> None:
    project, profile = seed(session)
    provider = Provider([provider_result()])
    decision = TwoAttemptDecision(session)
    service, committer, generator = build_service(session, provider, decision)

    result = service.plan(
        AgentCompanyPlanInput(
            project_id=project.id,
            search_profile_id=profile.id,
            goal="Choose the strongest company",
        )
    )

    assert result.selected_candidate_id is not None
    assert len(provider.calls) == committer.calls == generator.calls == 1
    assert result.serpapi_call_count == 1
    assert decision.calls == 2
    assert session.scalar(select(func.count()).select_from(CompanyDiscoveryRun)) == 1
    assert session.scalar(select(func.count()).select_from(CompanyDiscoveryCandidate)) == 1
    assert session.scalar(select(func.count()).select_from(Company)) == 0
    assert session.scalar(select(func.count()).select_from(Contact)) == 0
    assert session.scalar(select(func.count()).select_from(Lead)) == 0
    assert session.scalar(select(func.count()).select_from(Task)) == 0


def test_zero_results_skips_openai_and_creates_new_run(session: Session) -> None:
    project, profile = seed(session)
    provider = Provider([])
    decision = Decision(session)
    service, _, generator = build_service(session, provider, decision)
    data = AgentCompanyPlanInput(project_id=project.id, search_profile_id=profile.id, goal="Choose")

    first = service.plan(data)
    second = service.plan(data)

    assert first.decision is second.decision is None
    assert first.discovery_run_id != second.discovery_run_id
    assert len(provider.calls) == 2
    assert generator.calls == 2
    assert first.serpapi_call_count == second.serpapi_call_count == 1
    assert first.query == provider.calls[0].text
    assert second.query == provider.calls[1].text
    assert decision.calls == 0
    assert session.scalar(select(func.count()).select_from(CompanyDiscoveryRun)) == 2


def test_openai_failure_preserves_committed_discovery(session: Session) -> None:
    project, profile = seed(session)
    provider = Provider([provider_result()])
    decision = Decision(session, fail=True)
    service, _, _ = build_service(session, provider, decision)

    with pytest.raises(AgentCompanyPlanDecisionError) as caught:
        service.plan(
            AgentCompanyPlanInput(
                project_id=project.id,
                search_profile_id=profile.id,
                goal="Choose",
            )
        )
    assert str(caught.value) == "Company decision provider failed."
    session.close()
    with Session(session.bind) as verification:
        run = verification.scalar(select(CompanyDiscoveryRun))
        assert run is not None
        assert run.run_status == CompanyDiscoveryRunStatus.SUCCEEDED
        assert verification.scalar(select(func.count()).select_from(CompanyDiscoveryCandidate)) == 1


@pytest.mark.parametrize(
    ("foreign_project_id", "foreign_run_id"),
    [(991, None), (None, 992), (991, 992)],
)
def test_foreign_selection_scope_preserves_committed_discovery_and_crm_state(
    session: Session,
    foreign_project_id: int | None,
    foreign_run_id: int | None,
) -> None:
    project, profile = seed(session)
    project_snapshot = (project.id, project.name)
    profile_snapshot = (profile.id, profile.project_id, profile.name, profile.enabled)
    provider = Provider([provider_result()])
    decision = Decision(session)
    repository = CompanyDiscoveryStagingRepository(session)
    generator = RecordingQueryGenerator()
    committer = Committer(session)
    selection = ForeignSelection(
        AgentCompanySelectionService(cast(AgentCompanySelectionRepository, repository)),
        project_id=foreign_project_id,
        run_id=foreign_run_id,
    )
    factory = DecisionFactory(decision)
    service = AgentCompanyPlanService(
        projects=ProjectRepository(session),
        profiles=SearchProfileService(SearchProfileRepository(session)),
        staging=CompanyDiscoveryStagingService(
            repository=repository,
            query_generator=generator,
        ),
        staging_provider=provider,
        staging_repository=repository,
        provider_telemetry=provider,
        committer=committer,
        selection=cast(SelectionBoundary, selection),
        decision_factory=factory,
    )

    with pytest.raises(
        AgentCompanyPlanSelectionError,
        match="^Agent company selection failed\\.$",
    ) as caught:
        service.plan(
            AgentCompanyPlanInput(
                project_id=project.id,
                search_profile_id=profile.id,
                goal="Choose",
            )
        )

    assert caught.value.__cause__ is caught.value.__context__ is None
    assert len(provider.calls) == committer.calls == selection.revalidation_calls == 1
    assert factory.calls == decision.calls == selection.resolver_calls == 0

    session.close()
    with Session(session.bind) as verification:
        run = verification.scalar(select(CompanyDiscoveryRun))
        candidates = verification.scalars(select(CompanyDiscoveryCandidate)).all()
        persisted_project = verification.get(Project, project_snapshot[0])
        persisted_profile = verification.get(SearchProfile, profile_snapshot[0])
        assert run is not None
        assert run.run_status == CompanyDiscoveryRunStatus.SUCCEEDED
        assert len(candidates) == 1
        assert candidates[0].candidate_status == "DISCOVERED"
        assert candidates[0].promoted_company_id is None
        assert persisted_project is not None
        assert (persisted_project.id, persisted_project.name) == project_snapshot
        assert persisted_profile is not None
        assert (
            persisted_profile.id,
            persisted_profile.project_id,
            persisted_profile.name,
            persisted_profile.enabled,
        ) == profile_snapshot
        assert verification.scalar(select(func.count()).select_from(Company)) == 0
        assert verification.scalar(select(func.count()).select_from(Contact)) == 0
        assert verification.scalar(select(func.count()).select_from(Lead)) == 0
        assert verification.scalar(select(func.count()).select_from(Task)) == 0
