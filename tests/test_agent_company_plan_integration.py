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
    AgentCompanyPlanService,
    AgentCompanySelectionService,
)
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
from app.modules.search_profile.schemas import SearchQuery
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


class Committer:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.calls = 0

    def commit_discovery(self) -> None:
        self.calls += 1
        self.session.commit()


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
) -> tuple[AgentCompanyPlanService, Committer]:
    repository = CompanyDiscoveryStagingRepository(session)
    generator = SearchProfileQueryGenerator()
    committer = Committer(session)
    return (
        AgentCompanyPlanService(
            projects=ProjectRepository(session),
            profiles=SearchProfileService(SearchProfileRepository(session)),
            query_generator=generator,
            staging=CompanyDiscoveryStagingService(
                repository=repository, query_generator=generator
            ),
            staging_provider=provider,
            staging_repository=repository,
            committer=committer,
            selection=AgentCompanySelectionService(
                cast(AgentCompanySelectionRepository, repository)
            ),
            decision=decision,
        ),
        committer,
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
    service, committer = build_service(session, provider, decision)

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
    assert provider.calls[0].limit == 5
    assert session.scalar(select(func.count()).select_from(CompanyDiscoveryRun)) == 1
    assert session.scalar(select(func.count()).select_from(Company)) == 0
    assert session.scalar(select(func.count()).select_from(Contact)) == 0
    assert session.scalar(select(func.count()).select_from(Lead)) == 0
    assert session.scalar(select(func.count()).select_from(Task)) == 0


def test_zero_results_skips_openai_and_creates_new_run(session: Session) -> None:
    project, profile = seed(session)
    provider = Provider([])
    decision = Decision(session)
    service, _ = build_service(session, provider, decision)
    data = AgentCompanyPlanInput(project_id=project.id, search_profile_id=profile.id, goal="Choose")

    first = service.plan(data)
    second = service.plan(data)

    assert first.decision is second.decision is None
    assert first.discovery_run_id != second.discovery_run_id
    assert len(provider.calls) == 2
    assert decision.calls == 0
    assert session.scalar(select(func.count()).select_from(CompanyDiscoveryRun)) == 2


def test_openai_failure_preserves_committed_discovery(session: Session) -> None:
    project, profile = seed(session)
    provider = Provider([provider_result()])
    decision = Decision(session, fail=True)
    service, _ = build_service(session, provider, decision)

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
