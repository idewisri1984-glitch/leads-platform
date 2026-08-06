from collections.abc import Generator
from dataclasses import dataclass
from math import inf, nan
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.cli.agent import execute_agent_contact_plan
from app.core.database.base import Base
from app.modules.agent.contact_plan import (
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
from app.modules.contact_discovery.repository import ContactDiscoveryRepository
from app.modules.contact_discovery.schemas import (
    ContactDiscoveryCandidateCreate,
    ContactDiscoveryCandidateUpsertResult,
)
from app.modules.contact_discovery.website_provider import (
    WebsiteContactDiscoveryProvider,
    WebsiteContactDiscoveryProviderResult,
)
from app.modules.lead.models import Lead
from app.modules.project.models import Project
from app.modules.task.models import Task
from app.providers.public_web_fetcher import PublicWebFetchResult


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
    commit_attempts: int = 0
    successful_commits: int = 0
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
        self.counters.commit_attempts += 1
        if self.fail_commit:
            raise RuntimeError("controlled commit failure")
        super().commit()
        self.counters.successful_commits += 1

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
    assert provider.calls == instrumented.counters.commit_attempts == 1
    assert instrumented.counters.successful_commits == 1
    assert instrumented.counters.rollbacks == 0
    second = execute_agent_contact_plan(
        data, session_factory=instrumented, provider_factory=lambda: provider
    )
    assert first.decision is AgentContactDecision.SELECT and first.selected_contact_name == "Buyer"
    assert second.selected_candidate_id == first.selected_candidate_id and provider.calls == 2
    assert instrumented.counters.commit_attempts == 2
    assert instrumented.counters.successful_commits == 2
    assert instrumented.counters.rollbacks == 0
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


def test_real_provider_narrative_founders_select_and_remain_idempotent(database) -> None:
    engine, project_id, company_id = database
    instrumented = InstrumentedFactory(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    about = "https://example.com/about"
    homepage_html = """
    <html><body><a href="/about">About</a><footer>
      <a href="mailto:vendors@example.com">Vendor Inquiries</a>
      <a href="mailto:proposals@example.com">Project Inquiries</a>
      <a href="mailto:info@example.com">General Inquiries</a>
    </footer></body></html>
    """
    about_html = """
    <html><body><main><h1>Profile</h1><p>
      Meyer Davis is a globally recognized architecture and design studio
      founded in 1999 by Will Meyer and Gray Davis.
    </p></main></body></html>
    """

    class MockFetcher:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def fetch(self, url: str, *, allowed_hostname: str | None = None) -> PublicWebFetchResult:
            self.calls.append(url)
            if url == "https://example.com":
                assert allowed_hostname is None
            else:
                assert allowed_hostname == "example.com"
            html = homepage_html if url == "https://example.com" else about_html
            return PublicWebFetchResult(final_url=url, text=html, content_type="text/html")

    class CountingProvider(WebsiteContactDiscoveryProvider):
        def __init__(self, fetcher: MockFetcher) -> None:
            super().__init__(fetcher=fetcher)
            self.calls = 0

        def discover(
            self, *, company_id: int, website_url: str
        ) -> WebsiteContactDiscoveryProviderResult:
            self.calls += 1
            return super().discover(company_id=company_id, website_url=website_url)

    fetcher = MockFetcher()
    provider = CountingProvider(fetcher)
    data = AgentContactPlanInput(
        project_id=project_id,
        company_id=company_id,
        goal="Find the best founder for a partnership",
    )

    first = execute_agent_contact_plan(
        data, session_factory=instrumented, provider_factory=lambda: provider
    )

    assert provider.calls == 1
    assert fetcher.calls == ["https://example.com", about]
    assert first.attempted_pages == first.successful_pages == 2
    assert first.candidate_upsert_count == 2
    assert first.staged_candidate_count == 2
    assert first.eligible_candidate_count == 2
    assert first.decision is AgentContactDecision.SELECT
    assert first.selected_contact_name == "Will Meyer"
    assert first.selected_contact_title == "Founder"
    assert first.selected_contact_email is None
    assert first.human_review_required is True
    assert (
        first.contact_mutation_count == first.lead_mutation_count == first.task_mutation_count == 0
    )
    assert instrumented.counters.commit_attempts == 1
    assert instrumented.counters.successful_commits == 1
    assert instrumented.counters.rollbacks == 0

    second = execute_agent_contact_plan(
        data, session_factory=instrumented, provider_factory=lambda: provider
    )

    assert provider.calls == 2
    assert second.selected_candidate_id == first.selected_candidate_id
    assert instrumented.counters.commit_attempts == 2
    assert instrumented.counters.successful_commits == 2
    assert instrumented.counters.rollbacks == 0
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ContactDiscoveryCandidate)) == 2
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
    assert instrumented.counters.commit_attempts == 0
    assert instrumented.counters.successful_commits == 0
    assert instrumented.counters.rollbacks == 1
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


@pytest.mark.parametrize(
    "changes",
    [
        {"id": True},
        {"id": False},
        {"company_id": True},
        {"company_id": False},
        {"confidence": True},
        {"confidence": False},
        {"confidence": 0},
        {"confidence": 1},
        {"confidence": "0"},
        {"confidence": "0.9"},
        {"confidence": "1"},
        {"confidence": nan},
        {"confidence": inf},
        {"confidence": -inf},
        {"promoted_contact_id": True},
        {"promoted_contact_id": False},
        {"promoted_contact_id": 0},
        {"promoted_contact_id": "1"},
        {"discovery_status": "UNKNOWN"},
        {"source_type": "UNKNOWN"},
        {"name": 1},
        {"name": "bad\x00name"},
        {"notes": "<raw>"},
        {"company_id": 999},
        {"id": True, "company_id": True, "confidence": True},
    ],
)
def test_invalid_raw_record_is_rejected_before_read_model(
    database, monkeypatch: pytest.MonkeyPatch, changes: dict[str, object]
) -> None:
    engine, project_id, company_id = database
    instrumented = InstrumentedFactory(engine)
    original = ContactDiscoveryRepository.upsert_candidate

    def invalid_upsert(repository, scoped_company_id, candidate):
        result = original(repository, scoped_company_id, candidate)
        raw = result.candidate.model_dump() | {"confidence": result.persisted_candidate.confidence}
        return ContactDiscoveryRepository._result(
            SimpleNamespace(**(raw | changes)),
            created=result.created,
            updated=result.updated,
            protected=result.protected,
        )

    monkeypatch.setattr(ContactDiscoveryRepository, "upsert_candidate", invalid_upsert)
    with pytest.raises(
        AgentContactPlanPersistenceError,
        match=r"^Contact discovery state could not be persisted\.$",
    ) as exc_info:
        execute_agent_contact_plan(
            AgentContactPlanInput(project_id=project_id, company_id=company_id, goal="Find"),
            session_factory=instrumented,
            provider_factory=lambda: FakeProvider(company_id),
        )
    assert all(str(value) not in str(exc_info.value) for value in changes.values())
    assert instrumented.counters.commit_attempts == 0
    assert instrumented.counters.successful_commits == 0
    assert instrumented.counters.rollbacks == 1
    factory = sessionmaker(bind=engine)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ContactDiscoveryCandidate)) == 0
        assert session.scalar(select(func.count()).select_from(CompanyContactDiscoveryState)) == 0
        assert session.scalar(select(func.count()).select_from(Contact)) == 0
        assert session.scalar(select(func.count()).select_from(Lead)) == 0
        assert session.scalar(select(func.count()).select_from(Task)) == 0


@pytest.mark.parametrize(
    ("field", "contradiction"),
    [
        ("id", 999),
        ("company_id", 999),
        ("discovery_status", ContactDiscoveryCandidateStatus.REVIEWED),
        ("source_type", ContactDiscoverySourceType.CONTACT_PAGE),
        ("confidence", 1),
        ("name", "Contradiction"),
        ("promoted_contact_id", 999),
        ("persisted_confidence", 0.89),
        ("persisted_confidence_percent", 89),
    ],
)
def test_contradictory_repository_representations_are_rejected(
    database,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    contradiction: object,
) -> None:
    engine, project_id, company_id = database
    instrumented = InstrumentedFactory(engine)
    original = ContactDiscoveryRepository.upsert_candidate

    def contradictory_upsert(repository, scoped_company_id, candidate):
        result = original(repository, scoped_company_id, candidate)
        if field == "persisted_confidence":
            contradictory_candidate = result.candidate
            persisted_candidate = result.persisted_candidate.model_copy(
                update={"confidence": contradiction}
            )
        elif field == "persisted_confidence_percent":
            contradictory_candidate = result.candidate
            persisted_candidate = result.persisted_candidate.model_copy(
                update={"confidence_percent": contradiction}
            )
        else:
            contradictory_candidate = result.candidate.model_copy(update={field: contradiction})
            persisted_candidate = result.persisted_candidate
        return ContactDiscoveryCandidateUpsertResult.model_construct(
            candidate=contradictory_candidate,
            persisted_candidate=persisted_candidate,
            created=result.created,
            updated=result.updated,
            protected=result.protected,
        )

    monkeypatch.setattr(ContactDiscoveryRepository, "upsert_candidate", contradictory_upsert)
    with pytest.raises(
        AgentContactPlanPersistenceError,
        match=r"^Contact discovery state could not be persisted\.$",
    ):
        execute_agent_contact_plan(
            AgentContactPlanInput(project_id=project_id, company_id=company_id, goal="Find"),
            session_factory=instrumented,
            provider_factory=lambda: FakeProvider(company_id),
        )

    assert instrumented.counters.commit_attempts == 0
    assert instrumented.counters.successful_commits == 0
    assert instrumented.counters.rollbacks == 1
    factory = sessionmaker(bind=engine)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ContactDiscoveryCandidate)) == 0
        assert session.scalar(select(func.count()).select_from(CompanyContactDiscoveryState)) == 0
        assert session.scalar(select(func.count()).select_from(Contact)) == 0
        assert session.scalar(select(func.count()).select_from(Lead)) == 0
        assert session.scalar(select(func.count()).select_from(Task)) == 0


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
    assert instrumented.counters.commit_attempts == 0
    assert instrumented.counters.successful_commits == 0
    assert instrumented.counters.rollbacks == 1
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
    assert instrumented.counters.commit_attempts == 1
    assert instrumented.counters.successful_commits == 0
    assert instrumented.counters.rollbacks == 1
    factory = sessionmaker(bind=engine)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ContactDiscoveryCandidate)) == 0
        assert session.scalar(select(func.count()).select_from(CompanyContactDiscoveryState)) == 0
        assert session.scalar(select(func.count()).select_from(Contact)) == 0
        assert session.scalar(select(func.count()).select_from(Lead)) == 0
        assert session.scalar(select(func.count()).select_from(Task)) == 0


def _candidate_snapshot(candidate: ContactDiscoveryCandidate) -> tuple[object, ...]:
    return (
        candidate.id,
        candidate.company_id,
        candidate.name,
        candidate.title,
        candidate.email,
        candidate.normalized_email,
        candidate.phone,
        candidate.source_url,
        candidate.source_type,
        candidate.confidence,
        candidate.discovery_status,
        candidate.deduplication_key,
        candidate.notes,
        candidate.last_error,
        candidate.promoted_contact_id,
    )


@pytest.mark.parametrize(
    "protected_status",
    [
        ContactDiscoveryCandidateStatus.REVIEWED,
        ContactDiscoveryCandidateStatus.REJECTED,
        ContactDiscoveryCandidateStatus.PROMOTED,
    ],
)
def test_protected_candidate_does_not_block_separate_current_run_selection(
    database, protected_status: ContactDiscoveryCandidateStatus
) -> None:
    engine, project_id, company_id = database
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        repository = ContactDiscoveryRepository(session)
        protected_id = repository.upsert_candidate(
            company_id,
            ContactDiscoveryCandidateCreate(
                company_id=company_id,
                name="Protected",
                title="Creative Director",
                email="creative@example.com",
                source_url="https://example.com/team",
                source_type=ContactDiscoverySourceType.TEAM_PAGE,
                confidence=60,
                notes="preserved",
            ),
        ).candidate.id
        contact = Contact(company_id=company_id, first_name="Existing")
        session.add(contact)
        session.flush()
        protected = session.get(ContactDiscoveryCandidate, protected_id)
        assert protected is not None
        protected.discovery_status = protected_status
        protected.promoted_contact_id = contact.id
        session.commit()
        before = _candidate_snapshot(protected)
        canonical_counts = (
            session.scalar(select(func.count()).select_from(Contact)),
            session.scalar(select(func.count()).select_from(Lead)),
            session.scalar(select(func.count()).select_from(Task)),
        )

    instrumented = InstrumentedFactory(engine)
    result = execute_agent_contact_plan(
        AgentContactPlanInput(project_id=project_id, company_id=company_id, goal="Find"),
        session_factory=instrumented,
        provider_factory=lambda: FakeProvider(company_id),
    )

    assert result.decision is AgentContactDecision.SELECT
    assert result.selected_contact_name == "Buyer"
    assert result.staged_candidate_count == 2
    assert result.eligible_candidate_count == 1
    assert instrumented.counters.commit_attempts == 1
    assert instrumented.counters.successful_commits == 1
    assert instrumented.counters.rollbacks == 0
    with factory() as session:
        protected = session.get(ContactDiscoveryCandidate, protected_id)
        assert protected is not None
        assert _candidate_snapshot(protected) == before
        assert (
            session.scalar(select(func.count()).select_from(Contact)),
            session.scalar(select(func.count()).select_from(Lead)),
            session.scalar(select(func.count()).select_from(Task)),
        ) == canonical_counts


@pytest.mark.parametrize(
    "failure_mode",
    ["invalid_raw", "contradiction", "selection", "commit", "failed_result"],
)
def test_failures_preserve_existing_candidate_and_state(
    database, monkeypatch: pytest.MonkeyPatch, failure_mode: str
) -> None:
    engine, project_id, company_id = database
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        repository = ContactDiscoveryRepository(session)
        candidate_id = repository.upsert_candidate(
            company_id,
            ContactDiscoveryCandidateCreate(
                company_id=company_id,
                name="Original",
                title="Creative Director",
                email="creative@example.com",
                phone="+15550100",
                source_url="https://example.com/team",
                source_type=ContactDiscoverySourceType.TEAM_PAGE,
                confidence=40,
                notes="preserved",
                last_error="existing-error",
            ),
        ).candidate.id
        existing = session.get(ContactDiscoveryCandidate, candidate_id)
        assert existing is not None
        existing.discovery_status = ContactDiscoveryCandidateStatus.REVIEWED
        state = CompanyContactDiscoveryState(
            company_id=company_id,
            provider="existing",
            discovery_status=ContactDiscoveryStatus.NOT_FOUND,
            last_error="existing-state",
        )
        session.add(state)
        session.commit()
        candidate_before = _candidate_snapshot(existing)
        state_before = (state.provider, state.discovery_status, state.last_error, state.checked_at)
        row_count = session.scalar(select(func.count()).select_from(ContactDiscoveryCandidate))
        canonical_counts = (
            session.scalar(select(func.count()).select_from(Contact)),
            session.scalar(select(func.count()).select_from(Lead)),
            session.scalar(select(func.count()).select_from(Task)),
        )

    if failure_mode == "invalid_raw":
        original_upsert = ContactDiscoveryRepository.upsert_candidate

        def invalid_upsert(repository, scoped_company_id, candidate):
            result = original_upsert(repository, scoped_company_id, candidate)
            raw = result.persisted_candidate.model_dump()
            raw["id"] = True
            return ContactDiscoveryCandidateUpsertResult.model_construct(
                candidate=result.candidate,
                persisted_candidate=SimpleNamespace(**raw),
                created=result.created,
                updated=result.updated,
                protected=result.protected,
            )

        monkeypatch.setattr(ContactDiscoveryRepository, "upsert_candidate", invalid_upsert)
    elif failure_mode == "contradiction":
        original_upsert = ContactDiscoveryRepository.upsert_candidate

        def contradictory_upsert(repository, scoped_company_id, candidate):
            result = original_upsert(repository, scoped_company_id, candidate)
            return ContactDiscoveryCandidateUpsertResult.model_construct(
                candidate=result.candidate.model_copy(update={"id": result.candidate.id + 1}),
                persisted_candidate=result.persisted_candidate,
                created=result.created,
                updated=result.updated,
                protected=result.protected,
            )

        monkeypatch.setattr(ContactDiscoveryRepository, "upsert_candidate", contradictory_upsert)
    elif failure_mode == "selection":

        def inconsistent(*args: object, **kwargs: object):
            raise AgentContactPlanSelectionConsistencyError("controlled")

        monkeypatch.setattr(AgentContactPlanService, "_result", staticmethod(inconsistent))

    instrumented = InstrumentedFactory(engine, fail_commit=failure_mode == "commit")
    provider = FakeProvider(company_id, failed_result=failure_mode == "failed_result")
    expected_error = {
        "invalid_raw": AgentContactPlanPersistenceError,
        "contradiction": AgentContactPlanPersistenceError,
        "selection": AgentContactPlanSelectionConsistencyError,
        "commit": AgentContactPlanPersistenceError,
        "failed_result": AgentContactPlanProviderError,
    }[failure_mode]
    with pytest.raises(expected_error):
        execute_agent_contact_plan(
            AgentContactPlanInput(project_id=project_id, company_id=company_id, goal="Find"),
            session_factory=instrumented,
            provider_factory=lambda: provider,
        )

    expected_attempts = int(failure_mode == "commit")
    assert instrumented.counters.commit_attempts == expected_attempts
    assert instrumented.counters.successful_commits == 0
    assert instrumented.counters.rollbacks == 1
    with factory() as session:
        existing = session.get(ContactDiscoveryCandidate, candidate_id)
        restored_state = session.scalar(select(CompanyContactDiscoveryState))
        assert existing is not None and restored_state is not None
        assert _candidate_snapshot(existing) == candidate_before
        assert (
            restored_state.provider,
            restored_state.discovery_status,
            restored_state.last_error,
            restored_state.checked_at,
        ) == state_before
        assert (
            session.scalar(select(func.count()).select_from(ContactDiscoveryCandidate)) == row_count
        )
        assert (
            session.scalar(select(func.count()).select_from(Contact)),
            session.scalar(select(func.count()).select_from(Lead)),
            session.scalar(select(func.count()).select_from(Task)),
        ) == canonical_counts
