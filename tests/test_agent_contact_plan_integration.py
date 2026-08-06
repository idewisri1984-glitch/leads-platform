from collections.abc import Generator
from dataclasses import dataclass, replace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.cli.agent import execute_agent_contact_plan
from app.core.database.base import Base
from app.modules.agent.contact_plan import (
    AgentContactPlanDiscoveryResultError,
    AgentContactPlanPersistenceError,
    AgentContactPlanProviderError,
    AgentContactPlanSelectionConsistencyError,
    AgentContactPlanService,
)
from app.modules.agent.contact_plan_schemas import AgentContactDecision, AgentContactPlanInput
from app.modules.company.models import Company
from app.modules.contact.models import Contact
from app.modules.contact_discovery.models import (
    CompanyContactDiscoveryState,
    ContactDiscoveryCandidate,
    ContactDiscoveryCandidateStatus,
    ContactDiscoverySourceType,
    ContactDiscoveryStatus,
)
from app.modules.contact_discovery.schemas import ContactDiscoveryCandidateCreate
from app.modules.contact_discovery.service import ContactDiscoveryService
from app.modules.contact_discovery.website_provider import WebsiteContactDiscoveryProviderResult
from app.modules.lead.models import Lead
from app.modules.project.models import Project
from app.modules.task.models import Task


class FakeProvider:
    provider_name = "website"

    def __init__(self, company_id: int, fail: bool = False, failed_result: bool = False) -> None:
        self.company_id = company_id
        self.fail = fail
        self.failed_result = failed_result
        self.calls = 0

    def discover(
        self, *, company_id: int, website_url: str
    ) -> WebsiteContactDiscoveryProviderResult:
        self.calls += 1
        if self.fail:
            raise RuntimeError("controlled provider failure")
        if self.failed_result:
            return WebsiteContactDiscoveryProviderResult(
                attempted_pages=1,
                successful_pages=0,
                errors=("homepage_fetch_failed",),
            )
        return WebsiteContactDiscoveryProviderResult(
            candidates=(
                ContactDiscoveryCandidateCreate(
                    company_id=company_id,
                    name="Creative",
                    title="Creative Director",
                    email="creative@example.com",
                    source_url="https://example.com/team",
                    source_type=ContactDiscoverySourceType.TEAM_PAGE,
                    confidence=90,
                ),
                ContactDiscoveryCandidateCreate(
                    company_id=company_id,
                    name="Buyer",
                    title="Purchasing Manager",
                    email=None,
                    source_url="https://example.com/contact",
                    source_type=ContactDiscoverySourceType.CONTACT_PAGE,
                    confidence=70,
                ),
            ),
            attempted_pages=2,
            successful_pages=2,
            selected_urls=1,
        )


@dataclass
class TransactionCounters:
    commits: int = 0
    rollbacks: int = 0


class InstrumentedSession(Session):
    def __init__(
        self,
        *args: object,
        counters: TransactionCounters,
        fail_commit: bool = False,
        **kwargs: object,
    ) -> None:
        self.counters = counters
        self.fail_commit = fail_commit
        super().__init__(*args, **kwargs)

    def commit(self) -> None:
        self.counters.commits += 1
        if self.fail_commit:
            raise RuntimeError("controlled commit failure")
        super().commit()

    def rollback(self) -> None:
        self.counters.rollbacks += 1
        super().rollback()


class InstrumentedFactory:
    def __init__(self, engine: Engine, *, fail_commit: bool = False) -> None:
        self.counters = TransactionCounters()
        self.factory = sessionmaker(
            bind=engine,
            expire_on_commit=False,
            class_=InstrumentedSession,
            counters=self.counters,
            fail_commit=fail_commit,
        )

    def __call__(self) -> Session:
        return self.factory()


@pytest.fixture
def database(tmp_path) -> Generator[tuple[Engine, int, int]]:
    engine = create_engine(f"sqlite:///{(tmp_path / 'agent-contact.sqlite3').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        project = Project(name="External Test")
        session.add(project)
        session.flush()
        company = Company(project_id=project.id, name="Meyer Davis", website="https://example.com")
        session.add(company)
        session.commit()
        ids = (project.id, company.id)
    yield engine, *ids
    engine.dispose()


def test_full_plan_persists_only_staging_and_is_idempotent(database) -> None:
    engine, project_id, company_id = database
    instrumented = InstrumentedFactory(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    provider = FakeProvider(company_id)
    data = AgentContactPlanInput(
        project_id=project_id, company_id=company_id, goal="Find partnership contact"
    )
    first = execute_agent_contact_plan(
        data, session_factory=instrumented, provider_factory=lambda: provider
    )
    assert provider.calls == instrumented.counters.commits == 1
    assert instrumented.counters.rollbacks == 0
    second = execute_agent_contact_plan(
        data, session_factory=instrumented, provider_factory=lambda: provider
    )
    assert first.decision is AgentContactDecision.SELECT and first.selected_contact_name == "Buyer"
    assert second.selected_candidate_id == first.selected_candidate_id and provider.calls == 2
    assert instrumented.counters.commits == 2 and instrumented.counters.rollbacks == 0
    with factory() as session:
        rows = list(
            session.scalars(
                select(ContactDiscoveryCandidate).order_by(ContactDiscoveryCandidate.id)
            )
        )
        assert len(rows) == 2 and all(
            row.discovery_status == ContactDiscoveryCandidateStatus.DISCOVERED for row in rows
        )
        assert all(row.promoted_contact_id is None for row in rows)
        assert session.scalar(select(func.count()).select_from(CompanyContactDiscoveryState)) == 1
        assert session.scalar(select(func.count()).select_from(Contact)) == 0
        assert session.scalar(select(func.count()).select_from(Lead)) == 0
        assert session.scalar(select(func.count()).select_from(Task)) == 0


@pytest.mark.parametrize("failed_result", [False, True])
def test_provider_failure_rolls_back_external_database(database, failed_result: bool) -> None:
    engine, project_id, company_id = database
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        state = CompanyContactDiscoveryState(
            company_id=company_id,
            provider="existing",
            discovery_status=ContactDiscoveryStatus.NOT_FOUND,
            last_error=None,
        )
        session.add(state)
        session.commit()
    instrumented = InstrumentedFactory(engine)
    with pytest.raises(AgentContactPlanProviderError):
        execute_agent_contact_plan(
            AgentContactPlanInput(project_id=project_id, company_id=company_id, goal="Find"),
            session_factory=instrumented,
            provider_factory=lambda: FakeProvider(
                company_id, fail=not failed_result, failed_result=failed_result
            ),
        )
    assert instrumented.counters.commits == 0 and instrumented.counters.rollbacks == 1
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ContactDiscoveryCandidate)) == 0
        restored = session.scalar(select(CompanyContactDiscoveryState))
        assert restored is not None
        assert restored.provider == "existing"
        assert restored.discovery_status == ContactDiscoveryStatus.NOT_FOUND
        assert restored.last_error is None
        assert session.scalar(select(func.count()).select_from(Contact)) == 0
        assert session.scalar(select(func.count()).select_from(Lead)) == 0
        assert session.scalar(select(func.count()).select_from(Task)) == 0


def test_invalid_persisted_candidate_rolls_back_all_staging(
    database, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, project_id, company_id = database
    instrumented = InstrumentedFactory(engine)
    original = ContactDiscoveryService._persisted_snapshot

    def invalid_snapshot(candidate):
        return replace(original(candidate), id=True)

    monkeypatch.setattr(
        ContactDiscoveryService, "_persisted_snapshot", staticmethod(invalid_snapshot)
    )
    with pytest.raises(AgentContactPlanDiscoveryResultError):
        execute_agent_contact_plan(
            AgentContactPlanInput(project_id=project_id, company_id=company_id, goal="Find"),
            session_factory=instrumented,
            provider_factory=lambda: FakeProvider(company_id),
        )
    assert instrumented.counters.commits == 0 and instrumented.counters.rollbacks == 1
    factory = sessionmaker(bind=engine)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ContactDiscoveryCandidate)) == 0
        assert session.scalar(select(func.count()).select_from(CompanyContactDiscoveryState)) == 0


def test_selection_consistency_failure_rolls_back_all_staging(
    database, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, project_id, company_id = database
    instrumented = InstrumentedFactory(engine)

    def inconsistent(*args: object, **kwargs: object):
        raise AgentContactPlanSelectionConsistencyError("controlled")

    monkeypatch.setattr(AgentContactPlanService, "_result", staticmethod(inconsistent))
    with pytest.raises(AgentContactPlanSelectionConsistencyError):
        execute_agent_contact_plan(
            AgentContactPlanInput(project_id=project_id, company_id=company_id, goal="Find"),
            session_factory=instrumented,
            provider_factory=lambda: FakeProvider(company_id),
        )
    assert instrumented.counters.commits == 0 and instrumented.counters.rollbacks == 1
    factory = sessionmaker(bind=engine)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ContactDiscoveryCandidate)) == 0
        assert session.scalar(select(func.count()).select_from(CompanyContactDiscoveryState)) == 0


def test_commit_failure_rolls_back_all_staging(database) -> None:
    engine, project_id, company_id = database
    instrumented = InstrumentedFactory(engine, fail_commit=True)
    with pytest.raises(AgentContactPlanPersistenceError):
        execute_agent_contact_plan(
            AgentContactPlanInput(project_id=project_id, company_id=company_id, goal="Find"),
            session_factory=instrumented,
            provider_factory=lambda: FakeProvider(company_id),
        )
    assert instrumented.counters.commits == 1 and instrumented.counters.rollbacks == 1
    factory = sessionmaker(bind=engine)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ContactDiscoveryCandidate)) == 0
        assert session.scalar(select(func.count()).select_from(CompanyContactDiscoveryState)) == 0
        assert session.scalar(select(func.count()).select_from(Contact)) == 0
        assert session.scalar(select(func.count()).select_from(Lead)) == 0
        assert session.scalar(select(func.count()).select_from(Task)) == 0
