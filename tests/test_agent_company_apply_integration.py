from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

import app.cli.agent as agent_cli
from app.core.database.base import Base
from app.modules.agent import (
    AgentCompanyApplyConflictError,
    AgentCompanyApplyInput,
    AgentCompanyApplyInternalError,
    AgentCompanyApplyPersistenceError,
    AgentCompanyApplyResult,
    AgentCompanyApplyService,
    AgentCompanyApplyStaleHandoffError,
)
from app.modules.company.models import Company
from app.modules.company.repository import CompanyRepository
from app.modules.company_discovery import (
    CompanyDiscoveryCandidatePromotionService,
    CompanyDiscoveryCandidateReviewService,
)
from app.modules.company_discovery.models import (
    CompanyDiscoveryCandidate,
    CompanyDiscoveryCandidateStatus,
    CompanyDiscoveryRunStatus,
)
from app.modules.company_discovery.staging_repository import CompanyDiscoveryStagingRepository
from app.modules.company_discovery.staging_schemas import (
    CompanyDiscoveryCandidateCreate,
    CompanyDiscoveryRequestSnapshot,
    CompanyDiscoveryRunCreate,
)
from app.modules.contact.models import Contact
from app.modules.lead.models import Lead
from app.modules.project.models import Project
from app.modules.task.models import Task


@pytest.fixture
def database(tmp_path: Path) -> Generator[sessionmaker[Session]]:
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'agent-apply.sqlite3').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection: Any, record: Any) -> None:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def _real_fixture(
    session: Session,
    *,
    candidate_status: CompanyDiscoveryCandidateStatus = CompanyDiscoveryCandidateStatus.DISCOVERED,
    run_status: CompanyDiscoveryRunStatus = CompanyDiscoveryRunStatus.SUCCEEDED,
    website: str | None = "https://www.example.com/about",
    project_name: str = "Project",
) -> tuple[Project, Any, CompanyDiscoveryCandidate]:
    project = Project(name=project_name)
    session.add(project)
    session.flush()
    repository = CompanyDiscoveryStagingRepository(session)
    run = repository.create_run(
        CompanyDiscoveryRunCreate(
            project_id=project.id,
            search_profile_id=None,
            provider="serpapi",
            request_snapshot=CompanyDiscoveryRequestSnapshot(
                source_mode="AD_HOC",
                country_codes=["US"],
                query_count=1,
                result_limit=10,
                total_result_ceiling=10,
            ),
        )
    )
    run.run_status = run_status
    created = repository.upsert_candidate(
        project.id,
        run.id,
        CompanyDiscoveryCandidateCreate(
            project_id=project.id,
            run_id=run.id,
            provider="serpapi",
            name="Acme",
            website=website,
            country_code="US",
            position=1,
        ),
    )
    candidate = repository.get_candidate(created.candidate.id)
    assert candidate is not None
    if candidate_status is CompanyDiscoveryCandidateStatus.REVIEWED:
        CompanyDiscoveryCandidateReviewService(repository).mark_reviewed(project.id, candidate.id)
    elif candidate_status is CompanyDiscoveryCandidateStatus.REJECTED:
        CompanyDiscoveryCandidateReviewService(repository).reject(project.id, candidate.id)
    session.flush()
    return project, run, candidate


def _real_service(session: Session) -> AgentCompanyApplyService:
    staging = CompanyDiscoveryStagingRepository(session)
    companies = CompanyRepository(session)
    return AgentCompanyApplyService(
        staging_repository=cast(Any, staging),
        company_repository=cast(Any, companies),
        review_service=CompanyDiscoveryCandidateReviewService(staging),
        promotion_service=CompanyDiscoveryCandidatePromotionService(staging, companies),
    )


def _real_input(project: Project, run: Any, candidate: CompanyDiscoveryCandidate) -> Any:
    return AgentCompanyApplyInput(
        project_id=project.id,
        discovery_run_id=run.id,
        candidate_id=candidate.id,
        confirmed=True,
    )


@pytest.mark.parametrize(
    ("candidate_status", "reviewed"),
    [
        (CompanyDiscoveryCandidateStatus.DISCOVERED, True),
        (CompanyDiscoveryCandidateStatus.REVIEWED, False),
    ],
)
def test_real_apply_commits_one_atomic_promotion(
    database: sessionmaker[Session],
    candidate_status: CompanyDiscoveryCandidateStatus,
    reviewed: bool,
) -> None:
    with database() as session:
        project, run, candidate = _real_fixture(session, candidate_status=candidate_status)
        identifiers = (project.id, run.id, candidate.id)
        before = {
            "projects": session.scalar(select(func.count()).select_from(Project)),
            "contacts": session.scalar(select(func.count()).select_from(Contact)),
            "leads": session.scalar(select(func.count()).select_from(Lead)),
            "tasks": session.scalar(select(func.count()).select_from(Task)),
        }
        result = _real_service(session).apply(_real_input(project, run, candidate))
        session.commit()
    with database() as verification:
        stored = verification.get_one(CompanyDiscoveryCandidate, identifiers[2])
        assert stored.candidate_status == CompanyDiscoveryCandidateStatus.PROMOTED.value
        assert stored.promoted_company_id == result.company_id
        assert result.candidate_reviewed is reviewed
        assert result.network_call_count == 0
        assert verification.scalar(select(func.count()).select_from(Project)) == before["projects"]
        assert verification.scalar(select(func.count()).select_from(Contact)) == before["contacts"]
        assert verification.scalar(select(func.count()).select_from(Lead)) == before["leads"]
        assert verification.scalar(select(func.count()).select_from(Task)) == before["tasks"]


@pytest.mark.parametrize(
    "run_status",
    [CompanyDiscoveryRunStatus.SUCCEEDED, CompanyDiscoveryRunStatus.PARTIAL],
)
def test_real_apply_accepts_eligible_run_statuses(
    database: sessionmaker[Session], run_status: CompanyDiscoveryRunStatus
) -> None:
    with database() as session:
        project, run, candidate = _real_fixture(session, run_status=run_status)
        assert _real_service(session).apply(_real_input(project, run, candidate)).company_id > 0


def test_real_apply_rollback_restores_discovered_candidate(
    database: sessionmaker[Session],
) -> None:
    with database() as session:
        project, run, candidate = _real_fixture(session)
        identifiers = (project.id, run.id, candidate.id)
        session.commit()
        _real_service(session).apply(_real_input(project, run, candidate))
        session.rollback()
    with database() as verification:
        stored = verification.get_one(CompanyDiscoveryCandidate, identifiers[2])
        assert stored.candidate_status == CompanyDiscoveryCandidateStatus.DISCOVERED.value
        assert stored.promoted_company_id is None
        assert verification.scalar(select(func.count()).select_from(Company)) == 0


def test_real_apply_reuses_existing_hostname_and_is_idempotent(
    database: sessionmaker[Session],
) -> None:
    with database() as session:
        project, run, candidate = _real_fixture(session)
        existing = Company(
            project_id=project.id,
            name="Different Name",
            website="EXAMPLE.COM",
            country="GB",
        )
        session.add(existing)
        session.flush()
        data = _real_input(project, run, candidate)
        first = _real_service(session).apply(data)
        second = _real_service(session).apply(data)
        session.commit()
        assert first.company_id == second.company_id == existing.id
        assert first.company_reused is True and first.crm_mutated is True
        assert second.company_reused is True and second.crm_mutated is False
        assert session.scalar(select(func.count()).select_from(Company)) == 1


def test_real_apply_missing_website_creates_distinct_companies(
    database: sessionmaker[Session],
) -> None:
    with database() as session:
        first_project, first_run, first_candidate = _real_fixture(session, website=None)
        first = _real_service(session).apply(_real_input(first_project, first_run, first_candidate))
        second_project, second_run, second_candidate = _real_fixture(
            session, website=None, project_name="Second"
        )
        second = _real_service(session).apply(
            _real_input(second_project, second_run, second_candidate)
        )
        assert first.company_created is second.company_created is True
        assert first.company_id != second.company_id


def test_real_apply_rejects_stale_handoff_without_mutation(
    database: sessionmaker[Session],
) -> None:
    with database() as session:
        project, run, candidate = _real_fixture(session)
        _, newer_run, _ = _real_fixture(session, project_name="Later")
        candidate.last_seen_run_id = newer_run.id
        session.flush()
        with pytest.raises(AgentCompanyApplyStaleHandoffError, match="handoff is stale"):
            _real_service(session).apply(_real_input(project, run, candidate))
        assert session.scalar(select(func.count()).select_from(Company)) == 0


class _BarrierCompanyRepository(CompanyRepository):
    def __init__(self, session: Session, barrier: Barrier) -> None:
        super().__init__(session)
        self.barrier = barrier
        self.synchronized = False

    def acquire_promotion_scope(self, project_id: int) -> None:
        if not self.synchronized:
            self.synchronized = True
            self.barrier.wait(timeout=10)
        super().acquire_promotion_scope(project_id)


def _concurrent_apply(
    factory: sessionmaker[Session],
    barrier: Barrier,
    project_id: int,
    run_id: int,
    candidate_id: int,
) -> int | AgentCompanyApplyPersistenceError | AgentCompanyApplyConflictError:
    with factory() as session:
        staging = CompanyDiscoveryStagingRepository(session)
        companies = _BarrierCompanyRepository(session, barrier)
        service = AgentCompanyApplyService(
            staging_repository=cast(Any, staging),
            company_repository=cast(Any, companies),
            review_service=CompanyDiscoveryCandidateReviewService(staging),
            promotion_service=CompanyDiscoveryCandidatePromotionService(staging, companies),
        )
        try:
            result = service.apply(
                AgentCompanyApplyInput(
                    project_id=project_id,
                    discovery_run_id=run_id,
                    candidate_id=candidate_id,
                    confirmed=True,
                )
            )
            session.commit()
            return result.company_id
        except (AgentCompanyApplyPersistenceError, AgentCompanyApplyConflictError) as error:
            session.rollback()
            return error


def _retry_after_bounded_concurrency_outcome(
    factory: sessionmaker[Session], project_id: int, run_id: int, candidate_id: int
) -> int:
    with factory() as session:
        result = _real_service(session).apply(
            AgentCompanyApplyInput(
                project_id=project_id,
                discovery_run_id=run_id,
                candidate_id=candidate_id,
                confirmed=True,
            )
        )
        session.commit()
        return result.company_id


def test_concurrent_same_candidate_converges_on_one_company(
    database: sessionmaker[Session],
) -> None:
    with database() as setup:
        project, run, candidate = _real_fixture(setup)
        project_id, run_id, candidate_id = project.id, run.id, candidate.id
        setup.commit()
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(
                lambda _: _concurrent_apply(database, barrier, project_id, run_id, candidate_id),
                range(2),
            )
        )
    company_ids = [value for value in outcomes if isinstance(value, int)]
    for value in outcomes:
        if not isinstance(value, int):
            company_ids.append(
                _retry_after_bounded_concurrency_outcome(database, project_id, run_id, candidate_id)
            )
    with database() as verification:
        stored = verification.get_one(CompanyDiscoveryCandidate, candidate_id)
        assert len(set(company_ids)) == 1
        assert stored.candidate_status == CompanyDiscoveryCandidateStatus.PROMOTED.value
        assert stored.promoted_company_id == company_ids[0]
        assert verification.scalar(select(func.count()).select_from(Company)) == 1
        assert verification.scalar(select(func.count()).select_from(Contact)) == 0
        assert verification.scalar(select(func.count()).select_from(Lead)) == 0
        assert verification.scalar(select(func.count()).select_from(Task)) == 0


def test_concurrent_same_hostname_candidates_converge_on_one_company(
    database: sessionmaker[Session],
) -> None:
    with database() as setup:
        project, run, first = _real_fixture(setup, website="https://example.com:8443/a")
        repository = CompanyDiscoveryStagingRepository(setup)
        created = repository.upsert_candidate(
            project.id,
            run.id,
            CompanyDiscoveryCandidateCreate(
                project_id=project.id,
                run_id=run.id,
                provider="serpapi",
                name="Other Name",
                website="https://example.com:9443/b",
                country_code="US",
                position=2,
            ),
        )
        project_id, run_id = project.id, run.id
        candidate_ids = [first.id, created.candidate.id]
        setup.commit()
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(
                lambda candidate_id: _concurrent_apply(
                    database, barrier, project_id, run_id, candidate_id
                ),
                candidate_ids,
            )
        )
    company_ids: list[int] = []
    for candidate_id, value in zip(candidate_ids, outcomes, strict=True):
        company_ids.append(
            value
            if isinstance(value, int)
            else _retry_after_bounded_concurrency_outcome(
                database, project_id, run_id, candidate_id
            )
        )
    with database() as verification:
        stored = [verification.get_one(CompanyDiscoveryCandidate, value) for value in candidate_ids]
        assert len(set(company_ids)) == 1
        assert all(row.promoted_company_id == company_ids[0] for row in stored)
        assert verification.scalar(select(func.count()).select_from(Company)) == 1
        assert verification.scalar(select(func.count()).select_from(Contact)) == 0
        assert verification.scalar(select(func.count()).select_from(Lead)) == 0
        assert verification.scalar(select(func.count()).select_from(Task)) == 0


def test_postgresql_locking_sql_contains_for_update() -> None:
    dialect = postgresql.dialect()  # type: ignore[no-untyped-call]
    project_sql = str(
        select(Project.id).where(Project.id == 1).with_for_update().compile(dialect=dialect)
    )
    candidate_sql = str(
        select(CompanyDiscoveryCandidate)
        .where(
            CompanyDiscoveryCandidate.project_id == 1,
            CompanyDiscoveryCandidate.id == 2,
        )
        .with_for_update()
        .compile(dialect=dialect)
    )
    assert "FOR UPDATE" in project_sql
    assert "FOR UPDATE" in candidate_sql


def _input() -> AgentCompanyApplyInput:
    return AgentCompanyApplyInput(project_id=1, discovery_run_id=2, candidate_id=3, confirmed=True)


def _result() -> AgentCompanyApplyResult:
    return AgentCompanyApplyResult(
        project_id=1,
        discovery_run_id=2,
        candidate_id=3,
        company_id=4,
        candidate_status_before=CompanyDiscoveryCandidateStatus.REVIEWED,
        candidate_status_after=CompanyDiscoveryCandidateStatus.PROMOTED,
        company_created=True,
        company_reused=False,
        candidate_reviewed=False,
        candidate_promoted=True,
        crm_mutated=True,
        network_call_count=0,
        contact_mutation_count=0,
        lead_mutation_count=0,
        task_mutation_count=0,
        human_confirmation_required=True,
        human_confirmation_received=True,
    )


class _Session:
    def __init__(
        self,
        commit_error: BaseException | None = None,
        rollback_error: BaseException | None = None,
        close_error: BaseException | None = None,
    ) -> None:
        self.commit_error = commit_error
        self.rollback_error = rollback_error
        self.close_error = close_error
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0

    def commit(self) -> None:
        self.commits += 1
        if self.commit_error is not None:
            raise self.commit_error

    def rollback(self) -> None:
        self.rollbacks += 1
        if self.rollback_error is not None:
            raise self.rollback_error

    def close(self) -> None:
        self.closes += 1
        if self.close_error is not None:
            raise self.close_error


def _install_boundaries(monkeypatch: pytest.MonkeyPatch, result: Any) -> None:
    monkeypatch.setattr(agent_cli, "CompanyDiscoveryStagingRepository", lambda session: object())
    monkeypatch.setattr(agent_cli, "CompanyRepository", lambda session: object())
    monkeypatch.setattr(
        agent_cli, "CompanyDiscoveryCandidateReviewService", lambda repository: object()
    )
    monkeypatch.setattr(
        agent_cli,
        "CompanyDiscoveryCandidatePromotionService",
        lambda staging, companies: object(),
    )

    class Service:
        def __init__(self, **dependencies: object) -> None:
            assert set(dependencies) == {
                "staging_repository",
                "company_repository",
                "review_service",
                "promotion_service",
            }

        def apply(self, data: AgentCompanyApplyInput) -> Any:
            if isinstance(result, BaseException):
                raise result
            return result

    monkeypatch.setattr(agent_cli, "AgentCompanyApplyService", Service)


def test_executor_renders_before_exactly_one_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _Session()
    _install_boundaries(monkeypatch, _result())
    rendered = agent_cli._execute_agent_company_apply(
        _input(), "json", session_factory=cast(Any, lambda: session)
    )
    assert '"company_id":4' in rendered
    assert (session.commits, session.rollbacks, session.closes) == (1, 0, 1)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            IntegrityError("statement", {}, Exception("conflict")),
            AgentCompanyApplyConflictError,
        ),
        (RuntimeError("database secret"), AgentCompanyApplyPersistenceError),
    ],
)
def test_executor_rolls_back_commit_failures(
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
    expected: type[Exception],
) -> None:
    session = _Session(error)
    _install_boundaries(monkeypatch, _result())
    with pytest.raises(expected) as caught:
        agent_cli._execute_agent_company_apply(
            _input(), "text", session_factory=cast(Any, lambda: session)
        )
    assert "database secret" not in str(caught.value)
    assert (session.commits, session.rollbacks, session.closes) == (1, 1, 1)


def test_executor_rolls_back_service_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _Session()
    _install_boundaries(monkeypatch, AgentCompanyApplyPersistenceError("fixed"))
    with pytest.raises(AgentCompanyApplyPersistenceError, match="fixed"):
        agent_cli._execute_agent_company_apply(
            _input(), "text", session_factory=cast(Any, lambda: session)
        )
    assert (session.commits, session.rollbacks, session.closes) == (0, 1, 1)


def test_executor_preserves_base_exception_when_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = KeyboardInterrupt()

    class BrokenCleanupSession(_Session):
        def rollback(self) -> None:
            super().rollback()
            raise RuntimeError("rollback")

        def close(self) -> None:
            super().close()
            raise RuntimeError("close")

    session = BrokenCleanupSession()
    _install_boundaries(monkeypatch, sentinel)
    with pytest.raises(KeyboardInterrupt) as caught:
        agent_cli._execute_agent_company_apply(
            _input(), "text", session_factory=cast(Any, lambda: session)
        )
    assert caught.value is sentinel
    assert (session.rollbacks, session.closes) == (1, 1)


class _CleanupBaseException(BaseException):
    pass


@pytest.mark.parametrize(
    "primary",
    [
        KeyboardInterrupt(),
        SystemExit(17),
        _CleanupBaseException("primary"),
        AgentCompanyApplyPersistenceError("Agent company apply could not be persisted."),
    ],
)
@pytest.mark.parametrize(
    ("rollback_error", "close_error"),
    [
        (None, None),
        (RuntimeError("rollback"), None),
        (_CleanupBaseException("rollback"), None),
        (None, RuntimeError("close")),
        (None, _CleanupBaseException("close")),
        (_CleanupBaseException("rollback"), _CleanupBaseException("close")),
    ],
)
def test_executor_preserves_primary_identity_across_all_cleanup_failures(
    monkeypatch: pytest.MonkeyPatch,
    primary: BaseException,
    rollback_error: BaseException | None,
    close_error: BaseException | None,
) -> None:
    session = _Session(rollback_error=rollback_error, close_error=close_error)
    _install_boundaries(monkeypatch, primary)
    with pytest.raises(type(primary)) as caught:
        agent_cli._execute_agent_company_apply(
            _input(), "text", session_factory=cast(Any, lambda: session)
        )
    assert caught.value is primary
    assert primary.__cause__ is None and primary.__context__ is None
    assert (session.commits, session.rollbacks, session.closes) == (0, 1, 1)


def test_executor_does_not_commit_when_rendering_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _Session()
    _install_boundaries(monkeypatch, SimpleNamespace())
    with pytest.raises(AgentCompanyApplyInternalError):
        agent_cli._execute_agent_company_apply(
            _input(), "text", session_factory=cast(Any, lambda: session)
        )
    assert (session.commits, session.rollbacks, session.closes) == (0, 1, 1)
